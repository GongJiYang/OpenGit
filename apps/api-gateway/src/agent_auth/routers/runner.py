"""
Runner Router - Self-Hosted Compute Network API

API endpoints for the distributed CI/CD compute network.

Connection Flow:
1. User generates token: POST /runners/generate-token
2. User runs: agenthub-runner start --token=xxx
3. Runner registers: POST /runners/register
4. Runner sends heartbeats: POST /runners/heartbeat
5. Runner polls for jobs: GET /runners/poll-jobs
6. Runner submits results: POST /runners/submit-result

Security:
- All runner endpoints require valid runner token (not user JWT)
- Token is verified against bcrypt hash
- Banned runners are rejected with 403
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, List, Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlmodel import Session, select

from core.security import ensure_governance_allows_execution
from core.settings import get_settings

from ..models.runner import (
    Runner,
    RunnerStatus,
    RunnerToken,
    ComputeJob,
    ComputeJobStatus,
    ExecutionMode,
    GenerateTokenResponse,
    RunnerRegisterRequest,
    JobAssignment,
    SubmitResultRequest,
    RunnerResponse,
    ComputeJobResponse,
    AuditLog,
    RunnerPoolType,
    RunnerShareGrant,
)
from ..models import Agent
from ..models.platform import MembershipStatus, RepoMember, RepoRole, User, UserAgentBinding, UserRole
from ..database import get_db
from ..services.verification import VerificationService
from ..services.user_auth import get_current_user
from dependencies.auth import require_active_identity
from schemas.runner import (
    EndpointInfoResponse,
    ServiceReadyRequest,
    ServiceReadyResponse,
    ServiceStatusResponse,
    SubmitAuditResultRequest,
    UpdateRepoBindingRequest,
    UpsertRunnerShareGrantRequest,
    RunnerShareGrantResponse,
)

router = APIRouter(prefix="/runners", tags=["Runners"])


SENSITIVE_ENDPOINT_AGENT_ROLES = {"tester", "executor", "architect"}
SENSITIVE_ENDPOINT_REPO_ROLES = {
    RepoRole.BLACKBOX_TESTER.value,
    RepoRole.EXECUTOR.value,
    RepoRole.ARCHITECT.value,
}


def _normalize_role(role: Any) -> str:
    if role is None:
        return ""
    if hasattr(role, "value"):
        return str(role.value).lower()
    return str(role).lower()


def _get_active_repo_member(session: Session, repo_id: Optional[UUID], agent_id: UUID) -> Optional[RepoMember]:
    if not repo_id:
        return None
    return session.exec(
        select(RepoMember).where(
            RepoMember.repo_id == repo_id,
            RepoMember.agent_id == agent_id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
    ).first()


def _resolve_identity_agent_id(identity: Any) -> Optional[UUID]:
    raw_agent_id = getattr(identity, "id", None)
    if not raw_agent_id:
        return None
    try:
        return raw_agent_id if isinstance(raw_agent_id, UUID) else UUID(str(raw_agent_id))
    except (TypeError, ValueError):
        return None


def _is_authorized_job_identity(identity: Any, job: ComputeJob, session: Session) -> bool:
    """Authorize identity to read job/service status data."""
    if hasattr(identity, "status"):
        # Agent principal from API key
        agent_id = _resolve_identity_agent_id(identity)
        if not agent_id:
            return False

        agent = session.get(Agent, agent_id)
        if not agent:
            return False

        role = _normalize_role(agent.role)
        if role in SENSITIVE_ENDPOINT_AGENT_ROLES:
            return True

        if job.requester_agent_id and str(job.requester_agent_id) == str(agent_id):
            return True

        membership = _get_active_repo_member(session, job.repo_id, agent_id)
        return membership is not None

    # Human user from JWT
    user_id = getattr(identity, "id", None)
    if not user_id:
        return False

    if _normalize_role(getattr(identity, "role", None)) == UserRole.ADMIN.value:
        return True

    if job.requester_user_id and str(job.requester_user_id) == str(user_id):
        return True

    if not job.repo_id:
        return False

    binding = session.exec(
        select(UserAgentBinding).where(UserAgentBinding.user_id == user_id)
    ).first()
    if not binding:
        return False

    member = _get_active_repo_member(session, job.repo_id, binding.agent_id)
    return member is not None


def _is_authorized_endpoint_identity(identity: Any, job: ComputeJob, session: Session) -> bool:
    """Authorize identity to read sensitive service endpoint access tokens."""
    if hasattr(identity, "status"):
        agent_id = _resolve_identity_agent_id(identity)
        if not agent_id:
            return False

        agent = session.get(Agent, agent_id)
        if not agent:
            return False

        role = _normalize_role(agent.role)
        if role in SENSITIVE_ENDPOINT_AGENT_ROLES:
            return True

        if job.requester_agent_id and str(job.requester_agent_id) == str(agent_id):
            return True

        member = _get_active_repo_member(session, job.repo_id, agent_id)
        if not member:
            return False
        return _normalize_role(member.role) in SENSITIVE_ENDPOINT_REPO_ROLES

    user_id = getattr(identity, "id", None)
    if not user_id:
        return False

    if _normalize_role(getattr(identity, "role", None)) == UserRole.ADMIN.value:
        return True

    if job.requester_user_id and str(job.requester_user_id) == str(user_id):
        return True

    if not job.repo_id:
        return False

    binding = session.exec(
        select(UserAgentBinding).where(UserAgentBinding.user_id == user_id)
    ).first()
    if not binding:
        return False

    member = _get_active_repo_member(session, job.repo_id, binding.agent_id)
    if not member:
        return False
    return _normalize_role(member.role) in SENSITIVE_ENDPOINT_REPO_ROLES


def _runner_can_accept_job(runner: Runner, job: ComputeJob, session: Session) -> bool:
    """Check whether this runner is authorized to execute the job."""
    # Repository binding gate
    if not runner.is_global:
        job_repo_id = str(job.repo_id) if job.repo_id else None
        if not job_repo_id or job_repo_id not in runner.allowed_repo_ids:
            return False

    # Requester gate by pool type
    if runner.pool_type == RunnerPoolType.PLATFORM:
        return True

    if job.requester_user_id is None:
        # Legacy jobs without requester identity are restricted to private pool runners
        return runner.pool_type == RunnerPoolType.PRIVATE

    if runner.pool_type == RunnerPoolType.PRIVATE:
        return job.requester_user_id == runner.owner_user_id

    # SHARED: owner or explicit share grant
    if job.requester_user_id == runner.owner_user_id:
        return True

    grant = session.exec(
        select(RunnerShareGrant).where(
            RunnerShareGrant.runner_id == runner.id,
            RunnerShareGrant.grantee_user_id == job.requester_user_id,
            RunnerShareGrant.can_execute.is_(True),
        )
    ).first()
    return grant is not None


# ============== Helper Functions ==============

def _hash_token(token: str) -> str:
    """Hash token using bcrypt library directly to avoid passlib backend issues."""
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_token(token: str, token_hash: str) -> bool:
    """Verify token hash using bcrypt library directly."""
    try:
        return bcrypt.checkpw(token.encode("utf-8"), token_hash.encode("utf-8"))
    except ValueError:
        return False


def _runner_token_lookup(token: str) -> str:
    """Stable lookup key for indexed runner token candidate fetch."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _registration_token_lookup(token: str) -> str:
    """Stable lookup key for one-time registration token candidate fetch."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_runner_token(token: str, session: Session) -> Runner:
    """Verify runner authentication token and return runner."""
    if not token.startswith("ahauth_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format"
        )

    lookup = _runner_token_lookup(token)
    runner = session.exec(select(Runner).where(Runner.token_lookup == lookup)).first()

    # Backward compatibility during rollout: legacy rows may not have token_lookup yet.
    if runner is None:
        legacy_runners = session.exec(select(Runner).where(Runner.token_lookup.is_(None))).all()
        for legacy_runner in legacy_runners:
            if _verify_token(token, legacy_runner.token_hash):
                legacy_runner.token_lookup = lookup
                session.add(legacy_runner)
                session.commit()
                session.refresh(legacy_runner)
                runner = legacy_runner
                break

    if runner:
        if runner.token_lookup is None:
            runner.token_lookup = lookup
            session.add(runner)
            session.commit()
            session.refresh(runner)
        if _verify_token(token, runner.token_hash):
            if runner.is_banned or runner.status == RunnerStatus.BANNED:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Runner banned: {runner.banned_reason or 'Policy violation'}"
                )
            return runner

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid runner token"
    )


async def get_runner_from_header(
    x_runner_token: str = Header(..., description="Runner auth token"),
    session: Session = Depends(get_db)
) -> Runner:
    """FastAPI dependency to authenticate runner via header."""
    return verify_runner_token(x_runner_token, session)


def require_internal_token(
    x_internal_token: str = Header(..., alias="X-Internal-Token", description="Internal service token"),
) -> None:
    """Require internal token for infrastructure-only endpoints."""
    expected_token = get_settings().internal_api_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_API_TOKEN is not configured",
        )
    if not secrets.compare_digest(x_internal_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid internal token",
        )


# ============== Token Generation (User Auth Required) ==============

@router.post("/generate-token", response_model=GenerateTokenResponse)
async def generate_runner_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """
    Generate a one-time token for registering a new runner.

    User must be authenticated (JWT). Token is shown ONLY ONCE.
    """
    ensure_governance_allows_execution()
    token = f"ahrun_{secrets.token_urlsafe(32)}"
    token_hash = _hash_token(token)

    runner_token = RunnerToken(
        user_id=user.id,
        token_lookup=_registration_token_lookup(token),
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )

    session.add(runner_token)
    session.commit()

    return GenerateTokenResponse(
        token=token,
        expires_at=runner_token.expires_at,
        command=f'agenthub-runner start --token="{token}"'
    )


# ============== Runner Registration ==============

@router.post("/register")
async def register_runner(
    req: RunnerRegisterRequest,
    session: Session = Depends(get_db)
):
    """
    Register a new runner with one-time token.

    Called by agenthub-runner CLI on first start.
    Token is consumed and cannot be reused.
    """
    ensure_governance_allows_execution()

    lookup = _registration_token_lookup(req.token)
    statement = select(RunnerToken).where(RunnerToken.token_lookup == lookup)
    runner_token = session.exec(statement).first()

    if not runner_token or not _verify_token(req.token, runner_token.token_hash):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if runner_token.is_used:
        raise HTTPException(status_code=400, detail="Token already used")
    if runner_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Token expired")

    # Generate runner auth token
    runner_auth_token = f"ahauth_{secrets.token_urlsafe(32)}"
    runner_auth_hash = _hash_token(runner_auth_token)

    # Create runner
    runner = Runner(
        name=req.name,
        owner_user_id=runner_token.user_id,
        token_hash=runner_auth_hash,
        token_lookup=_runner_token_lookup(runner_auth_token),
        status=RunnerStatus.ONLINE,
        last_heartbeat_at=datetime.utcnow(),
        cpu_cores=req.cpu_cores,
        memory_gb=req.memory_gb,
        os_type=req.os_type,
        os_version=req.os_version,
        docker_version=req.docker_version,
        labels=req.labels,
    )

    session.add(runner)

    # Mark token as used
    runner_token.is_used = True
    runner_token.used_at = datetime.utcnow()
    session.add(runner_token)

    session.commit()
    session.refresh(runner)

    return {
        "success": True,
        "runner": RunnerResponse.model_validate(runner),
        "auth_token": runner_auth_token,
        "message": "Runner registered. Save the auth_token - it won't be shown again!"
    }


# ============== Heartbeat ==============

@router.post("/heartbeat")
async def runner_heartbeat(
    x_runner_token: str = Header(..., description="Runner auth token"),
    current_job_id: Optional[UUID] = None,
    session: Session = Depends(get_db)
):
    """
    Runner sends heartbeat to indicate it's still alive.

    Should be called every 30 seconds.
    Runners with no heartbeat for 60+ seconds are marked OFFLINE.
    """
    ensure_governance_allows_execution()

    runner = verify_runner_token(x_runner_token, session)

    runner.last_heartbeat_at = datetime.utcnow()
    runner.current_job_id = current_job_id
    runner.status = RunnerStatus.BUSY if current_job_id else RunnerStatus.ONLINE

    session.add(runner)
    session.commit()

    return {
        "success": True,
        "server_time": datetime.utcnow(),
        "next_heartbeat_seconds": 30
    }


# ============== Job Polling (Reverse Long-Polling) ==============

@router.get("/poll-jobs", response_model=List[JobAssignment])
async def poll_jobs(
    max_jobs: int = 1,
    runner: Runner = Depends(get_runner_from_header),
    session: Session = Depends(get_db)
):
    """
    Runner polls for available jobs.

    This is the core of the reverse long-polling architecture.
    Runner calls this endpoint every 5 seconds.

    Repository Binding:
    - If runner.is_global=True: can serve any repo
    - If runner.is_global=False: only serve repos in allowed_repo_ids
    """
    ensure_governance_allows_execution()
    if runner.status == RunnerStatus.BUSY:
        return []

    # Build base query
    statement = select(ComputeJob).where(
        ComputeJob.status == ComputeJobStatus.PENDING,
        ComputeJob.execution_mode == ExecutionMode.SELF_HOSTED,
    )

    jobs = session.exec(statement).all()

    assignments = []
    for job in jobs:
        if not _runner_can_accept_job(runner, job, session):
            continue

        # Assign job to runner
        job.runner_id = runner.id
        job.status = ComputeJobStatus.ASSIGNED
        job.assigned_at = datetime.utcnow()
        session.add(job)

        assignments.append(JobAssignment(
            job_id=job.id,
            code_url=job.code_url or "",
            code_branch=job.code_branch,
            test_command=job.test_command,
            env_vars=job.env_vars,
            timeout_seconds=job.timeout_seconds,
        ))

        if len(assignments) >= max_jobs:
            break

    if assignments:
        runner.status = RunnerStatus.BUSY
        runner.current_job_id = assignments[0].job_id
        session.add(runner)

    session.commit()
    return assignments


# ============== Result Submission (Zero-Trust) ==============

@router.post("/submit-result")
async def submit_job_result(
    req: SubmitResultRequest,
    runner: Runner = Depends(get_runner_from_header),
    session: Session = Depends(get_db)
):
    """
    Runner submits job execution results.

    Zero-Trust Requirements:
    - stdout_log is MANDATORY
    - Log is validated for real test output
    - Random audits may be triggered

    Recovery Features:
    - Failure classification (critical/warning/info)
    - Automatic retry with exponential backoff
    - Partial pass detection (>= 80% tests pass)
    - Human review fallback when retries exhausted
    """
    ensure_governance_allows_execution()
    from ..services.recovery_service import RecoveryService

    job = session.get(ComputeJob, req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.runner_id != runner.id:
        raise HTTPException(status_code=403, detail="Job not assigned to this runner")

    # Zero-Trust: Validate stdout log
    is_valid, validation_reason = VerificationService.validate_stdout(req.stdout_log)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"stdout_log validation failed: {validation_reason}"
        )

    # Update job with results
    job.exit_code = req.exit_code
    job.stdout_log = req.stdout_log
    job.stderr_log = req.stderr_log
    job.test_results = req.test_results
    job.completed_at = datetime.utcnow()

    # === Recovery Service: Classify Failure ===
    recovery_service = RecoveryService(session)
    severity, reason = recovery_service.classify_failure(
        exit_code=req.exit_code,
        stderr=req.stderr_log or "",
        test_results=req.test_results
    )

    # === Handle Test Results ===
    if req.passed or req.exit_code == 0:
        # All tests passed
        job.passed = True
        job.status = ComputeJobStatus.COMPLETED

        # Update test counts if available
        if req.test_results:
            job.total_tests = req.test_results.get("total", 0)
            job.passed_tests = req.test_results.get("passed", 0)
            job.failed_tests = req.test_results.get("failed", 0)
            job.skipped_tests = req.test_results.get("skipped", 0)

    else:
        # Tests failed - use recovery service
        job.passed = False

        # Update test counts
        if req.test_results:
            job.total_tests = req.test_results.get("total", 0)
            job.passed_tests = req.test_results.get("passed", 0)
            job.failed_tests = req.test_results.get("failed", 0)
            job.skipped_tests = req.test_results.get("skipped", 0)

            # Check for partial pass
            if job.total_tests > 0 and job.passed_tests > 0:
                final_status = recovery_service.update_test_results(
                    job=job,
                    total=job.total_tests,
                    passed=job.passed_tests,
                    failed=job.failed_tests,
                    skipped=job.skipped_tests
                )
                job.status = final_status
            else:
                # No tests ran or all failed - classify failure
                action, new_status = recovery_service.handle_job_failure(
                    job=job,
                    failure_reason=reason,
                    severity=severity
                )
                job.status = new_status
        else:
            # No test results - treat as failure
            action, new_status = recovery_service.handle_job_failure(
                job=job,
                failure_reason=reason,
                severity=severity
            )
            job.status = new_status

    # Record original runner for fallback tracking
    if job.original_runner_id is None:
        job.original_runner_id = runner.id

    session.add(job)

    # Update runner stats
    if job.status == ComputeJobStatus.COMPLETED:
        runner.total_jobs_completed += 1
    elif job.status == ComputeJobStatus.FAILED:
        runner.total_jobs_failed += 1

    if job.started_at and job.completed_at:
        runner.total_compute_seconds += int((job.completed_at - job.started_at).total_seconds())

    # Free up runner
    runner.status = RunnerStatus.ONLINE
    runner.current_job_id = None
    session.add(runner)

    # Check if audit should be triggered
    audit_triggered = False
    audit_reason = ""
    if job.status in [ComputeJobStatus.COMPLETED, ComputeJobStatus.PARTIAL_PASS]:
        audit_triggered, audit_reason = VerificationService.should_trigger_audit(
            runner=runner,
            session=session,
            job=job,
        )
        if audit_triggered:
            audit = VerificationService.create_audit(
                session=session,
                job=job,
                reason=audit_reason,
            )
            job.is_audited = True
            job.audit_job_id = audit.id
            job.audit_result = "pending"
            session.add(job)

    session.commit()

    # Build response with recovery info
    response = {
        "success": True,
        "job_id": str(job.id),
        "status": job.status,
        "audit_triggered": audit_triggered,
    }
    if audit_triggered:
        response["audit_reason"] = audit_reason

    # Add recovery-specific fields
    if job.retry_count > 0:
        response["retry_count"] = job.retry_count

    if job.status == ComputeJobStatus.PENDING:
        # Job queued for retry
        response["action"] = "retry_scheduled"
        response["next_retry_at"] = job.next_retry_at.isoformat() if job.next_retry_at else None

    if job.status == ComputeJobStatus.HUMAN_REVIEW:
        # Job needs human review
        response["action"] = "human_review_required"
        response["reason"] = "Max retries exceeded"

    if job.status == ComputeJobStatus.PARTIAL_PASS:
        # Partial pass
        response["action"] = "partial_pass"
        response["pass_rate"] = f"{job.passed_tests}/{job.total_tests}"

    return response


# ============== Service Ready Notification ==============

@router.post("/service-ready", response_model=ServiceReadyResponse)
async def report_service_ready(
    req: ServiceReadyRequest,
    runner: Runner = Depends(get_runner_from_header),
    session: Session = Depends(get_db)
):
    """
    Runner reports that the service is deployed and ready for testing.

    This endpoint:
    1. Validates the runner owns the job
    2. Generates a temporary JWT access token for the tester
    3. Stores the endpoint and token in the job record
    4. Notifies any waiting testers

    The access token:
    - Is a JWT signed with platform secret
    - Expires after specified hours (default 1 hour)
    - Can only access the specified service endpoint
    - Includes job_id and runner_id for audit
    """
    ensure_governance_allows_execution()
    import jwt as pyjwt

    # Get the job
    job = session.get(ComputeJob, req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.runner_id != runner.id:
        raise HTTPException(status_code=403, detail="Job not assigned to this runner")
    if job.status != ComputeJobStatus.RUNNING:
        raise HTTPException(status_code=400,
                            detail=f"Job status must be 'running', current: {job.status}")

    # Generate JWT access token for tester
    settings = get_settings()
    jwt_secret = settings.effective_jwt_secret_key
    if not jwt_secret:
        raise HTTPException(status_code=500, detail="JWT secret is not configured")

    expires_at = datetime.utcnow() + timedelta(hours=req.access_token_validity_hours)

    token_payload = {
        "job_id": str(job.id),
        "runner_id": str(runner.id),
        "bounty_id": job.bounty_id,
        "endpoint": req.service_endpoint,
        "type": "service_access",
        "iat": int(datetime.utcnow().timestamp()),
        "exp": int(expires_at.timestamp())
    }

    access_token = pyjwt.encode(token_payload, jwt_secret, algorithm="HS256")

    # Update job with endpoint info
    job.service_endpoint = req.service_endpoint
    job.access_token = access_token
    job.token_expires_at = expires_at
    session.add(job)

    # Create audit log entry
    audit_entry = AuditLog(
        job_id=job.id,
        runner_id=runner.id,
        reason="service_endpoint_registered",
        status="completed",
        explanation=f"Service endpoint registered: {req.service_endpoint}"
    )
    session.add(audit_entry)

    session.commit()

    return ServiceReadyResponse(
        success=True,
        job_id=job.id,
        service_endpoint=req.service_endpoint,
        access_token=access_token,
        expires_at=expires_at,
        message="Service endpoint registered. Tester can now access the service."
    )


# ============== Runner Management (User Auth) ==============

@router.get("", response_model=List[RunnerResponse])
async def list_my_runners(
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all runners owned by the authenticated user."""
    statement = select(Runner).where(Runner.owner_user_id == user.id)
    runners = session.exec(statement).all()
    return [RunnerResponse.model_validate(r) for r in runners]


