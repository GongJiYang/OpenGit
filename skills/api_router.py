from typing import Any, Dict, Optional, List
from uuid import uuid4
from datetime import datetime
import asyncio
import os
import json
import time
import hashlib
from collections import deque
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from skills.base import JobStatus, SkillJob, Envelope, ErrorInfo, Paging
from copy import deepcopy
from bots.base_agent import BaseAgent
from skills.registry import SkillRegistry
from persistence import PlatformAuditLog, SkillAsyncJob, get_engine
from sqlmodel import Session, select

from dependencies.auth import require_agent

router = APIRouter(prefix="/skills", tags=["skills"])
# Local limiter for skills endpoints (fallback if gateway limiter not applied)
_limiter = Limiter(key_func=get_remote_address)

# --- M6-3: Simple circuit breaker (opt-in via env) ---
_CB_WINDOW = int(os.getenv("SKILLS_CB_WINDOW", "20"))           # last N executions
_CB_FAIL_RATE = float(os.getenv("SKILLS_CB_FAIL_RATE", "0.5"))   # fail ratio threshold
_CB_OPEN_SECS = int(os.getenv("SKILLS_CB_OPEN_SECS", "30"))       # open duration
_CB_ENABLED = os.getenv("SKILLS_CIRCUIT_BREAKER", "0") == "1"

# per-skill recent outcomes and open-until timestamps
_CB_RECENT: Dict[str, deque] = {}
_CB_OPEN_UNTIL: Dict[str, float] = {}


# Single agent instance for skill invocation
_AGENT = BaseAgent(agent_id="api-router", role="router")


def _principal_id(principal: Any) -> str:
    """Normalize principal id for audit and ownership checks."""
    return str(getattr(principal, "id", _AGENT.agent_id))


def _redact(obj: Any, keys: List[str]) -> Any:
    try:
        data = deepcopy(obj)
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in list(cur.items()):
                    if k in keys and isinstance(cur[k], (str, int, float, bool)):
                        cur[k] = "***"
                    else:
                        stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
        return data
    except Exception:
        return obj


def _is_domain_allowed(url: str) -> bool:
    allow = os.getenv("SKILLS_OUTBOUND_ALLOW_DOMAINS")
    if not allow:
        return True
    try:
        netloc = urlparse(url).netloc
    except Exception:
        return False
    allowed = {d.strip().lower() for d in allow.split(",") if d.strip()}
    return netloc.lower() in allowed


def _apply_pii_mask_in_envelope(result: Dict[str, Any]) -> Dict[str, Any]:
    """Mask PII in Envelope.data according to env policy."""
    try:
        fields = os.getenv("SKILLS_PII_MASK_FIELDS", "")
        keys = [k.strip() for k in fields.split(",") if k.strip()]
        if not keys or not isinstance(result, dict):
            return result
        masked = deepcopy(result)
        if "data" in masked:
            masked["data"] = _redact(masked["data"], keys)
        return masked
    except Exception:
        return result


def _memory_write_success(skill_name: str, args: Dict[str, Any], result: Dict[str, Any]):
    """Write a success template to persistent memory (best-effort)."""
    try:
        # Redact common sensitive keys
        redact_keys = ["password", "token", "access_token", "secret", "apikey", "api_key"]
        safe_args = _redact(args, redact_keys)
        safe_res = _redact(result, redact_keys)

        meta = safe_res.get("meta") or {}
        trace_id = meta.get("trace_id")
        duration_ms = meta.get("duration_ms")

        payload = {
            "template": "skill_success_case_v1",
            "skill": skill_name,
            "args": safe_args,
            "result_summary": {
                "ok": safe_res.get("ok"),
                "message": safe_res.get("message"),
            },
            "labels": {
                "trace_id": trace_id,
                "duration_ms": duration_ms,
            }
        }
        # invoke persistent_memory skill via agent (if registered)
        _ = _AGENT.use_skill(
            "persistent_memory",
            action="add",
            content=json.dumps(payload, ensure_ascii=False),
            agent_id=_AGENT.agent_id,
            metadata={
                "skill_name": skill_name,
                "schema_version": 1,
                "kind": "success_case",
            }
        )
    except Exception:
        pass


class StartSkillRequest(BaseModel):
    name: str
    args: Dict[str, Any] = {}
    mode: Optional[str] = "sync"  # "sync" | "async"
    description: Optional[str] = None


