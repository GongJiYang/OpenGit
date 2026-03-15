"""
Recovery Router

API endpoints for failure recovery management:
- Human review queue
- Manual approval/rejection
- Recovery statistics
"""

from typing import List, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlmodel import Session

from ..database import get_db
from ..models.runner import ComputeJob, ComputeJobStatus
from ..services.recovery_service import RecoveryService, FailureSeverity

router = APIRouter(prefix="/v1/recovery", tags=["recovery"])


# ==================== Request Models ====================

class ApproveReviewRequest(BaseModel):
    reviewer_id: UUID
    notes: str = ""


class RejectReviewRequest(BaseModel):
    reviewer_id: UUID
    reason: str


class HumanReviewJobResponse(BaseModel):
    id: UUID
    bounty_id: str
    retry_count: int
    max_retries: int
    failure_reason: Optional[str]
    failure_severity: Optional[str]
    created_at: datetime
    updated_at: datetime
    original_runner_id: Optional[UUID]
    stdout_preview: Optional[str] = None  # First 500 chars

    class Config:
        from_attributes = True


class RecoveryStatsResponse(BaseModel):
    pending_retries: int
    human_review_queue: int
    partial_passes: int
    total_in_recovery: int


# ==================== Helper Functions ====================

def get_stdout_preview(stdout: Optional[str], max_len: int = 500) -> Optional[str]:
    """Get a preview of stdout for display."""
    if not stdout:
        return None
    return stdout[:max_len] + "..." if len(stdout) > max_len else stdout


# ==================== Human Review Endpoints ====================

@router.get("/human-review/queue", response_model=List[HumanReviewJobResponse])
async def get_human_review_queue(
    session: Session = Depends(get_db)
):
    """
    Get all jobs waiting for human review.

    These are jobs that:
    - Exceeded max retries
    - Have status = HUMAN_REVIEW
    - Need manual intervention
    """
    service = RecoveryService(session)
    jobs = service.get_human_review_queue()

    return [
        HumanReviewJobResponse(
            id=job.id,
            bounty_id=job.bounty_id,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            failure_reason=job.failure_reason,
            failure_severity=job.failure_severity,
            created_at=job.created_at,
            updated_at=job.updated_at,
            original_runner_id=job.original_runner_id,
            stdout_preview=get_stdout_preview(job.stdout_log)
        )
        for job in jobs
    ]