@router.delete("/{runner_id}")
async def delete_runner(
    runner_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Delete a runner (soft disable)."""
    ensure_governance_allows_execution()
    runner = session.get(Runner, runner_id)

    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    runner.status = RunnerStatus.DISABLED
    session.add(runner)
    session.commit()

    return {"success": True, "message": "Runner disabled"}


# ============== Repository Binding Management ==============

@router.put("/{runner_id}/repos")
async def update_runner_repos(
    runner_id: UUID,
    req: UpdateRepoBindingRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """
    Update runner's repository bindings.

    - is_global=True: Runner serves all repos (ignores allowed_repo_ids)
    - is_global=False: Runner only serves repos in allowed_repo_ids
    """
    ensure_governance_allows_execution()
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    # Update bindings
    runner.allowed_repo_ids = req.allowed_repo_ids
    runner.is_global = req.is_global

    if req.pool_type:
        try:
            runner.pool_type = RunnerPoolType(req.pool_type.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid pool_type: {req.pool_type}")

    session.add(runner)
    session.commit()
    session.refresh(runner)

    return {
        "success": True,
        "runner_id": str(runner.id),
        "is_global": runner.is_global,
        "pool_type": runner.pool_type.value,
        "allowed_repo_ids": runner.allowed_repo_ids
    }


@router.get("/{runner_id}/shares", response_model=List[RunnerShareGrantResponse])
async def list_runner_shares(
    runner_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """List share grants of a runner. Owner only."""
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    grants = session.exec(
        select(RunnerShareGrant)
        .where(RunnerShareGrant.runner_id == runner_id)
        .order_by(RunnerShareGrant.created_at.desc())
    ).all()
    return [RunnerShareGrantResponse.model_validate(g) for g in grants]


@router.post("/{runner_id}/shares", response_model=RunnerShareGrantResponse)
async def upsert_runner_share(
    runner_id: UUID,
    req: UpsertRunnerShareGrantRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Create or update a share grant for a runner. Owner only."""
    ensure_governance_allows_execution()
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    if req.grantee_user_id == runner.owner_user_id:
        raise HTTPException(status_code=400, detail="Owner does not need a share grant")

    grant = session.exec(
        select(RunnerShareGrant).where(
            RunnerShareGrant.runner_id == runner_id,
            RunnerShareGrant.grantee_user_id == req.grantee_user_id,
        )
    ).first()

    if grant:
        grant.can_execute = req.can_execute
        session.add(grant)
    else:
        grant = RunnerShareGrant(
            runner_id=runner_id,
            grantee_user_id=req.grantee_user_id,
            granted_by_user_id=user.id,
            can_execute=req.can_execute,
        )
        session.add(grant)

    # Sharing implies shared pool
    if runner.pool_type == RunnerPoolType.PRIVATE:
        runner.pool_type = RunnerPoolType.SHARED
        session.add(runner)

    session.commit()
    session.refresh(grant)
    return RunnerShareGrantResponse.model_validate(grant)


@router.delete("/{runner_id}/shares/{grantee_user_id}")
async def delete_runner_share(
    runner_id: UUID,
    grantee_user_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Delete a share grant for a runner. Owner only."""
    ensure_governance_allows_execution()
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    grant = session.exec(
        select(RunnerShareGrant).where(
            RunnerShareGrant.runner_id == runner_id,
            RunnerShareGrant.grantee_user_id == grantee_user_id,
        )
    ).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Share grant not found")

    session.delete(grant)
    session.commit()

    return {"success": True, "runner_id": str(runner_id), "grantee_user_id": str(grantee_user_id)}


@router.post("/{runner_id}/repos/{repo_id}")
async def add_runner_repo(
    runner_id: UUID,
    repo_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Add a single repository to runner's allowed list."""
    ensure_governance_allows_execution()

    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    repo_id_str = str(repo_id)

    # Add repo if not already in list
    if repo_id_str not in runner.allowed_repo_ids:
        runner.allowed_repo_ids = runner.allowed_repo_ids + [repo_id_str]
        runner.is_global = False  # Adding specific repo makes it non-global
        session.add(runner)
        session.commit()
        session.refresh(runner)

    return {
        "success": True,
        "runner_id": str(runner.id),
        "is_global": runner.is_global,
        "allowed_repo_ids": runner.allowed_repo_ids
    }


@router.delete("/{runner_id}/repos/{repo_id}")
async def remove_runner_repo(
    runner_id: UUID,
    repo_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Remove a single repository from runner's allowed list."""
    ensure_governance_allows_execution()
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    repo_id_str = str(repo_id)

    # Remove repo from list
    if repo_id_str in runner.allowed_repo_ids:
        new_list = [r for r in runner.allowed_repo_ids if r != repo_id_str]
        runner.allowed_repo_ids = new_list
        session.add(runner)
        session.commit()
        session.refresh(runner)

    return {
        "success": True,
        "runner_id": str(runner.id),
        "is_global": runner.is_global,
        "allowed_repo_ids": runner.allowed_repo_ids
    }


@router.get("/{runner_id:uuid}", response_model=RunnerResponse)
async def get_runner_info(
    runner_id: UUID,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed info about a specific runner."""
    runner = session.get(Runner, runner_id)

    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    return RunnerResponse.model_validate(runner)


# ============== Job Listing (For Testers) ==============
# NOTE: This route MUST be defined BEFORE /{runner_id}/jobs to avoid route conflicts

@router.get("/jobs", response_model=List[ComputeJobResponse])
async def list_compute_jobs(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_db),
    _: Any = Depends(require_active_identity),
):
    """
    List compute jobs for testing interface.

    Query parameters:
    - status: Filter by job status (pending, running, completed, failed, etc.)
    - limit: Max number of results (default 50)
    - offset: Pagination offset

    Returns jobs sorted by creation time (newest first).
    """
    query = select(ComputeJob)

    # Apply status filter if provided
    if status:
        try:
            status_enum = ComputeJobStatus(status)
            query = query.where(ComputeJob.status == status_enum)
        except ValueError:
            pass  # Invalid status, ignore filter

    # Order by creation time, newest first
    query = query.order_by(ComputeJob.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    jobs = session.exec(query).all()

    return [
        ComputeJobResponse(
            id=job.id,
            bounty_id=job.bounty_id,
            repo_id=job.repo_id,
            runner_id=job.runner_id,
            status=job.status,
            execution_mode=job.execution_mode,
            test_command=job.test_command,
            exit_code=job.exit_code,
            passed=job.passed,
            is_audited=job.is_audited,
            audit_result=job.audit_result,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at
        )
        for job in jobs
    ]


@router.get("/{runner_id}/jobs", response_model=List[ComputeJobResponse])
async def get_runner_jobs(
    runner_id: UUID,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Get job execution history for a specific runner.

    - status: Filter by job status (pending, assigned, running, completed, failed, timeout, audit_failed)
    - limit: Max number of jobs to return (default 50, max 100)
    - offset: Pagination offset
    """
    # Verify runner ownership
    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your runner")

    # Build query
    limit = min(limit, 100)  # Cap at 100
    statement = select(ComputeJob).where(
        ComputeJob.runner_id == runner_id
    ).order_by(ComputeJob.created_at.desc())

    # Apply status filter
    if status:
        try:
            status_enum = ComputeJobStatus(status.lower())
            statement = statement.where(ComputeJob.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    # Apply pagination
    statement = statement.offset(offset).limit(limit)

    jobs = session.exec(statement).all()
    return [ComputeJobResponse.model_validate(job) for job in jobs]


# ============== Job Status ==============

@router.get("/jobs/{job_id}", response_model=ComputeJobResponse)
async def get_job_status(
    job_id: UUID,
    session: Session = Depends(get_db),
    identity: Any = Depends(require_active_identity),
):
    """Get status of a compute job."""
    job = session.get(ComputeJob, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _is_authorized_job_identity(identity, job, session):
        raise HTTPException(status_code=403, detail="Forbidden")

    return ComputeJobResponse.model_validate(job)


# ============== Internal Audit Endpoints ==============



@router.post("/internal/audit/submit")
async def submit_audit_result(
    req: SubmitAuditResultRequest,
    _: None = Depends(require_internal_token),
    session: Session = Depends(get_db)
):
    """
    Submit audit result from trusted infrastructure.

    This is an internal endpoint called by the platform's audit worker.
    It compares the runner's submission with the audited result and
    applies reputation penalties or bans as needed.
    """
    ensure_governance_allows_execution()

    from ..models.runner import AuditResult

    audit = session.get(AuditLog, req.audit_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    if audit.status != "pending":
        raise HTTPException(status_code=400, detail="Audit already processed")

    # Compare results
    result, explanation = VerificationService.execute_audit(
        original_stdout=audit.original_stdout or "",
        original_exit_code=audit.original_exit_code or 0,
        audited_stdout=req.audited_stdout,
        audited_exit_code=req.audited_exit_code,
        original_test_command=audit.original_test_command,
        audited_test_command=req.audited_test_command,
        original_code_commit=audit.original_code_commit,
        audited_code_commit=req.audited_code_commit,
        original_env_fingerprint=audit.original_env_fingerprint,
        audited_env_fingerprint=req.audited_env_fingerprint,
    )

    # Apply result
    VerificationService.apply_audit_result(
        session=session,
        audit=audit,
        result=result,
        explanation=explanation,
        audited_stdout=req.audited_stdout,
        audited_exit_code=req.audited_exit_code,
        audited_test_command=req.audited_test_command,
        audited_code_commit=req.audited_code_commit,
        audited_env_fingerprint=req.audited_env_fingerprint,
    )

    # Update job audit status
    job = session.get(ComputeJob, audit.job_id)
    if job:
        job.audit_result = result.value
        job.audit_mismatch_details = explanation
        if result == AuditResult.FAILED:
            job.status = ComputeJobStatus.AUDIT_FAILED
        session.add(job)
        session.commit()

    return {
        "success": True,
        "audit_id": str(audit.id),
        "result": result.value,
        "explanation": explanation
    }


@router.get("/internal/audit/pending")
async def get_pending_audits(
    limit: int = 10,
    _: None = Depends(require_internal_token),
    session: Session = Depends(get_db)
):
    """Get pending audits for the audit worker to process."""
    statement = select(AuditLog).where(
        AuditLog.status == "pending"
    ).limit(limit)

    audits = session.exec(statement).all()

    return [
        {
            "audit_id": str(audit.id),
            "job_id": str(audit.job_id),
            "runner_id": str(audit.runner_id),
            "reason": audit.reason,
            "created_at": audit.created_at.isoformat()
        }
        for audit in audits
    ]


# ============== Service Status API (for Testers) ==============

@router.get("/jobs/{job_id}/service-status", response_model=ServiceStatusResponse)
async def get_service_status(
    job_id: UUID,
    session: Session = Depends(get_db),
    identity: Any = Depends(require_active_identity),
):
    """
    Get the current status of a service deployment.

    This endpoint allows testers to poll for service availability
    without relying on push notifications.

    Status values:
    - pending: Job is waiting for a runner
    - assigned: Job assigned to runner, not started
    - running: Runner is executing the job
    - service_ready: Service is deployed and accessible
    - completed: Job completed successfully
    - failed: Job failed
    """
    job = session.get(ComputeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _is_authorized_job_identity(identity, job, session):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Determine if service is ready for testing
    is_ready = (
        job.service_endpoint is not None and
        job.access_token is not None and
        job.token_expires_at is not None and
        job.token_expires_at > datetime.utcnow()
    )

    # Calculate remaining token validity
    expires_in = None
    if job.token_expires_at:
        now = datetime.utcnow()
        if job.token_expires_at > now:
            expires_in = int((job.token_expires_at - now).total_seconds())

    # Get runner status if assigned
    runner_status = None
    if job.runner_id:
        runner = session.get(Runner, job.runner_id)
        if runner:
            runner_status = runner.status.value

    # Determine message based on status
    if job.status == ComputeJobStatus.PENDING:
        message = "Waiting for available runner"
    elif job.status == ComputeJobStatus.ASSIGNED:
        message = "Job assigned to runner, deployment pending"
    elif job.status == ComputeJobStatus.RUNNING:
        if is_ready:
            message = "Service is ready for testing"
        else:
            message = "Runner is deploying the service"
    elif job.status == ComputeJobStatus.COMPLETED:
        message = "Job completed"
    elif job.status == ComputeJobStatus.FAILED:
        message = "Job failed"
    elif job.status == ComputeJobStatus.TIMEOUT:
        message = "Job timed out"
    elif job.status == ComputeJobStatus.AUDIT_FAILED:
        message = "Job failed audit verification"
    else:
        message = f"Job status: {job.status.value}"

    return ServiceStatusResponse(
        job_id=job.id,
        status=job.status.value,
        service_endpoint=job.service_endpoint if is_ready else None,
        access_token=None,
        token_expires_at=job.token_expires_at if is_ready else None,
        token_expires_in_seconds=expires_in,
        runner_id=job.runner_id,
        runner_status=runner_status,
        is_ready_for_testing=is_ready,
        message=message
    )


@router.get("/jobs/{job_id}/endpoint", response_model=EndpointInfoResponse)
async def get_service_endpoint(
    job_id: UUID,
    session: Session = Depends(get_db),
    identity: Any = Depends(require_active_identity),
):
    """
    Get the service endpoint info for testing.

    This is a convenience endpoint for testers to quickly get
    the endpoint URL and access token.

    Requires authentication - only the bounty owner or assigned tester
    can access this endpoint.
    """
    job = session.get(ComputeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _is_authorized_endpoint_identity(identity, job, session):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Check if service is ready
    if not job.service_endpoint:
        raise HTTPException(
            status_code=400,
            detail="Service endpoint not yet registered. Runner may still be deploying."
        )

    # Check if token is still valid
    if not job.token_expires_at or job.token_expires_at <= datetime.utcnow():
        raise HTTPException(
            status_code=410,
            detail="Access token has expired. Request a new token from the runner."
        )

    # Calculate remaining time
    expires_in = int((job.token_expires_at - datetime.utcnow()).total_seconds())

    return EndpointInfoResponse(
        job_id=job.id,
        bounty_id=job.bounty_id,
        service_endpoint=job.service_endpoint,
        access_token=job.access_token,
        expires_at=job.token_expires_at,
        expires_in_seconds=expires_in,
        health_check_url=f"{job.service_endpoint.rstrip('/')}/health"
    )
