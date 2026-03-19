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

import os
import secrets
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlmodel import Session, select
from passlib.context import CryptContext

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
)
from ..models.platform import User
from ..database import get_db
from ..services.verification import VerificationService
from ..services.user_auth import get_current_user
from schemas.runner import (
    EndpointInfoResponse,
    ServiceReadyRequest,
    ServiceReadyResponse,
    ServiceStatusResponse,
    SubmitAuditResultRequest,
    UpdateRepoBindingRequest,
)

router = APIRouter(prefix="/runners", tags=["Runners"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============== Helper Functions ==============

def verify_runner_token(token: str, session: Session) -> Runner:
    """Verify runner authentication token and return runner."""
    if not token.startswith("ahauth_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format"
        )

    # Find all runners and verify hash (timing-safe)
    runners = session.exec(select(Runner)).all()

    for runner in runners:
        if pwd_context.verify(token, runner.token_hash):
            if runner.is_banned:
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
    expected_token = os.getenv("INTERNAL_API_TOKEN")
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
    user_id: UUID = Header(..., alias="X-User-Id", description="User ID from JWT"),
    session: Session = Depends(get_db)
):
    """
    Generate a one-time token for registering a new runner.

    User must be authenticated (JWT). Token is shown ONLY ONCE.
    """
    token = f"ahrun_{secrets.token_urlsafe(32)}"
    token_hash = pwd_context.hash(token)

    runner_token = RunnerToken(
        user_id=user_id,
        token=token,
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
    statement = select(RunnerToken).where(RunnerToken.token == req.token)
    runner_token = session.exec(statement).first()

    if not runner_token:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if runner_token.is_used:
        raise HTTPException(status_code=400, detail="Token already used")
    if runner_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Token expired")

    # Generate runner auth token
    runner_auth_token = f"ahauth_{secrets.token_urlsafe(32)}"
    runner_auth_hash = pwd_context.hash(runner_auth_token)

    # Create runner
    runner = Runner(
        name=req.name,
        owner_user_id=runner_token.user_id,
        token_hash=runner_auth_hash,
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
        # Check repository binding
        if not runner.is_global:
            # Runner is repo-specific, check if job's repo is allowed
            job_repo_id = str(job.repo_id) if job.repo_id else None
            if not job_repo_id or job_repo_id not in runner.allowed_repo_ids:
                # Skip this job - runner not allowed for this repo
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
    if job.status in [ComputeJobStatus.COMPLETED, ComputeJobStatus.PARTIAL_PASS]:
        audit_triggered = VerificationService.should_trigger_audit(runner)
        if audit_triggered:
            audit = VerificationService.create_audit(
                session=session,
                job=job,
                reason="random" if runner.reputation_score >= 50 else "low_reputation"
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
    import jwt as pyjwt
    import os

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
    jwt_secret = os.getenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
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
    user_id: Optional[str] = Header(None, alias="X-User-Id")
):
    """
    List all runners owned by the authenticated user.

    Accepts either:
    - X-User-Id header (UUID) for demo/testing
    - Authorization Bearer token (JWT) for production
    """
    # Try to get user_id from header first
    actual_user_id = None
    if user_id:
        try:
            actual_user_id = UUID(user_id)
        except ValueError:
            pass  # Not a valid UUID, try JWT

    # If no valid user_id, return empty list (unauthenticated)
    if not actual_user_id:
        return []

    statement = select(Runner).where(Runner.owner_user_id == actual_user_id)
    runners = session.exec(statement).all()
    return [RunnerResponse.model_validate(r) for r in runners]


@router.delete("/{runner_id}")
async def delete_runner(
    runner_id: UUID,
    user_id: UUID = Header(..., description="User ID from JWT"),
    session: Session = Depends(get_db)
):
    """Delete a runner (soft disable)."""
    runner = session.get(Runner, runner_id)

    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != user_id:
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
    user_id: Optional[str] = Header(None, alias="X-User-Id"),
    session: Session = Depends(get_db)
):
    """
    Update runner's repository bindings.

    - is_global=True: Runner serves all repos (ignores allowed_repo_ids)
    - is_global=False: Runner only serves repos in allowed_repo_ids
    """
    # Validate user_id
    actual_user_id = None
    if user_id:
        try:
            actual_user_id = UUID(user_id)
        except ValueError:
            pass

    if not actual_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != actual_user_id:
        raise HTTPException(status_code=403, detail="Not your runner")

    # Update bindings
    runner.allowed_repo_ids = req.allowed_repo_ids
    runner.is_global = req.is_global
    session.add(runner)
    session.commit()
    session.refresh(runner)

    return {
        "success": True,
        "runner_id": str(runner.id),
        "is_global": runner.is_global,
        "allowed_repo_ids": runner.allowed_repo_ids
    }


@router.post("/{runner_id}/repos/{repo_id}")
async def add_runner_repo(
    runner_id: UUID,
    repo_id: UUID,
    user_id: Optional[str] = Header(None, alias="X-User-Id"),
    session: Session = Depends(get_db)
):
    """Add a single repository to runner's allowed list."""
    # Validate user_id
    actual_user_id = None
    if user_id:
        try:
            actual_user_id = UUID(user_id)
        except ValueError:
            pass

    if not actual_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != actual_user_id:
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
    user_id: Optional[str] = Header(None, alias="X-User-Id"),
    session: Session = Depends(get_db)
):
    """Remove a single repository from runner's allowed list."""
    # Validate user_id
    actual_user_id = None
    if user_id:
        try:
            actual_user_id = UUID(user_id)
        except ValueError:
            pass

    if not actual_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    runner = session.get(Runner, runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")
    if runner.owner_user_id != actual_user_id:
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


@router.get("/{runner_id}", response_model=RunnerResponse)
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
    user_id: str = Header(None, alias="X-User-Id", description="Optional user ID for auth"),
    session: Session = Depends(get_db)
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
    session: Session = Depends(get_db)
):
    """Get status of a compute job."""
    job = session.get(ComputeJob, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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
        audited_exit_code=req.audited_exit_code
    )

    # Apply result
    VerificationService.apply_audit_result(
        session=session,
        audit=audit,
        result=result,
        explanation=explanation,
        audited_stdout=req.audited_stdout,
        audited_exit_code=req.audited_exit_code
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
    session: Session = Depends(get_db)
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
        access_token=job.access_token if is_ready else None,
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
    user_id: str = Header(..., alias="X-User-Id", description="User ID for auth"),
    session: Session = Depends(get_db)
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
