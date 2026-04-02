from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import Session, select

from core.settings import get_settings
from dependencies.auth import require_active_identity
from persistence import PlatformAuditLog, SkillAsyncJob, get_engine
from schemas.backlog_governance import BacklogEnvelope, BacklogStartRequest
from services.backlog_mcp_adapter import BacklogMcpAdapter, BacklogMcpAdapterError

router = APIRouter(prefix="/backlog", tags=["backlog-governance"])
_limiter = Limiter(key_func=get_remote_address)


def _principal_id(principal: Any) -> str:
    return str(getattr(principal, "id", "unknown"))


def _hash_payload(obj: Any) -> str:
    try:
        payload = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        payload = str(obj)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _job_status_value(status: str) -> str:
    return status if status in {"queued", "running", "succeeded", "failed", "canceled"} else "failed"


def _create_job(job_id: str, actor_id: str, trace_id: Optional[str], args: Dict[str, Any]) -> None:
    with Session(get_engine()) as session:
        rec = SkillAsyncJob(
            job_id=job_id,
            skill_name="backlog.start",
            actor_id=actor_id,
            status="queued",
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
        rec.status = "running"
        rec.started_at = datetime.utcnow()
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec


def _set_job_result(job_id: str, status: str, result: Dict[str, Any]) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        rec = session.get(SkillAsyncJob, job_id)
        if rec is None:
            return None
        rec.status = status
        rec.result = result
        rec.result_hash = _hash_payload(result)
        rec.finished_at = datetime.utcnow()
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec


def _get_job(job_id: str) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        stmt = select(SkillAsyncJob).where(SkillAsyncJob.job_id == job_id, SkillAsyncJob.skill_name == "backlog.start")
        return session.exec(stmt).first()


def _write_audit(event_type: str, actor_id: str, details: Dict[str, Any], trace_id: Optional[str]) -> None:
    try:
        with Session(get_engine()) as session:
            log = PlatformAuditLog(
                event_type=event_type,
                actor_type="agent",
                actor_id=actor_id,
                target_type="skill",
                target_id="backlog.start",
                details={**details, **({"trace_id": trace_id} if trace_id else {})},
            )
            session.add(log)
            session.commit()
    except Exception:
        pass


def _ensure_governance_enabled() -> None:
    mode = get_settings().normalized_governance_mode
    if mode == "off":
        raise HTTPException(status_code=403, detail="Backlog governance endpoints are disabled when APP_GOVERNANCE_MODE=off")


async def _run_backlog_and_store(
    job_id: str,
    actor_id: str,
    trace_id: Optional[str],
    repo_name: str,
    args: Dict[str, Any],
) -> None:
    _set_job_running(job_id)
    adapter = BacklogMcpAdapter()

    try:
        result_data = await adapter.start(repo_name=repo_name, payload=args)
        envelope = BacklogEnvelope(
            ok=True,
            message="ok",
            data=result_data,
            job={"id": job_id, "status": "succeeded"},
            meta={"trace_id": trace_id} if trace_id else None,
        ).model_dump()
        _set_job_result(job_id, "succeeded", envelope)
        _write_audit(
            event_type="backlog_completed",
            actor_id=actor_id,
            details={"repo_name": repo_name, "job_id": job_id, "result_hash": _hash_payload(envelope)},
            trace_id=trace_id,
        )
    except BacklogMcpAdapterError as exc:
        envelope = BacklogEnvelope(
            ok=False,
            message="backlog execution failed",
            error={"code": "backlog_execution_error", "reason": str(exc), "retriable": False},
            job={"id": job_id, "status": "failed"},
            meta={"trace_id": trace_id} if trace_id else None,
        ).model_dump()
        _set_job_result(job_id, "failed", envelope)
        _write_audit(
            event_type="backlog_failed",
            actor_id=actor_id,
            details={"repo_name": repo_name, "job_id": job_id, "result_hash": _hash_payload(envelope)},
            trace_id=trace_id,
        )


@router.get("/health")
@_limiter.limit("120/minute")
async def backlog_health(request: Request):
    _ensure_governance_enabled()

    adapter = BacklogMcpAdapter()
    return {
        "ok": True,
        "configured": adapter.is_configured(),
        "governance_mode": get_settings().normalized_governance_mode,
    }


@router.post("/start")
@_limiter.limit("60/minute")
async def start_backlog(
    req: BacklogStartRequest,
    bg: BackgroundTasks,
    request: Request,
    principal: Any = Depends(require_active_identity),
):
    _ensure_governance_enabled()

    mode = (req.mode or "sync").lower()
    if mode not in {"sync", "async"}:
        raise HTTPException(status_code=400, detail="mode must be 'sync' or 'async'")

    actor_id = _principal_id(principal)
    trace_id = getattr(getattr(request, "state", object()), "trace_id", None)

    payload_args = req.args or {}
    audit_args = {"repo_name": req.repo_name, **payload_args}

    if mode == "sync":
        adapter = BacklogMcpAdapter()
        try:
            result_data = await adapter.start(repo_name=req.repo_name, payload=payload_args)
        except BacklogMcpAdapterError as exc:
            env = BacklogEnvelope(
                ok=False,
                message="backlog execution failed",
                error={"code": "backlog_execution_error", "reason": str(exc), "retriable": False},
                meta={"trace_id": trace_id} if trace_id else None,
            ).model_dump()
            _write_audit(
                event_type="backlog_failed",
                actor_id=actor_id,
                details={
                    "repo_name": req.repo_name,
                    "args_hash": _hash_payload(audit_args),
                    "result_hash": _hash_payload(env),
                },
                trace_id=trace_id,
            )
            return env

        env = BacklogEnvelope(
            ok=True,
            message="ok",
            data=result_data,
            job={"id": str(uuid4()), "status": "succeeded"},
            meta={"trace_id": trace_id} if trace_id else None,
        ).model_dump()
        _write_audit(
            event_type="backlog_completed",
            actor_id=actor_id,
            details={
                "repo_name": req.repo_name,
                "args_hash": _hash_payload(audit_args),
                "result_hash": _hash_payload(env),
            },
            trace_id=trace_id,
        )
        return env

    job_id = str(uuid4())
    _create_job(job_id=job_id, actor_id=actor_id, trace_id=trace_id, args=audit_args)

    _write_audit(
        event_type="backlog_queued",
        actor_id=actor_id,
        details={"repo_name": req.repo_name, "args_hash": _hash_payload(audit_args), "job_id": job_id},
        trace_id=trace_id,
    )

    bg.add_task(_run_backlog_and_store, job_id, actor_id, trace_id, req.repo_name, payload_args)

    return BacklogEnvelope(
        ok=True,
        message="job queued",
        job={"id": job_id, "status": "queued"},
        next_suggested_action=f"GET /api/v1/backlog/jobs/{job_id}",
        description=req.description or "Backlog MCP started in async mode; poll jobs endpoint",
        meta={"trace_id": trace_id} if trace_id else None,
    ).model_dump()


@router.get("/jobs/{job_id}")
@_limiter.limit("120/minute")
async def get_backlog_job(
    request: Request,
    job_id: str,
    principal: Any = Depends(require_active_identity),
):
    _ensure_governance_enabled()

    rec = _get_job(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="job not found")

    actor_id = _principal_id(principal)
    if rec.actor_id and rec.actor_id != actor_id:
        raise HTTPException(status_code=403, detail="forbidden")

    if rec.status in {"succeeded", "failed"} and rec.result is not None:
        return rec.result

    return BacklogEnvelope(
        ok=True,
        message="job in progress" if rec.status != "queued" else "job queued",
        job={"id": job_id, "status": _job_status_value(rec.status)},
        description="Polling backlog job status",
    ).model_dump()
