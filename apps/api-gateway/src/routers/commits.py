import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from agenthub_execution_vmm.guard import ExecutionGuard
from agenthub_protocol.path_utils import ensure_safe_path
from agenthub_protocol.validator import TraceValidator
from core.middleware import limiter
from core.security import STORE_ROOT, ensure_safe_ref, get_secure_repo_path
from dependencies.auth import require_active_identity, require_agent
from dependencies.services import get_sandbox
from git_tree_service import GitTreeService
from persistence import Bounty, CommitRecord, get_session
from schemas.commits import BlackboxReport, CommitRequest, VerificationRequest

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))
execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


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


@router.post("/api/v1/repos/{repo_name}/commit")
@router.post("/repos/{repo_name}/commit")
@limiter.limit("10/minute")
async def api_commit(
    request: Request,
    repo_name: str,
    req: CommitRequest,
    session: Session = Depends(get_session),
    agent: Any = Depends(require_agent),
):
    """
    Submit code via API (no git client needed).
    Creates files and commits to the bare repo.
    """
    trusted_agent_id = str(agent.id)
    if trusted_agent_id != req.agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    bare_repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(bare_repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="agenthub_commit_")

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

        # Determine Branch Name (Level  isolation)
        if req.bounty_id:
            branch_name = f"agent/{trusted_agent_id}/bounty_{req.bounty_id}"
        else:
            ts = int(time.time())
            branch_name = f"agent/{trusted_agent_id}/dev_{ts}"
        ensure_safe_ref(branch_name)

        # Create and switch to the new branch
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=work_dir, check=True, capture_output=True)

        # Build TraceCommit JSON
        trace_commit = {
            "diff_summary": req.diff_summary,
            "reasoning_trace": req.reasoning_trace,
            "rejected_alternatives": [],
            "context_snapshot": {
                "file_paths": list(req.files.keys()),
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {},
            },
            "intent": {
                "description": req.intent_description,
                "category": req.intent_category,
            },
            "author": {
                "agent_id": trusted_agent_id,
                "model_name": req.model_name,
            },
        }

        # Validate TraceCommit schema and logic before committing
        try:
            TraceValidator.validate_commit(trace_commit)
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

        # Push specific branch to bare repo
        result = subprocess.run([
            "git",
            "push",
            "origin",
            branch_name,
        ], cwd=work_dir, capture_output=True, text=True)

        if result.returncode != 0:
            # Avoid leaking git stderr to clients
            logger.error("[commit] git push failed: %s", (result.stderr[:2000] if result.stderr else ""))
            return {"success": False, "error": "Git push failed"}

        # --- Automated Verification (P1 MVP) ---
        v_exit_code = None
        v_stdout = None

        if req.bounty_id:
            bounty = session.get(Bounty, req.bounty_id)
            if bounty:
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
                est_cost = ExecutionGuard.estimate_cost(is_new_session=True)
                if not budget_tracker.check_and_record(est_cost):
                    raise HTTPException(status_code=402, detail="Daily platform budget exceeded. Try again tomorrow.")

                # 先进行状态流转（成功后再计步）
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
                # 成功提交后再递增步骤（防止无效重试占用配额）
                bounty.current_steps = max(0, (bounty.current_steps or 0) + 1)
                session.add(bounty)
                session.commit()
                session.refresh(bounty)

                verification_mode = (bounty.verification_mode or "auto").lower()
                test_cmd = bounty.test_command or "pytest"
                if verification_mode == "auto":
                    sb = get_sandbox(request)
                    if sb is None:
                        raise HTTPException(status_code=503, detail="Sandbox is disabled")
                    logger.info("[automation] Running validation for Bounty %s: %s", bounty.id, test_cmd)
                    try:
                        async with execution_semaphore:
                            # Use a temporary worktree cloned from the bare repo for running tests
                            verify_work_dir = tempfile.mkdtemp(prefix="agenthub_auto_verify_")
                            try:
                                subprocess.run(["git", "clone", bare_repo_path, verify_work_dir], check=True, capture_output=True)
                                v_exit_code, v_stdout = sb.run_tests(verify_work_dir, test_cmd)
                            finally:
                                shutil.rmtree(verify_work_dir, ignore_errors=True)
                    except Exception as e:
                        v_exit_code, v_stdout = -1, f"Execution failed under semaphore: {str(e)}"
                elif verification_mode == "human":
                    v_exit_code, v_stdout = None, "Human verification required"
                elif verification_mode == "external":
                    v_exit_code, v_stdout = None, "External CI verification required"
                else:
                    v_exit_code, v_stdout = -1, f"Unknown verification_mode: {verification_mode}"

        # Save record to history
        try:
            # Capture SHA
            sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
            sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

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
                trace_json=trace_commit,
                verification_exit_code=v_exit_code,
                verification_stdout=v_stdout[:5000] if v_stdout else None,
            )
            session.add(record)
            session.commit()
        except Exception as db_err:
            logger.error("Failed to record commit history: %s", db_err)

        # Sync task tree to repository after submission
        try:
            tree_service = GitTreeService(session, STORE_ROOT)
            tree_service.sync_repo_task_tree(repo_name, trusted_agent_id)
        except Exception as e:
            logger.warning("Failed to sync task tree after commit: %s", e)

        return {
            "success": True,
            "repo": repo_name,
            "files_committed": list(req.files.keys()),
            "agent": req.agent_id,
            "sha": sha if "sha" in locals() else None,
            "verification": {
                "exit_code": v_exit_code,
                "passed": v_exit_code == 0 if v_exit_code is not None else None,
            },
        }

    except subprocess.CalledProcessError as e:
        # Avoid leaking raw stderr to clients
        err_msg = e.stderr.decode(errors="replace")[:2000] if getattr(e, "stderr", None) else str(e)
        logger.error("[commit] git operation failed: %s", err_msg)
        return {"success": False, "error": "Git operation failed"}
    except HTTPException:
        raise  # Re-raise HTTP exceptions (403, 404, etc.)
    except Exception as e:
        logger.exception("[commit] unexpected error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
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
def list_pending_verifications(repo_name: Optional[str] = None, session: Session = Depends(get_session)):
    """List commits pending manual/external verification."""
    statement = select(CommitRecord, Bounty).where(CommitRecord.bounty_id == Bounty.id)
    statement = statement.where(CommitRecord.status == "pending")
    statement = statement.where(Bounty.verification_mode.in_(["human", "external"]))
    if repo_name:
        statement = statement.where(CommitRecord.repo_name == repo_name)
    rows = session.exec(statement).all()
    results = []
    for record, bounty in rows:
        results.append({
            "commit_id": record.id,
            "repo_name": record.repo_name,
            "bounty_id": record.bounty_id,
            "verification_mode": bounty.verification_mode,
            "verification_exit_code": record.verification_exit_code,
            "verification_stdout": record.verification_stdout,
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