@router.get("/human-review/{job_id}")
async def get_human_review_job(
    job_id: UUID,
    session: Session = Depends(get_db)
):
    """
    Get detailed info about a job in human review.

    Includes full logs and test results.
    """
    job = session.get(ComputeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != ComputeJobStatus.HUMAN_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Job status is {job.status}, not human_review"
        )

    return {
        "id": str(job.id),
        "bounty_id": job.bounty_id,
        "status": job.status,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "failure_reason": job.failure_reason,
        "failure_severity": job.failure_severity,
        "test_results": job.test_results,
        "total_tests": job.total_tests,
        "passed_tests": job.passed_tests,
        "failed_tests": job.failed_tests,
        "stdout_log": job.stdout_log,
        "stderr_log": job.stderr_log,
        "exit_code": job.exit_code,
        "warnings": job.warnings,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


@router.post("/human-review/{job_id}/approve")
async def approve_human_review(
    job_id: UUID,
    request: ApproveReviewRequest,
    session: Session = Depends(get_db)
):
    """
    Approve a job after human review.

    This will:
    1. Mark the job as COMPLETED
    2. Record the approval in job.warnings
    3. Trigger downstream success handling
    """
    service = RecoveryService(session)

    try:
        job = service.approve_human_review(
            job_id=job_id,
            reviewer_id=request.reviewer_id,
            notes=request.notes
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # TODO: Trigger downstream success handling
    # - Update bounty status
    # - Reward agent
    # - Notify relevant parties

    return {
        "success": True,
        "job_id": str(job_id),
        "status": job.status,
        "message": "Job approved after human review",
        "reviewer_id": str(request.reviewer_id)
    }


@router.post("/human-review/{job_id}/reject")
async def reject_human_review(
    job_id: UUID,
    request: RejectReviewRequest,
    session: Session = Depends(get_db)
):
    """
    Reject a job after human review.

    This will:
    1. Mark the job as FAILED
    2. Record the rejection reason
    3. Allow agent to retry (reset bounty status)
    """
    service = RecoveryService(session)

    try:
        job = service.reject_human_review(
            job_id=job_id,
            reviewer_id=request.reviewer_id,
            reason=request.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Reset bounty to allow retry
    # This is handled in the main submission flow

    return {
        "success": True,
        "job_id": str(job_id),
        "status": job.status,
        "message": "Job rejected after human review",
        "reason": request.reason,
        "reviewer_id": str(request.reviewer_id)
    }


# ==================== Recovery Stats ====================

@router.get("/stats", response_model=RecoveryStatsResponse)
async def get_recovery_stats(
    session: Session = Depends(get_db)
):
    """
    Get recovery statistics.

    Returns counts of jobs in various recovery states.
    """
    service = RecoveryService(session)
    return service.get_recovery_stats()


# ==================== Retry Management ====================

@router.get("/retry/queue")
async def get_retry_queue(
    ready_only: bool = False,
    session: Session = Depends(get_db)
):
    """
    Get jobs in retry queue.

    Args:
        ready_only: If True, only return jobs ready for retry now
    """
    service = RecoveryService(session)

    if ready_only:
        jobs = service.get_ready_retry_jobs()
    else:
        # Get all pending jobs with retry_count > 0
        from sqlmodel import select
        jobs = session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.PENDING,
                ComputeJob.retry_count > 0
            )
        ).all()

    return {
        "total": len(jobs),
        "jobs": [
            {
                "id": str(job.id),
                "bounty_id": job.bounty_id,
                "retry_count": job.retry_count,
                "max_retries": job.max_retries,
                "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
                "failure_reason": job.failure_reason,
            }
            for job in jobs
        ]
    }


@router.post("/retry/process")
async def process_retry_queue(
    session: Session = Depends(get_db)
):
    """
    Process jobs ready for retry.

    This is normally called by the scheduler, but can be triggered manually.
    """
    service = RecoveryService(session)
    stats = service.process_retry_queue()

    return {
        "success": True,
        **stats
    }


# ==================== Partial Pass Management ====================

@router.get("/partial-pass")
async def get_partial_pass_jobs(
    session: Session = Depends(get_db)
):
    """
    Get all jobs with partial pass status.

    These jobs passed >= 80% of tests but had some failures.
    """
    from sqlmodel import select

    jobs = session.exec(
        select(ComputeJob).where(
            ComputeJob.status == ComputeJobStatus.PARTIAL_PASS
        )
    ).all()

    return {
        "total": len(jobs),
        "jobs": [
            {
                "id": str(job.id),
                "bounty_id": job.bounty_id,
                "total_tests": job.total_tests,
                "passed_tests": job.passed_tests,
                "failed_tests": job.failed_tests,
                "pass_rate": f"{(job.passed_tests / job.total_tests * 100):.1f}%" if job.total_tests > 0 else "N/A",
                "warnings": job.warnings,
                "created_at": job.created_at.isoformat(),
            }
            for job in jobs
        ]
    }


@router.post("/partial-pass/{job_id}/accept")
async def accept_partial_pass(
    job_id: UUID,
    reviewer_id: UUID,
    notes: str = "",
    session: Session = Depends(get_db)
):
    """
    Accept a partial pass as complete.

    This is for cases where the failing tests are known issues
    or not critical for the submission.
    """
    job = session.get(ComputeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != ComputeJobStatus.PARTIAL_PASS:
        raise HTTPException(
            status_code=400,
            detail=f"Job status is {job.status}, not partial_pass"
        )

    # Upgrade to completed
    job.status = ComputeJobStatus.COMPLETED
    job.warnings = job.warnings or []
    job.warnings.append({
        "type": "partial_pass_accepted",
        "reviewer_id": str(reviewer_id),
        "notes": notes,
        "original_pass_rate": f"{job.passed_tests}/{job.total_tests}",
        "accepted_at": datetime.utcnow().isoformat()
    })

    session.commit()

    return {
        "success": True,
        "job_id": str(job_id),
        "status": job.status,
        "message": "Partial pass accepted as complete"
    }


@router.post("/partial-pass/{job_id}/reject")
async def reject_partial_pass(
    job_id: UUID,
    reviewer_id: UUID,
    reason: str,
    session: Session = Depends(get_db)
):
    """
    Reject a partial pass - require full fix.

    This resets the bounty to allow the agent to retry.
    """
    job = session.get(ComputeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != ComputeJobStatus.PARTIAL_PASS:
        raise HTTPException(
            status_code=400,
            detail=f"Job status is {job.status}, not partial_pass"
        )

    # Mark as failed
    job.status = ComputeJobStatus.FAILED
    job.failure_reason = f"Partial pass rejected: {reason}"
    job.warnings = job.warnings or []
    job.warnings.append({
        "type": "partial_pass_rejected",
        "reviewer_id": str(reviewer_id),
        "reason": reason,
        "rejected_at": datetime.utcnow().isoformat()
    })

    session.commit()

    return {
        "success": True,
        "job_id": str(job_id),
        "status": job.status,
        "message": "Partial pass rejected, requires full fix"
    }
