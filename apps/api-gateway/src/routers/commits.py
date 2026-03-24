import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime, timezone
from typing import Any, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from agenthub_execution_vmm.guard import ExecutionGuard
from agenthub_protocol.path_utils import ensure_safe_path
from agenthub_protocol.schemas import TRACE_COMMIT_PROTOCOL_VERSION
from agenthub_protocol.signing import (
    compute_binding_hash,
    compute_diff_hash_from_patch,
    compute_reasoning_hash,
    get_trace_signing_secret,
    sign_trace_commit,
)
from agenthub_protocol.validator import TraceValidator
from core.middleware import limiter
from core.security import STORE_ROOT, ensure_safe_ref, get_secure_repo_path
from core.settings import get_settings
from dependencies.auth import require_active_identity, require_agent
from git_tree_service import GitTreeService
from persistence import Bounty, CommitRecord, get_session
from schemas.commits import BlackboxReport, CommitRequest, CommitResponse, VerificationRequest
from agent_auth.models.runner import ComputeJob, ComputeJobStatus, ExecutionMode, RepoExecutionConfig

router = APIRouter()
logger = logging.getLogger(__name__)


class DailyBudgetTracker:
    """Simple JSON-based daily budget tracker."""

    def __init__(self, limit: float = 10.0):
        self.limit = limit
        self.path = os.path.abspath("./agenthub_data/daily_budget.json")
        self.lock_path = os.path.abspath("./agenthub_data/daily_budget.lock")
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({"date": str(date.today()), "spent": 0.0}, f)

    def check_and_record(self, amount: float) -> bool:
        lockf = None
        try:
            today_str = str(date.today())
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            lockf = open(self.lock_path, "w")
            try:
                import fcntl

                fcntl.flock(lockf, fcntl.LOCK_EX)
            except Exception:
                pass

            with open(self.path, "r") as f:
                data = json.load(f)

            if data.get("date") != today_str:
                data = {"date": today_str, "spent": 0.0}

            data["spent"] += amount
            if data["spent"] > self.limit:
                return False

            with open(self.path, "w") as f:
                json.dump(data, f)
            return True
        except Exception:
            # Fail-closed: if budget file ops fail, deny execution to preserve cost guard
            return False
        finally:
            if lockf:
                try:
                    import fcntl

                    fcntl.flock(lockf, fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    lockf.close()
                except Exception:
                    pass


budget_tracker = DailyBudgetTracker(limit=10.0)


def _resolve_execution_mode(
    session: Session,
    bounty: Bounty,
    sandbox_provider: str,
) -> Tuple[ExecutionMode, str, Optional[UUID]]:
    """Resolve execution mode with explicit precedence and transparency source."""
    selected_execution_mode = ExecutionMode.SHARED_LOCAL
    source = "default"
    repo_uuid: Optional[UUID] = None

    if bounty.repo_id:
        try:
            repo_uuid = UUID(str(bounty.repo_id))
        except (TypeError, ValueError):
            repo_uuid = None
            logger.warning("[automation] invalid bounty.repo_id for execution config lookup: %s", bounty.repo_id)

    if repo_uuid:
        try:
            repo_exec_config = session.exec(
                select(RepoExecutionConfig).where(RepoExecutionConfig.repo_id == repo_uuid)
            ).first()
        except Exception:
            repo_exec_config = None

        if repo_exec_config:
            selected_execution_mode = repo_exec_config.execution_mode
            source = "repo_execution_config"

    if source != "repo_execution_config" and sandbox_provider == "runner":
        selected_execution_mode = ExecutionMode.SELF_HOSTED
        source = "sandbox_provider"

    return selected_execution_mode, source, repo_uuid


@router.post("/api/v1/repos/{repo_name}/commit")
@router.post("/repos/{repo_name}/commit")
@limiter.limit("10/minute")
async def api_commit(
    request: Request,
    repo_name: str,
    req: CommitRequest,
    session: Session = Depends(get_session),
    agent: Any = Depends(require_agent),
) -> CommitResponse:
    """
    Submit code via API (no git client needed).
    Creates files and commits to the bare repo.
    """
    trusted_agent_id = str(agent.id)
    trusted_agent_uuid: Optional[UUID] = None
    try:
        trusted_agent_uuid = UUID(trusted_agent_id)
    except (TypeError, ValueError):
        logger.warning("[commit] trusted agent id is not a valid UUID: %s", trusted_agent_id)
    if trusted_agent_id != req.agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    bare_repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(bare_repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="agenthub_commit_")
    bounty = None
    bounty_transitioned = False
    estimated_timeout_seconds = 300

    def rollback_bounty_transition() -> None:
        nonlocal bounty, bounty_transitioned
        if not bounty_transitioned or bounty is None:
            return
        try:
            from agent_auth.services.bounty_fsm import transition
            from persistence import BountyStatus

            _, rollback_err = transition(
                session,
                bounty.id,
                BountyStatus.IN_PROGRESS.value,
                ctx={"actor_type": "system", "actor_id": "system", "agent_id": trusted_agent_id},
            )
            if rollback_err:
                logger.warning("[commit] failed to rollback bounty transition: %s", rollback_err)
                return

            bounty_transitioned = False
            refreshed_bounty = session.get(Bounty, bounty.id)
            if refreshed_bounty:
                refreshed_bounty.current_steps = max(0, (refreshed_bounty.current_steps or 0) - 1)
                session.add(refreshed_bounty)
                session.commit()
                session.refresh(refreshed_bounty)
                bounty = refreshed_bounty
        except Exception as rollback_exc:
            logger.warning("[commit] exception while rolling back bounty transition: %s", rollback_exc)

    try:
        # Clone bare repo to temp dir
        subprocess.run([
            "git",
            "clone",
            bare_repo_path,
            work_dir,
        ], check=True, capture_output=True)

        # Write files
        for file_path, content in req.files.items():
            full_path = ensure_safe_path(work_dir, file_path, "Invalid file path")
            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True, capture_output=True)

        def allocate_branch_name() -> str:
            suffix = secrets.token_hex(8)
            if req.bounty_id:
                return f"agent/{trusted_agent_id}/bounty_{req.bounty_id}-{suffix}"
            ts = int(time.time())
            return f"agent/{trusted_agent_id}/dev_{ts}-{suffix}"

        def is_branch_conflict(stderr: str) -> bool:
            lowered = (stderr or "").lower()
            return (
                "non-fast-forward" in lowered
                or "failed to push some refs" in lowered
                or "cannot lock ref" in lowered
                or "already exists" in lowered
            )

        # Determine branch name and create branch
        branch_name = allocate_branch_name()
        ensure_safe_ref(branch_name)
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=work_dir, check=True, capture_output=True)

        # Collect traceability metadata available before commit
        parent_sha: Optional[str] = None
        parent_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
        if parent_result.returncode == 0:
            parsed_parent = parent_result.stdout.strip()
            if parsed_parent and parsed_parent != "HEAD":
                parent_sha = parsed_parent
        trace_timestamp = datetime.now(timezone.utc).isoformat()

        # --- Automated Verification (P1 MVP) ---
        v_exit_code = None
        v_stdout = None
        verification_mode: Optional[str] = None

        if req.bounty_id:
            bounty = session.get(Bounty, req.bounty_id)
            if not bounty:
                raise HTTPException(status_code=404, detail=f"Bounty {req.bounty_id} not found")

            # [Task Board] Ownership Verification
            if not bounty.assignee or bounty.assignee != trusted_agent_id:
                raise HTTPException(status_code=403, detail=f"Forbidden: Task {req.bounty_id} is locked by Agent {bounty.assignee}")
            if bounty.status not in {"in_progress", "submitted", "claimed"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Bounty {req.bounty_id} is not in progress (status={bounty.status}).",
                )

            # [Blind-Spot 2] Cost Control: Max Steps
            if bounty.current_steps >= bounty.max_steps:
                raise HTTPException(
                    status_code=403,
                    detail=f"Bounty {req.bounty_id} has exceeded the execution step limit ({bounty.max_steps}).",
                )

            # [Blind-Spot 2] Rough Cost Check
            estimated_timeout_seconds = max(300, min(12 * 3600, (bounty.estimated_hours or 1) * 3600))
            est_cost = ExecutionGuard.estimate_cost(
                is_new_session=True,
                command_count=1,
                timeout_seconds=estimated_timeout_seconds,
                command_str=bounty.test_command or "pytest",
                sandbox_provider=get_settings().normalized_sandbox_provider,
                cpu_cores=None,
            )
            if not budget_tracker.check_and_record(est_cost):
                raise HTTPException(status_code=402, detail="Daily platform budget exceeded. Try again tomorrow.")

            # Move bounty to submitted before git side effects; rollback on downstream failures
            from agent_auth.services.bounty_fsm import transition
            from persistence import BountyStatus

            updated, err = transition(
                session,
                bounty.id,
                BountyStatus.SUBMITTED.value,
                ctx={"actor_type": "agent", "actor_id": trusted_agent_id, "agent_id": trusted_agent_id},
            )
            if err:
                raise HTTPException(status_code=409, detail=err)
            bounty_transitioned = True

            refreshed_bounty = session.get(Bounty, bounty.id)
            if refreshed_bounty:
                refreshed_bounty.current_steps = max(0, (refreshed_bounty.current_steps or 0) + 1)
                session.add(refreshed_bounty)
                session.commit()
                session.refresh(refreshed_bounty)
                bounty = refreshed_bounty

        tree_hash_result = subprocess.run(["git", "write-tree"], cwd=work_dir, check=True, capture_output=True, text=True)
        tree_hash = tree_hash_result.stdout.strip()

        diff_patch_result = subprocess.run(
            ["git", "diff", "--cached", "--binary", "--full-index"],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        diff_patch = diff_patch_result.stdout
        diff_hash = compute_diff_hash_from_patch(diff_patch)
        reasoning_hash = compute_reasoning_hash(req.reasoning_trace)

        # Build TraceCommit JSON
        trace_commit = {
            "protocol_version": TRACE_COMMIT_PROTOCOL_VERSION,
            "tree_hash": tree_hash,
            "diff_hash": diff_hash,
            "reasoning_hash": reasoning_hash,
            "diff_summary": req.diff_summary,
            "reasoning_trace": req.reasoning_trace,
            "rejected_alternatives": req.rejected_alternatives,
            "context_snapshot": {
                "file_paths": sorted(list(req.files.keys())),
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {},
            },
            "intent": {
                "description": req.intent_description,
                "category": req.intent_category,
                "vector": req.intent_vector,
            },
            "author": {
                "agent_id": trusted_agent_id,
                "model_name": req.model_name,
            },
            "parent_sha": parent_sha,
            "timestamp": trace_timestamp,
        }
        trace_commit["binding_hash"] = compute_binding_hash(trace_commit)

        signing_secret = get_trace_signing_secret()
        if not signing_secret:
            raise HTTPException(status_code=500, detail="Trace signing secret is not configured")

        trace_commit["signature"] = sign_trace_commit(
            trace_commit,
            signing_secret,
            agent_id=trusted_agent_id,
        )

        # Validate TraceCommit schema and logic before committing
        try:
            TraceValidator.validate_commit(
                trace_commit,
                require_parent_sha=bool(parent_sha),
                require_timezone_aware_timestamp=True,
            )
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        # Commit with TraceCommit JSON as message
        commit_msg = json.dumps(trace_commit)
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=work_dir,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": trusted_agent_id,
                "GIT_AUTHOR_EMAIL": f"{trusted_agent_id}@agenthub.dev",
                "GIT_COMMITTER_NAME": trusted_agent_id,
                "GIT_COMMITTER_EMAIL": f"{trusted_agent_id}@agenthub.dev",
            },
        )

        # Push branch to bare repo (retry on branch-name conflict)
        max_push_attempts = 3
        for attempt in range(max_push_attempts):
            result = subprocess.run([
                "git",
                "push",
                "origin",
                branch_name,
            ], cwd=work_dir, capture_output=True, text=True)

            if result.returncode == 0:
                break

            if attempt < max_push_attempts - 1 and is_branch_conflict(result.stderr):
                old_branch_name = branch_name
                branch_name = allocate_branch_name()
                ensure_safe_ref(branch_name)
                subprocess.run(["git", "branch", "-m", branch_name], cwd=work_dir, check=True, capture_output=True)
                logger.warning(
                    "[commit] push branch conflict, retrying with new branch name: %s -> %s",
                    old_branch_name,
                    branch_name,
                )
                continue

            # Avoid leaking git stderr to clients
            logger.error("[commit] git push failed: %s", (result.stderr[:2000] if result.stderr else ""))
            raise HTTPException(status_code=502, detail="Git push failed")

        queue_runner_job = False
        runner_job_repo_id: Optional[UUID] = None
        runner_job_test_cmd: Optional[str] = None
        runner_job_id: Optional[str] = None
        resolved_execution_mode: Optional[ExecutionMode] = None
        execution_mode_source: Optional[str] = None

        if bounty:
            verification_mode = (bounty.verification_mode or "auto").lower()
            test_cmd = bounty.test_command or "pytest"
            sandbox_provider = get_settings().normalized_sandbox_provider
            resolved_execution_mode, execution_mode_source, runner_job_repo_id = _resolve_execution_mode(
                session=session,
                bounty=bounty,
                sandbox_provider=sandbox_provider,
            )

            if verification_mode == "auto":
                if resolved_execution_mode == ExecutionMode.SELF_HOSTED:
                    queue_runner_job = True
                    runner_job_test_cmd = test_cmd
                    v_exit_code, v_stdout = None, (
                        "Runner-based verification required: auto verification is delegated to runner polling"
                    )
                    logger.info(
                        "[automation] selected runner verification mode bounty=%s provider=%s mode=%s source=%s cmd=%s",
                        bounty.id,
                        sandbox_provider,
                        resolved_execution_mode.value,
                        execution_mode_source,
                        test_cmd,
                    )
                elif resolved_execution_mode == ExecutionMode.YOLO_MODE:
                    v_exit_code, v_stdout = None, (
                        "YOLO mode enabled: automated verification is skipped and submission is routed to human review"
                    )
                    logger.info(
                        "[automation] selected yolo verification mode bounty=%s provider=%s mode=%s source=%s cmd=%s",
                        bounty.id,
                        sandbox_provider,
                        resolved_execution_mode.value,
                        execution_mode_source,
                        test_cmd,
                    )
                else:
                    v_exit_code, v_stdout = None, (
                        "Auto verification deferred: local sandbox verification is disabled"
                    )
                    logger.info(
                        "[automation] deferred auto verification for bounty=%s provider=%s mode=%s source=%s cmd=%s",
                        bounty.id,
                        sandbox_provider,
                        resolved_execution_mode.value,
                        execution_mode_source,
                        test_cmd,
                    )
            elif verification_mode == "human":
                v_exit_code, v_stdout = None, "Human verification required"
            elif verification_mode == "external":
                v_exit_code, v_stdout = None, "External CI verification required"
            else:
                raise HTTPException(status_code=500, detail="Invalid bounty verification_mode")

        # Save record to history
        # Capture SHA
        sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
        if sha_result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to resolve commit SHA")
        sha = sha_result.stdout.strip()
        if not sha:
            raise HTTPException(status_code=500, detail="Failed to resolve commit SHA")
        persisted_trace_json = {**trace_commit}
        if sha:
            persisted_trace_json["commit_sha"] = sha
            persisted_trace_json["binding_hash"] = compute_binding_hash(persisted_trace_json)
            persisted_trace_json["signature"] = sign_trace_commit(
                persisted_trace_json,
                signing_secret,
                agent_id=trusted_agent_id,
            )

        try:
            validated_trace = TraceValidator.validate_commit(
                persisted_trace_json,
                expected_commit_sha=sha,
                require_commit_sha=True,
                require_parent_sha=bool(parent_sha),
                require_timezone_aware_timestamp=True,
            )
        except ValueError as ve:
            raise HTTPException(status_code=500, detail=f"Invalid persisted TraceCommit: {ve}")

        quality_warnings = TraceValidator.check_quality(validated_trace)
        if quality_warnings:
            persisted_trace_json["quality_warnings"] = quality_warnings

        if bounty and resolved_execution_mode:
            persisted_trace_json["execution_policy"] = {
                "mode": resolved_execution_mode.value,
                "source": execution_mode_source,
                "verification_mode": verification_mode,
                "runner_job_queued": queue_runner_job,
            }

        task_tree_sync = {
            "attempted": True,
            "status": "pending",
            "error": None,
        }

        # [Blind-Spot 1] Human-in-the-loop: status='pending'
        record = CommitRecord(
            repo_name=repo_name,
            commit_sha=sha,
            agent_id=req.agent_id,
            bounty_id=req.bounty_id,
            branch_name=branch_name if "branch_name" in locals() else None,
            status="pending",
            model_name=req.model_name,
            intent_category=req.intent_category,
            intent_description=req.intent_description,
            diff_summary=req.diff_summary,
            trace_json=persisted_trace_json,
            verification_exit_code=v_exit_code,
            verification_stdout=v_stdout[:5000] if v_stdout else None,
        )
        try:
            session.add(record)
            session.commit()
            session.refresh(record)
        except Exception as db_err:
            session.rollback()
            logger.exception("[commit] failed to persist commit record after git push: %s", db_err)
            raise HTTPException(status_code=502, detail="Commit persisted to git, but failed to record history")

        if queue_runner_job:
            env_fingerprint = {
                "repo_name": repo_name,
                "branch_name": branch_name,
                "trace_commit_sha": sha,
            }
            compute_job = ComputeJob(
                bounty_id=str(bounty.id),
                repo_id=runner_job_repo_id,
                submission_id=str(record.id),
                execution_mode=ExecutionMode.SELF_HOSTED,
                requester_user_id=None,
                requester_agent_id=trusted_agent_uuid,
                requester_type="agent",
                test_command=runner_job_test_cmd or "pytest",
                code_url=None,
                code_branch=branch_name,
                code_commit=sha,
                env_vars=env_fingerprint,
                timeout_seconds=estimated_timeout_seconds,
                status=ComputeJobStatus.PENDING,
            )
            try:
                session.add(compute_job)
                session.commit()
                session.refresh(compute_job)
                runner_job_id = str(compute_job.id)
                logger.info(
                    "[automation] queued runner compute job bounty=%s commit_id=%s job_id=%s mode=%s cmd=%s",
                    bounty.id,
                    record.id,
                    compute_job.id,
                    compute_job.execution_mode.value,
                    runner_job_test_cmd,
                )
            except Exception as runner_job_err:
                session.rollback()
                logger.exception("[commit] failed to create runner compute job after commit persist: %s", runner_job_err)
                raise HTTPException(status_code=502, detail="Commit recorded, but failed to enqueue runner compute job")

        # Sync task tree to repository after submission
        try:
            tree_service = GitTreeService(session, STORE_ROOT)
            tree_service.sync_repo_task_tree(repo_name, trusted_agent_id)
            task_tree_sync["status"] = "synced"
            persisted_trace_json["task_tree_sync"] = {"status": "synced"}
            persisted_trace_json["binding_hash"] = compute_binding_hash(persisted_trace_json)
            persisted_trace_json["signature"] = sign_trace_commit(
                persisted_trace_json,
                signing_secret,
                agent_id=trusted_agent_id,
            )
            record.trace_json = persisted_trace_json
            session.add(record)
            session.commit()
        except Exception as e:
            err = str(e)[:500]
            task_tree_sync["status"] = "failed"
            task_tree_sync["error"] = err
            persisted_trace_json["task_tree_sync"] = {"status": "failed", "error": err}
            persisted_trace_json["binding_hash"] = compute_binding_hash(persisted_trace_json)
            persisted_trace_json["signature"] = sign_trace_commit(
                persisted_trace_json,
                signing_secret,
                agent_id=trusted_agent_id,
            )
            try:
                record.trace_json = persisted_trace_json
                session.add(record)
                session.commit()
            except Exception as persist_err:
                session.rollback()
                logger.warning("[commit] failed to persist task tree sync failure: %s", persist_err)
            logger.warning("[commit] task tree sync failed repo=%s agent=%s error=%s", repo_name, trusted_agent_id, err)

        return CommitResponse(
            success=True,
            repo=repo_name,
            files_committed=list(req.files.keys()),
            agent=req.agent_id,
            sha=sha if "sha" in locals() else None,
            quality_warnings=quality_warnings,
            verification={
                "exit_code": v_exit_code,
                "passed": v_exit_code == 0 if v_exit_code is not None else None,
                "runner_job_id": runner_job_id,
                "execution_mode": resolved_execution_mode.value if resolved_execution_mode else None,
                "execution_mode_source": execution_mode_source,
            },
            task_tree_sync=task_tree_sync,
        )

    except subprocess.CalledProcessError as e:
        rollback_bounty_transition()

        # Avoid leaking raw stderr to clients
        stderr = getattr(e, "stderr", None)
        if isinstance(stderr, (bytes, bytearray)):
            err_msg = stderr.decode(errors="replace")[:2000]
        elif isinstance(stderr, str):
            err_msg = stderr[:2000]
        else:
            err_msg = str(e)
        logger.error("[commit] git operation failed: %s", err_msg)
        raise HTTPException(status_code=502, detail="Git operation failed")
    except HTTPException:
        rollback_bounty_transition()
        raise  # Re-raise HTTP exceptions (403, 404, etc.)
    except Exception as e:
        rollback_bounty_transition()

        logger.exception("[commit] unexpected error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)


# --- Review & Human-in-the-loop ---


@router.get("/api/v1/commits/pending")
def list_pending_submissions(
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity),
):
    """
    List submissions awaiting human approval.
    Requires either agent or human user authentication.
    """
    return session.exec(select(CommitRecord).where(CommitRecord.status == "pending")).all()


