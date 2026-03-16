"""
Verification Service for AgentHub Self-Hosted Compute Network

Implements Zero-Trust verification:
1. Log validation - ensure stdout contains real test output
2. Random audit triggering - every Nth job
3. Audit execution - re-run job on trusted infrastructure
4. Ban mechanism - permanent bans for cheaters
"""

import random
import re
from datetime import datetime
from typing import Tuple

from sqlmodel import Session

from ..models.runner import (
    Runner,
    RunnerStatus,
    ComputeJob,
    AuditLog,
    AuditResult,
)


class VerificationService:
    """
    Zero-Trust verification for self-hosted runner results.
    """

    # Minimum characters required in stdout
    MIN_STDOUT_LENGTH = 50

    # Audit probability (1 in N jobs)
    AUDIT_INTERVAL = 10

    # Patterns that indicate real test output
    REAL_TEST_PATTERNS = [
        r"passed|failed|error",  # Test results
        r"\d+\s*(tests?|specs?|cases?)",  # Test counts
        r"(PASS|FAIL|ERROR|OK)",  # Status markers
        r"(pytest|jest|mocha|unittest|rspec|go test)",  # Test frameworks
        r"(assert|expect|should)",  # Assertion keywords
        r"(running|executing|starting)",  # Execution markers
        r"\d+\s*(ms|s|seconds?)",  # Timing info
    ]

    # Patterns that indicate fake/generated output
    FAKE_OUTPUT_PATTERNS = [
        r"^(success|ok|done|complete)\.?$",  # Too simple
        r"^\.+$",  # Just dots
        r"^[a-f0-9]{8,}$",  # Just hex
    ]

    @classmethod
    def validate_stdout(cls, stdout: str) -> Tuple[bool, str]:
        """
        Validate that stdout contains meaningful test output.

        Returns:
            (is_valid, reason)
        """
        if not stdout:
            return False, "stdout is empty"

        if len(stdout.strip()) < cls.MIN_STDOUT_LENGTH:
            return False, f"stdout too short (min {cls.MIN_STDOUT_LENGTH} chars)"

        # Check for fake patterns
        for pattern in cls.FAKE_OUTPUT_PATTERNS:
            if re.match(pattern, stdout.strip(), re.IGNORECASE):
                return False, "stdout appears to be fake/generated"

        # Check for real test patterns
        real_pattern_count = 0
        for pattern in cls.REAL_TEST_PATTERNS:
            if re.search(pattern, stdout, re.IGNORECASE):
                real_pattern_count += 1

        # Require at least 2 real patterns for confidence
        if real_pattern_count < 2:
            return False, "stdout lacks typical test output patterns"

        return True, "stdout validated"

    @classmethod
    def should_trigger_audit(cls, runner: Runner) -> bool:
        """
        Determine if an audit should be triggered.

        Strategy:
        - Every Nth job (configurable)
        - Random 5% chance for all other jobs
        - Always audit if runner reputation is low
        """
        # Always audit low reputation runners
        if runner.reputation_score < 50:
            return True

        # Every Nth job
        if runner.total_jobs_completed > 0:
            if runner.total_jobs_completed % cls.AUDIT_INTERVAL == 0:
                return True

        # Random 5% chance
        return random.random() < 0.05

    @classmethod
    def create_audit(
        cls,
        session: Session,
        job: ComputeJob,
        reason: str = "random"
    ) -> AuditLog:
        """
        Create an audit log entry for a job.

        The audit will be executed asynchronously by a trusted runner.
        """
        audit = AuditLog(
            job_id=job.id,
            runner_id=job.runner_id,
            original_stdout=job.stdout_log,
            original_exit_code=job.exit_code,
            original_passed=job.passed,
            reason=reason,
            status="pending"
        )

        session.add(audit)
        session.commit()
        session.refresh(audit)

        return audit

    @classmethod
    def execute_audit(
        cls,
        original_stdout: str,
        original_exit_code: int,
        audited_stdout: str,
        audited_exit_code: int
    ) -> Tuple[AuditResult, str]:
        """
        Compare original and audited results.

        Returns:
            (result, explanation)
        """
        # If exit codes match and outputs are similar, trust the runner
        if original_exit_code == audited_exit_code:
            # Check output similarity (at least 50% overlap in key patterns)
            original_patterns = set(re.findall(r'\b\w+\b', original_stdout.lower()))
            audited_patterns = set(re.findall(r'\b\w+\b', audited_stdout.lower()))

            if original_patterns and audited_patterns:
                overlap = len(original_patterns & audited_patterns) / len(audited_patterns)
                if overlap >= 0.5:
                    return AuditResult.PASSED, f"Output matches (similarity: {overlap:.0%})"

            return AuditResult.PASSED, "Exit codes match"

        # Exit codes differ - potential cheating
        if original_exit_code == 0 and audited_exit_code != 0:
            return AuditResult.FAILED, (
                f"Runner reported success (exit={original_exit_code}) "
                f"but audit shows failure (exit={audited_exit_code})"
            )

        return AuditResult.SUSPICIOUS, (
            f"Exit code mismatch: original={original_exit_code}, "
            f"audited={audited_exit_code}"
        )

    @classmethod
    def apply_audit_result(
        cls,
        session: Session,
        audit: AuditLog,
        result: AuditResult,
        explanation: str,
        audited_stdout: str,
        audited_exit_code: int
    ) -> None:
        """
        Apply audit result: update runner reputation, ban if needed.
        """
        audit.status = "completed"
        audit.audited_stdout = audited_stdout
        audit.audited_exit_code = audited_exit_code
        audit.result = result
        audit.explanation = explanation
        audit.audited_at = datetime.utcnow()

        session.add(audit)

        # Get runner
        runner = session.get(Runner, audit.runner_id)
        if not runner:
            return

        if result == AuditResult.PASSED:
            # Increase reputation
            runner.reputation_score = min(100, runner.reputation_score + 2)

        elif result == AuditResult.FAILED:
            # Major reputation hit
            runner.reputation_score = max(0, runner.reputation_score - 30)

            # Ban if reputation drops too low
            if runner.reputation_score < 20:
                runner.status = RunnerStatus.BANNED
                runner.banned_at = datetime.utcnow()
                runner.banned_reason = f"Audit failure: {explanation}"

        elif result == AuditResult.SUSPICIOUS:
            # Minor reputation hit
            runner.reputation_score = max(0, runner.reputation_score - 10)

        session.add(runner)
        session.commit()