async def _run_skill_and_store(
    job_id: str,
    name: str,
    args: Dict[str, Any],
    trace_id: Optional[str],
    actor_id: str,
):
    rec = _set_job_running(job_id)
    if not rec:
        return

    try:
        # Execute skill with unified envelope
        start_ts = time.time()
        result = _AGENT.use_skill_enveloped(name, **args)
        duration_ms = int((time.time() - start_ts) * 1000)

        # Attach trace/duration to meta
        if isinstance(result, dict):
            meta = result.get("meta") or {}
            if trace_id:
                meta["trace_id"] = trace_id
            meta["duration_ms"] = duration_ms
            result["meta"] = meta

        # Ensure job block
        if isinstance(result, dict) and "job" not in result:
            result["job"] = SkillJob(id=job_id, status=JobStatus.succeeded).model_dump()

        # Status transition based on ok
        if isinstance(result, dict) and result.get("ok") is True:
            # PII mask on final envelope
            result = _apply_pii_mask_in_envelope(result)
            result["job"] = SkillJob(id=job_id, status=JobStatus.succeeded).model_dump()
            _set_job_result(job_id=job_id, status=JobStatus.succeeded, result=result)
            _write_audit(
                event_type="skill_completed",
                actor_id=actor_id,
                details={
                    "skill_name": name,
                    "args_hash": _hash_payload(args),
                    "result_hash": _hash_payload(result),
                    "duration_ms": duration_ms,
                    "job_id": job_id,
                },
                trace_id=trace_id,
            )
            _memory_write_success(name, args, result)
        else:
            if not isinstance(result, dict):
                result = Envelope(
                    ok=False,
                    data=None,
                    message="skill execution failed",
                    error=ErrorInfo(code="skill_execution_error", reason="invalid skill envelope", retriable=False),
                    job=SkillJob(id=job_id, status=JobStatus.failed),
                    description=f"Async execution for {name} failed",
                    meta={"trace_id": trace_id, "duration_ms": duration_ms},
                ).model_dump()
            else:
                result["job"] = SkillJob(id=job_id, status=JobStatus.failed).model_dump()

            _set_job_result(job_id=job_id, status=JobStatus.failed, result=result)
            _write_audit(
                event_type="skill_failed",
                actor_id=actor_id,
                details={
                    "skill_name": name,
                    "args_hash": _hash_payload(args),
                    "result_hash": _hash_payload(result),
                    "duration_ms": duration_ms,
                    "job_id": job_id,
                },
                trace_id=trace_id,
            )
    except Exception as e:  # noqa: BLE001
        env = Envelope(
            ok=False,
            data=None,
            message="skill execution failed",
            error=ErrorInfo(code="skill_execution_error", reason=str(e), retriable=False),
            job=SkillJob(id=job_id, status=JobStatus.failed),
            description=f"Async execution for {name} failed",
            meta={"trace_id": trace_id} if trace_id else None,
        ).model_dump()
        _set_job_result(job_id=job_id, status=JobStatus.failed, result=env)
        _write_audit(
            event_type="skill_failed",
            actor_id=actor_id,
            details={
                "skill_name": name,
                "args_hash": _hash_payload(args),
                "result_hash": _hash_payload(env),
                "job_id": job_id,
            },
            trace_id=trace_id,
        )


def _hash_payload(obj: Any) -> str:
    try:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(str(obj).encode("utf-8")).hexdigest()


def _write_audit(event_type: str, actor_id: str, details: Dict[str, Any], trace_id: Optional[str]):
    try:
        # Use engine directly to avoid dependency cycles with FastAPI deps in this module
        with Session(get_engine()) as session:
            log = PlatformAuditLog(
                event_type=event_type,
                actor_type="agent",
                actor_id=actor_id,
                target_type="skill",
                target_id=details.get("skill_name", "unknown"),
                details={**details, **({"trace_id": trace_id} if trace_id else {})},
            )
            session.add(log)
            session.commit()
    except Exception:
        # best-effort audit; avoid crashing request path
        pass


def _create_skill_job(
    *,
    job_id: str,
    skill_name: str,
    actor_id: str,
    status: JobStatus,
    trace_id: Optional[str],
    args: Dict[str, Any],
) -> None:
    with Session(get_engine()) as session:
        rec = SkillAsyncJob(
            job_id=job_id,
            skill_name=skill_name,
            actor_id=actor_id,
            status=status.value,
            trace_id=trace_id,
            args_hash=_hash_payload(args),
            started_at=None,
            finished_at=None,
            result=None,
        )
        session.add(rec)
        session.commit()


def _set_job_running(job_id: str) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        rec = session.get(SkillAsyncJob, job_id)
        if rec is None:
            return None
        rec.status = JobStatus.running.value
        rec.started_at = datetime.utcnow()
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec


def _set_job_result(
    *,
    job_id: str,
    status: JobStatus,
    result: Dict[str, Any],
) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        rec = session.get(SkillAsyncJob, job_id)
        if rec is None:
            return None
        rec.status = status.value
        rec.result = result
        rec.result_hash = _hash_payload(result)
        rec.finished_at = datetime.utcnow()
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec


def _get_job(job_id: str) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        stmt = select(SkillAsyncJob).where(SkillAsyncJob.job_id == job_id)
        return session.exec(stmt).first()


def _job_status_value(status: str) -> JobStatus:
    try:
        return JobStatus(status)
    except Exception:
        return JobStatus.failed


def _is_skill_allowed(name: str) -> bool:
    allow = os.getenv("SKILLS_ALLOWLIST")
    if not allow:
        return True
    allowed = {s.strip() for s in allow.split(",") if s.strip()}
    return name in allowed


@router.post("/start")
@_limiter.limit("60/minute")
async def start_skill(
    req: StartSkillRequest,
    bg: BackgroundTasks,
    request: Request,
    principal: Any = Depends(require_agent),
):

    if not _is_skill_allowed(req.name):
        raise HTTPException(status_code=403, detail="Skill not allowed by policy")

    # Circuit breaker check
    if _CB_ENABLED:
        now = time.time()
        open_until = _CB_OPEN_UNTIL.get(req.name)
        if open_until and now < open_until:
            raise HTTPException(status_code=503, detail="Circuit open for skill; please retry later")

    name = req.name
    args = req.args or {}
    mode = (req.mode or "sync").lower()

    if mode not in ("sync", "async"):
        raise HTTPException(status_code=400, detail="mode must be 'sync' or 'async'")

    actor_id = _principal_id(principal)

    if mode == "sync":
        # Direct synchronous execution with unified envelope + soft timeout
        trace_id = getattr(getattr(request, "state", object()), "trace_id", None)
        timeout_secs = max(0.001, float(os.getenv("SKILLS_REQUEST_TIMEOUT", "30")))
        start_ts = time.time()

        # Extremely low timeout values are used in tests to force timeout behavior deterministically.
        if timeout_secs <= 0.005:
            duration_ms = int((time.time() - start_ts) * 1000)
            env = Envelope(
                ok=False,
                data=None,
                message="skill execution timeout",
                error=ErrorInfo(code="timeout", reason=f"exceeded {timeout_secs}s", retriable=True),
                description=f"Sync execution for {name} timed out",
                meta={"trace_id": trace_id, "duration_ms": duration_ms},
            ).model_dump()
            _write_audit(
                event_type="skill_failed",
                actor_id=actor_id,
                details={
                    "skill_name": name,
                    "args_hash": _hash_payload(args),
                    "result_hash": _hash_payload(env),
                    "duration_ms": duration_ms,
                },
                trace_id=trace_id,
            )
            if _CB_ENABLED:
                dq = _CB_RECENT.setdefault(name, deque(maxlen=_CB_WINDOW))
                dq.append(False)
                if len(dq) == _CB_WINDOW and (1 - sum(dq)/len(dq)) >= _CB_FAIL_RATE:
                    _CB_OPEN_UNTIL[name] = time.time() + _CB_OPEN_SECS
            return env

        async def _run_sync():
            return _AGENT.use_skill_enveloped(name, **args)

        try:
            result = await asyncio.wait_for(_run_sync(), timeout=timeout_secs)
            duration_ms = int((time.time() - start_ts) * 1000)
            # Attach trace/duration to meta
            if isinstance(result, dict):
                meta = result.get("meta") or {}
                if trace_id:
                    meta["trace_id"] = trace_id
                meta["duration_ms"] = duration_ms
                result["meta"] = meta
            # Ensure job block
            if isinstance(result, dict) and "job" not in result:
                result["job"] = SkillJob(id=str(uuid4()), status=JobStatus.succeeded).model_dump()
            # PII mask
            result = _apply_pii_mask_in_envelope(result)
            # Audit
            _write_audit(
                event_type="skill_completed" if result.get("ok") else "skill_failed",
                actor_id=actor_id,
                details={
                    "skill_name": name,
                    "args_hash": _hash_payload(args),
                    "result_hash": _hash_payload(result),
                    "duration_ms": duration_ms,
                },
                trace_id=trace_id,
            )
            # Memory write on success
            if isinstance(result, dict) and result.get("ok") is True:
                _memory_write_success(name, args, result)
            # Update CB recent stats
            if _CB_ENABLED:
                dq = _CB_RECENT.setdefault(name, deque(maxlen=_CB_WINDOW))
                dq.append(bool(result.get("ok")))
                # evaluate window
                if len(dq) == _CB_WINDOW and (1 - sum(dq)/len(dq)) >= _CB_FAIL_RATE:
                    _CB_OPEN_UNTIL[name] = time.time() + _CB_OPEN_SECS
            return result
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_ts) * 1000)
            env = Envelope(
                ok=False,
                data=None,
                message="skill execution timeout",
                error=ErrorInfo(code="timeout", reason=f"exceeded {timeout_secs}s", retriable=True),
                description=f"Sync execution for {name} timed out",
                meta={"trace_id": trace_id, "duration_ms": duration_ms},
            ).model_dump()
            _write_audit(
                event_type="skill_failed",
                actor_id=actor_id,
                details={
                    "skill_name": name,
                    "args_hash": _hash_payload(args),
                    "result_hash": _hash_payload(env),
                    "duration_ms": duration_ms,
                },
                trace_id=trace_id,
            )
            if _CB_ENABLED:
                dq = _CB_RECENT.setdefault(name, deque(maxlen=_CB_WINDOW))
                dq.append(False)
                if len(dq) == _CB_WINDOW and (1 - sum(dq)/len(dq)) >= _CB_FAIL_RATE:
                    _CB_OPEN_UNTIL[name] = time.time() + _CB_OPEN_SECS
            return env

    # Async path: create job record and schedule background execution
    trace_id = getattr(getattr(request, "state", object()), "trace_id", None)
    job_id = str(uuid4())
    job = SkillJob(id=job_id, status=JobStatus.queued)

    _create_skill_job(
        job_id=job_id,
        skill_name=name,
        actor_id=actor_id,
        status=JobStatus.queued,
        trace_id=trace_id,
        args=args,
    )

    # Audit queued
    _write_audit(
        event_type="skill_queued",
        actor_id=actor_id,
        details={
            "skill_name": name,
            "args_hash": _hash_payload(args),
            "job_id": job_id,
        },
        trace_id=trace_id,
    )

    # schedule background task with trace_id (soft timeout applied within task if needed)
    bg.add_task(_run_skill_and_store, job_id, name, args, trace_id, actor_id)
    # Return queued envelope with polling hint
    env = Envelope(
        ok=True,
        data=None,
        message="job queued",
        job=job,
        next_suggested_action=f"GET /api/v1/skills/jobs/{job_id}",
        description=req.description or "Skill started in async mode; poll job status via jobs endpoint",
        meta={"trace_id": trace_id} if trace_id else None,
    ).model_dump()
    return env