@router.get("/api/v1/commits/{commit_id}")
def get_commit_detail(
    commit_id: int,
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity),
):
    """Fetch a single commit record and its git diff (minimal PR view)."""
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")

    diff_text = None
    if record.commit_sha:
        try:
            repo_path = get_secure_repo_path(record.repo_name)
            diff_text = subprocess.check_output([
                "git",
                "show",
                "--no-color",
                record.commit_sha,
            ], cwd=repo_path, stderr=subprocess.STDOUT).decode("utf-8", errors="replace")
            diff_text = diff_text[:20000]
        except subprocess.CalledProcessError:
            diff_text = None

    return {
        "record": record,
        "diff": diff_text,
    }


@router.get("/api/v1/commits/pending/verification")
def list_pending_verifications(
    repo_name: Optional[str] = None,
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity),
):
    """List commits pending manual/external verification."""
    statement = select(CommitRecord, Bounty).where(CommitRecord.bounty_id == Bounty.id)
    statement = statement.where(CommitRecord.status == "pending")
    statement = statement.where(Bounty.verification_mode.in_(["human", "external"]))
    if repo_name:
        statement = statement.where(CommitRecord.repo_name == repo_name)

    include_stdout = bool(getattr(identity, "role", "").lower() in {"executor", "tester", "architect"})
    rows = session.exec(statement).all()
    results = []
    for record, bounty in rows:
        results.append({
            "commit_id": record.id,
            "repo_name": record.repo_name,
            "bounty_id": record.bounty_id,
            "verification_mode": bounty.verification_mode,
            "verification_exit_code": record.verification_exit_code,
            "verification_stdout": record.verification_stdout if include_stdout else None,
            "diff_summary": record.diff_summary,
            "agent_id": record.agent_id,
        })
    return results


