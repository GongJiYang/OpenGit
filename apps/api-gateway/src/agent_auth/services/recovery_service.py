"""
Failure Recovery Service

Implements intelligent failure recovery with:
- Exponential backoff retry policy
- Human review fallback
- Partial success detection
- Failure severity classification

Retry Policy:
├─ max_retries: 3 (default)
├─ backoff: exponential (60s → 120s → 240s)
└─ fallback: human_review when retries exhausted
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any
from uuid import UUID
from sqlmodel import Session, select
import logging

from ..models.runner import (
    ComputeJob,
    ComputeJobStatus,
    Runner,
    RunnerStatus,
)

logger = logging.getLogger(__name__)


class FailureSeverity:
    """Failure severity levels."""
    CRITICAL = "critical"    # Blocking - must fix before proceeding
    WARNING = "warning"      # Non-blocking - continue with warning
    INFO = "info"            # Informational - can be ignored


class RecoveryService:
    """
    Service for handling job failures with retry logic,
    human review fallback, and partial success detection.
    """

    # Default configuration
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BASE_DELAY = 60  # seconds
    DEFAULT_BACKOFF_FACTOR = 2.0
    DEFAULT_PARTIAL_PASS_THRESHOLD = 0.8

    def __init__(self, session: Session):
        self.session = session

    # ==================== Retry Policy ====================

    def calculate_next_retry_time(self, job: ComputeJob) -> datetime:
        """
        Calculate next retry time using exponential backoff.

        Formula: base_delay * (backoff_factor ^ retry_count)
        Example: 60s → 120s → 240s → 480s
        """
        base_delay = job.retry_base_delay_seconds or self.DEFAULT_RETRY_BASE_DELAY
        backoff_factor = job.retry_backoff_factor or self.DEFAULT_BACKOFF_FACTOR

        # Calculate delay with exponential backoff
        delay_seconds = base_delay * (backoff_factor ** job.retry_count)

        # Cap at 1 hour maximum
        delay_seconds = min(delay_seconds, 3600)

        return datetime.utcnow() + timedelta(seconds=delay_seconds)

    def should_retry(self, job: ComputeJob) -> bool:
        """Determine if a job should be retried."""
        return job.retry_count < (job.max_retries or self.DEFAULT_MAX_RETRIES)

    def handle_job_failure(
        self,
        job: ComputeJob,
        failure_reason: str,
        severity: str = FailureSeverity.CRITICAL
    ) -> Tuple[str, str]:
        """
        Handle a failed job with retry logic.

        This is the main entry point for failure handling.

        Returns:
            Tuple of (action_taken, next_status)
            - action_taken: "retry" | "human_review" | "partial_pass" | "failed"
            - next_status: The new ComputeJobStatus
        """
        # Record failure info
        job.failure_reason = failure_reason
        job.failure_severity = severity

        # Check if we can retry
        if severity == FailureSeverity.CRITICAL and self.should_retry(job):
            # Schedule retry
            job.retry_count += 1
            job.next_retry_at = self.calculate_next_retry_time(job)
            job.status = ComputeJobStatus.PENDING
            job.runner_id = None
            job.assigned_at = None
            job.started_at = None

            return "retry", ComputeJobStatus.PENDING

        # Check if max retries exceeded
        if severity == FailureSeverity.CRITICAL and not self.should_retry(job):
            # Escalate to human review
            job.status = ComputeJobStatus.HUMAN_REVIEW
            job.requires_manual_review = True

            return "human_review", ComputeJobStatus.HUMAN_REVIEW

        # Non-critical failure - check for partial pass
        if self._check_partial_pass(job):
            job.status = ComputeJobStatus.PARTIAL_PASS
            return "partial_pass", ComputeJobStatus.PARTIAL_PASS

        # Default to failed
        job.status = ComputeJobStatus.FAILED
        return "failed", ComputeJobStatus.FAILED

    # ==================== Partial Success ====================

    def _check_partial_pass(self, job: ComputeJob) -> bool:
        """
        Check if job qualifies for partial pass.

        A job is a partial pass if:
        - At least 80% of tests passed (configurable)
        - No critical failures
        """
        if job.total_tests == 0:
            return False

        threshold = job.partial_pass_threshold or self.DEFAULT_PARTIAL_PASS_THRESHOLD
        pass_rate = job.passed_tests / job.total_tests

        return pass_rate >= threshold

    def update_test_results(
        self,
        job: ComputeJob,
        total: int,
        passed: int,
        failed: int,
        skipped: int = 0
    ) -> str:
        """
        Update test results and determine final status.

        Returns:
            Final job status string
        """
        job.total_tests = total
        job.passed_tests = passed
        job.failed_tests = failed
        job.skipped_tests = skipped

        # Calculate pass rate
        if total > 0:
            pass_rate = passed / total
            threshold = job.partial_pass_threshold or self.DEFAULT_PARTIAL_PASS_THRESHOLD

            if failed == 0:
                return ComputeJobStatus.COMPLETED
            elif pass_rate >= threshold:
                return ComputeJobStatus.PARTIAL_PASS
            else:
                return ComputeJobStatus.FAILED

        return ComputeJobStatus.FAILED

    # ==================== Retry Queue Processing ====================

    def get_ready_retry_jobs(self) -> List[ComputeJob]:
        """
        Get jobs that are ready for retry.

        Returns jobs where:
        - Status is PENDING
        - retry_count > 0
        - next_retry_at has passed
        """
        now = datetime.utcnow()

        jobs = self.session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.PENDING,
                ComputeJob.retry_count > 0,
                ComputeJob.next_retry_at <= now
            )
        ).all()

        return list(jobs)

    def process_retry_queue(self) -> Dict[str, int]:
        """
        Process jobs ready for retry.

        This should be called by the scheduler.

        Returns:
            Statistics about processed jobs
        """
        ready_jobs = self.get_ready_retry_jobs()

        stats = {
            "retries_processed": 0,
            "retries_ready": len(ready_jobs)
        }

        for job in ready_jobs:
            # Clear retry time - job assignment logic will handle it
            job.next_retry_at = None
            stats["retries_processed"] += 1

        self.session.commit()
        return stats

    # ==================== Failure Classification ====================

    def classify_failure(
        self,
        exit_code: int,
        stderr: str,
        test_results: Dict[str, Any]
    ) -> Tuple[str, str]:
        """
        Classify the severity of a failure.

        Returns:
            Tuple of (severity, reason)
        """
        # Check for critical failures
        if exit_code != 0:
            # Check if it's a timeout
            if stderr and "timeout" in stderr.lower():
                return FailureSeverity.CRITICAL, "Execution timeout"

            # Check if it's an infrastructure error
            infra_errors = ["docker", "container", "memory", "disk", "network"]
            for err in infra_errors:
                if stderr and err in stderr.lower():
                    return FailureSeverity.WARNING, f"Infrastructure issue: {err}"

            # Check if tests actually ran
            if test_results.get("total", 0) > 0:
                failed = test_results.get("failed", 0)
                total = test_results.get("total", 0)
                if failed > 0 and failed < total:
                    return FailureSeverity.WARNING, f"Partial test failure: {failed}/{total} failed"

            return FailureSeverity.CRITICAL, f"Process exited with code {exit_code}"

        # Exit code 0 but check for warnings in output
        if test_results.get("warnings"):
            return FailureSeverity.INFO, "Tests passed with warnings"

        return FailureSeverity.INFO, "Success"

    # ==================== Fallback Execution ====================

    def get_fallback_runner(self, job: ComputeJob) -> Optional[Runner]:
        """
        Get a fallback runner for a failed job.

        Priority:
        1. Try another dedicated runner
        2. Fall back to shared_local runner
        """
        # Try to find an available dedicated runner
        dedicated_runner = self.session.exec(
            select(Runner).where(
                Runner.status == RunnerStatus.ONLINE,
                Runner.runner_type == "dedicated",
                Runner.current_job_id == None,
                Runner.id != job.runner_id if job.runner_id else True
            )
        ).first()

        if dedicated_runner:
            logger.info(f"Found fallback dedicated runner {dedicated_runner.id}")
            return dedicated_runner

        # Fall back to shared_local runner
        shared_runner = self.session.exec(
            select(Runner).where(
                Runner.status == RunnerStatus.ONLINE,
                Runner.runner_type == "shared_local",
                Runner.current_job_id == None
            )
        ).first()

        if shared_runner:
            logger.info(f"Found fallback shared_local runner {shared_runner.id}")
            return shared_runner

        logger.warning("No fallback runner available")
        return None

    def assign_to_fallback(self, job: ComputeJob) -> Tuple[bool, str]:
        """
        Assign job to a fallback runner.

        Returns:
            Tuple of (success, message)
        """
        fallback_runner = self.get_fallback_runner(job)

        if not fallback_runner:
            return False, "No fallback runner available"

        # Record original runner
        if job.runner_id and not job.original_runner_id:
            job.original_runner_id = job.runner_id

        # Assign to fallback
        job.runner_id = fallback_runner.id
        job.status = ComputeJobStatus.ASSIGNED
        job.assigned_at = datetime.utcnow()
        job.started_at = None
        job.used_fallback = True

        # Update runner status
        fallback_runner.status = RunnerStatus.BUSY
        fallback_runner.current_job_id = job.id

        self.session.commit()

        logger.info(f"Assigned job {job.id} to fallback runner {fallback_runner.id}")
        return True, f"Assigned to fallback runner {fallback_runner.id}"

    # ==================== Human Review ====================

    def get_human_review_queue(self) -> List[ComputeJob]:
        """Get all jobs waiting for human review."""
        jobs = self.session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.HUMAN_REVIEW
            )
        ).all()
        return list(jobs)

    def approve_human_review(
        self,
        job_id: UUID,
        reviewer_id: UUID,
        notes: str = ""
    ) -> ComputeJob:
        """
        Approve a job after human review.

        Args:
            job_id: The job to approve
            reviewer_id: ID of the human reviewer
            notes: Review notes

        Returns:
            Updated job
        """
        job = self.session.get(ComputeJob, job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status != ComputeJobStatus.HUMAN_REVIEW:
            raise ValueError("Job is not in human review status")

        # Mark as completed with manual approval
        job.status = ComputeJobStatus.COMPLETED
        job.requires_manual_review = False
        job.completed_at = datetime.utcnow()
        job.warnings = job.warnings or []
        job.warnings.append({
            "type": "manual_approval",
            "reviewer_id": str(reviewer_id),
            "notes": notes,
            "approved_at": datetime.utcnow().isoformat()
        })

        self.session.commit()
        return job

    def reject_human_review(
        self,
        job_id: UUID,
        reviewer_id: UUID,
        reason: str
    ) -> ComputeJob:
        """
        Reject a job after human review.

        Args:
            job_id: The job to reject
            reviewer_id: ID of the human reviewer
            reason: Rejection reason

        Returns:
            Updated job
        """
        job = self.session.get(ComputeJob, job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status != ComputeJobStatus.HUMAN_REVIEW:
            raise ValueError("Job is not in human review status")

        # Mark as failed with manual rejection
        job.status = ComputeJobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.failure_reason = f"Human review rejected: {reason}"
        job.warnings = job.warnings or []
        job.warnings.append({
            "type": "manual_rejection",
            "reviewer_id": str(reviewer_id),
            "reason": reason,
            "rejected_at": datetime.utcnow().isoformat()
        })

        self.session.commit()
        return job

    # ==================== Stats & Reporting ====================

    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get statistics about failure recovery."""
        # Count jobs by status
        pending_retries = len(self.session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.PENDING,
                ComputeJob.retry_count > 0
            )
        ).all())

        human_review = len(self.session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.HUMAN_REVIEW
            )
        ).all())

        partial_passes = len(self.session.exec(
            select(ComputeJob).where(
                ComputeJob.status == ComputeJobStatus.PARTIAL_PASS
            )
        ).all())

        return {
            "pending_retries": pending_retries,
            "human_review_queue": human_review,
            "partial_passes": partial_passes,
            "total_in_recovery": pending_retries + human_review,
        }