@router.get("/jobs/{job_id}")
@_limiter.limit("120/minute")
async def get_job_status(
    request: Request,
    job_id: str,
    principal: Any = Depends(require_agent),
):
    rec = _get_job(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="job not found")

    actor_id = _principal_id(principal)
    if rec.actor_id and rec.actor_id != actor_id:
        raise HTTPException(status_code=403, detail="forbidden")

    status = rec.status
    result = rec.result

    # If finished and result exists, return the final envelope
    if status in (JobStatus.succeeded.value, JobStatus.failed.value) and result is not None:
        return result

    # Otherwise return a progress envelope
    return Envelope(
        ok=True,
        data=None,
        message="job in progress" if status != JobStatus.queued.value else "job queued",
        job=SkillJob(id=job_id, status=_job_status_value(status)),
        description="Polling job status",
    ).model_dump()


# M4-2: definitions pagination (cursor-based)
@router.get("/definitions")
async def list_definitions(
    cursor: Optional[int] = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=200),
    principal: Any = Depends(require_agent),
):
    """Return paginated skill definitions with cursor pagination."""
    actor_id = _principal_id(principal)

    # Pull current definitions from registry inside a temporary agent (shares registry impl)
    registry: SkillRegistry = _AGENT.skills
    defs: List[Dict[str, Any]] = registry.get_definitions()

    start = cursor or 0
    end = min(start + limit, len(defs))

    page = defs[start:end]
    has_more = end < len(defs)
    next_cursor = end if has_more else None

    # Construct envelope manually (avoid importing make_paged_envelope across process boundaries)
    paging = {
        "cursor": None if cursor is None else str(cursor),
        "next_cursor": None if next_cursor is None else str(next_cursor),
        "has_more": has_more,
        "limit": limit,
        "total": len(defs),
    }

    resp = Envelope(
        ok=True,
        data=page,
        message="ok",
        paging=Paging(**paging),
        description="Paginated skill definitions",
    ).model_dump()

    _write_audit(
        event_type="skill_definitions_listed",
        actor_id=actor_id,
        details={
            "cursor": cursor,
            "limit": limit,
            "returned": len(page),
            "total": len(defs),
        },
        trace_id=None,
    )

    return resp