@router.post("/api/v1/commits/{commit_id}/blackbox-test")
def submit_blackbox_test(
    commit_id: int,
    report: BlackboxReport,
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity),
):
    """Submit a blackbox test report for a commit."""
    # If identity is an Agent, check role
    if hasattr(identity, "role"):
        if identity.role.lower() != "tester":
            raise HTTPException(status_code=403, detail="Forbidden: only tester can submit blackbox reports")
    # If identity is a User, we allow it (admin action)

    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")

    record.blackbox_report = report.model_dump()
    record.blackbox_status = "passed" if report.overall_verdict.upper() == "PASS" else "failed"

    # Auto-extract memory from test report for the agent
    agent_id = getattr(identity, "agent_id", None)
    if not agent_id and hasattr(identity, "id"):
        # Fallback to database ID if agent_id string is missing
        agent_id = f"agent-{identity.id}"

    if agent_id:
        passed_count = sum(1 for r in report.results if r.passed)
        memory_content = (
            f"Blackbox test performed on {record.repo_name} (Verdict: {record.blackbox_status.upper()}). "
            f"Tested endpoint: {report.endpoint}. Success: {passed_count}/{len(report.results)}. "
        )
        failed_tests = [f"{r.method} {r.api_path}" for r in report.results if not r.passed]
        if failed_tests:
            memory_content += f"Failed patterns: {', '.join(failed_tests)}."

        try:
            # TODO: expose memory add via facade; skipping call until facade is available
            pass
        except Exception as e:
            logger.warning("Failed to store memory from report: %s", e)

    # Do NOT auto-approve or merge on blackbox PASS; require reviewer approval
    if record.blackbox_status == "passed":
        record.status = "pending"
    else:
        record.status = "rejected"
        if record.bounty_id:
            bounty = session.get(Bounty, record.bounty_id)
            if bounty and bounty.assignee == record.agent_id:
                from agent_auth.services.bounty_fsm import transition
                from persistence import BountyStatus

                updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
                if err:
                    logger.warning("FSM revert failed: %s", err)
                else:
                    # 黑盒失败/拒绝：回退一次步骤计数，避免无效重试长期锁死
                    try:
                        fresh = session.get(Bounty, record.bounty_id)
                        if fresh:
                            fresh.current_steps = max(0, (fresh.current_steps or 0) - 1)
                            session.add(fresh)
                    except Exception as _e:
                        logger.warning("Failed to decrement current_steps on reject: %s", _e)

    session.add(record)
    session.commit()
    return {"message": f"Blackbox test submitted. Status: {record.blackbox_status}", "commit_id": commit_id}


@router.post("/api/v1/commits/{commit_id}/verify")
def verify_commit(
    commit_id: int,
    req: VerificationRequest,
    session: Session = Depends(get_session),
    agent: Any = Depends(require_agent),
):
    """Manual verification from executor/reviewer agents."""
    if agent.role.lower() != "executor":
        raise HTTPException(status_code=403, detail="Only executor can verify")
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if record.status not in {"pending", "conflict"}:
        raise HTTPException(status_code=409, detail="Commit not in a verifiable state")

    record.verification_exit_code = req.exit_code
    record.verification_stdout = (req.stdout or "")[:5000] if req.stdout is not None else None
    session.add(record)
    session.commit()
    return {"message": "Verification recorded", "commit_id": commit_id}


@router.post("/api/v1/commits/{commit_id}/verify/external")
async def verify_commit_external(
    commit_id: int,
    request: Request,
    req: VerificationRequest,
    x_ci_token: str = Header(None, alias="X-CI-Token"),
    x_ci_signature: str = Header(None, alias="X-CI-Signature"),
    session: Session = Depends(get_session),
):
    """External CI callback verification."""
    expected_token = os.getenv("EXTERNAL_CI_TOKEN")
    expected_secret = os.getenv("EXTERNAL_CI_SECRET")
    if expected_secret:
        body_bytes = await request.body()
        computed = hmac.new(expected_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        if not x_ci_signature or not hmac.compare_digest(x_ci_signature, computed):
            raise HTTPException(status_code=401, detail="Invalid CI signature")
    else:
        if not expected_token or not x_ci_token or x_ci_token != expected_token:
            raise HTTPException(status_code=401, detail="Invalid CI token")
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if record.status not in {"pending", "conflict"}:
        raise HTTPException(status_code=409, detail="Commit not in a verifiable state")

    record.verification_exit_code = req.exit_code
    record.verification_stdout = (req.stdout or "")[:5000] if req.stdout is not None else None
    session.add(record)
    session.commit()
    return {"message": "External verification recorded", "commit_id": commit_id}
