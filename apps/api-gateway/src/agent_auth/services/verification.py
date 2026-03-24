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
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select

from ..models import Agent
from ..models.platform import MembershipStatus, Repo, RepoMember, RepoRole
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

    # Baseline random chance when no risk signal is present
    BASE_RANDOM_AUDIT_PROB = 0.05

    # Dynamic risk thresholds
    FORCE_AUDIT_SCORE = 80
    ELEVATED_AUDIT_SCORE = 50

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
    def _normalize_agent_role(cls, role: Any) -> str:
        if role is None:
            return ""
        if hasattr(role, "value"):
            return str(role.value).lower()
        return str(role).lower()

    @classmethod
    def _resolve_job_risk_context(cls, session: Session, job: ComputeJob) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "repo_membership_role": "",
            "agent_role": "",
            "is_sensitive_path": False,
            "has_runner_mismatch_signal": False,
            "risk_reasons": [],
        }

        requester_agent_id = job.requester_agent_id
        if requester_agent_id:
            agent = session.get(Agent, requester_agent_id)
            if agent:
                context["agent_role"] = cls._normalize_agent_role(getattr(agent, "role", None))

        repo_uuid = job.repo_id
        if requester_agent_id and not repo_uuid:
            repo_name = (job.env_vars or {}).get("repo_name")
            if repo_name:
                repo = session.exec(select(Repo).where(Repo.name == repo_name)).first()
                if repo:
                    repo_uuid = repo.id

        if requester_agent_id and repo_uuid:
            membership = session.exec(
                select(RepoMember).where(
                    RepoMember.repo_id == repo_uuid,
                    RepoMember.agent_id == requester_agent_id,
                    RepoMember.status == MembershipStatus.ACTIVE,
                )
            ).first()
            if membership:
                context["repo_membership_role"] = cls._normalize_agent_role(getattr(membership, "role", None))

        env_vars = job.env_vars or {}
        touched_paths_raw = env_vars.get("touched_paths") or env_vars.get("file_paths")
        if touched_paths_raw:
            paths: list[str] = []
            if isinstance(touched_paths_raw, str):
                paths = [p.strip() for p in touched_paths_raw.split(",") if p.strip()]
            elif isinstance(touched_paths_raw, list):
                paths = [str(p).strip() for p in touched_paths_raw if str(p).strip()]

            sensitive_prefixes = (
                "infra/",
                ".github/workflows/",
                "apps/api-gateway/src/meta/",
                "services/git-core/",
            )
            if any(path.startswith(sensitive_prefixes) for path in paths):
                context["is_sensitive_path"] = True

        if job.used_fallback or (job.retry_count and job.retry_count > 0) or bool(job.failure_severity):
            context["has_runner_mismatch_signal"] = True

        return context

    @classmethod
    def _compute_audit_risk_score(
        cls,
        runner: Runner,
        job: ComputeJob,
        context: Dict[str, Any],
    ) -> Tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        if runner.reputation_score < 50:
            score += 50
            reasons.append("low_reputation")
        elif runner.reputation_score < 70:
            score += 25
            reasons.append("medium_reputation")

        if runner.audit_failures > 0:
            score += min(50, runner.audit_failures * 30)
            reasons.append("historical_audit_failures")
            if runner.audit_failures >= 2:
                score += 40
                reasons.append("repeat_audit_failures")

        if context.get("has_runner_mismatch_signal"):
            score += 20
            reasons.append("runtime_mismatch_signal")

        if context.get("is_sensitive_path"):
            score += 20
            reasons.append("sensitive_path")

        repo_role = context.get("repo_membership_role") or ""
        has_high_privilege_repo_role = repo_role in {RepoRole.ARCHITECT.value, RepoRole.EXECUTOR.value}
        if has_high_privilege_repo_role:
            score += 15
            reasons.append("high_privilege_repo_role")

        agent_role = context.get("agent_role") or ""
        has_high_privilege_agent_role = agent_role in {RepoRole.ARCHITECT.value, RepoRole.EXECUTOR.value}
        if has_high_privilege_agent_role:
            score += 10
            reasons.append("high_privilege_agent_role")

        if context.get("is_sensitive_path") and (has_high_privilege_repo_role or has_high_privilege_agent_role):
            score += 45
            reasons.append("sensitive_privileged_combination")

        return min(100, score), reasons

    @classmethod
    def should_trigger_audit(
        cls,
        runner: Runner,
        session: Session,
        job: ComputeJob,
    ) -> Tuple[bool, str]:
        """
        Determine if an audit should be triggered using dynamic risk signals.

        Returns:
            (triggered, reason)
        """
        context = cls._resolve_job_risk_context(session, job)
        risk_score, risk_reasons = cls._compute_audit_risk_score(runner, job, context)

        if risk_score >= cls.FORCE_AUDIT_SCORE:
            reason = "risk_forced:" + ",".join(risk_reasons or ["high_risk"])
            return True, reason

        if runner.total_jobs_completed > 0 and runner.total_jobs_completed % cls.AUDIT_INTERVAL == 0:
            reason = "interval_nth"
            if risk_score >= cls.ELEVATED_AUDIT_SCORE and risk_reasons:
                reason += ":" + ",".join(risk_reasons)
            return True, reason

        base_prob = cls.BASE_RANDOM_AUDIT_PROB
        bonus_prob = min(0.35, risk_score / 250.0)
        audit_prob = min(0.8, base_prob + bonus_prob)
        if random.random() < audit_prob:
            if risk_reasons:
                return True, "risk_weighted_random:" + ",".join(risk_reasons)
            return True, "random"

        return False, "no_audit"

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
            original_test_command=job.test_command,
            original_code_commit=job.code_commit,
            original_env_fingerprint=cls._compute_env_fingerprint(job.env_vars),
            reason=reason,
            status="pending"
        )

        session.add(audit)
        session.commit()
        session.refresh(audit)

        return audit

    @classmethod
    def _normalize_command(cls, command: Optional[str]) -> str:
        return " ".join((command or "").strip().split())

    @classmethod
    def _compute_env_fingerprint(cls, env_vars: Optional[dict]) -> str:
        if not env_vars:
            return ""
        normalized_items = [f"{k}={env_vars[k]}" for k in sorted(env_vars.keys())]
        return "|".join(normalized_items)

    @classmethod
    def execute_audit(
        cls,
        original_stdout: str,
        original_exit_code: int,
        audited_stdout: str,
        audited_exit_code: int,
        original_test_command: Optional[str],
        audited_test_command: Optional[str],
        original_code_commit: Optional[str],
        audited_code_commit: Optional[str],
        original_env_fingerprint: Optional[str],
        audited_env_fingerprint: Optional[str],
    ) -> Tuple[AuditResult, str]:
        """
        Compare original and audited results.

        Returns:
            (result, explanation)
        """
        normalized_original_cmd = cls._normalize_command(original_test_command)
        normalized_audited_cmd = cls._normalize_command(audited_test_command)
        if normalized_original_cmd != normalized_audited_cmd:
            return AuditResult.FAILED, "Execution fingerprint mismatch: test_command differs"

        normalized_original_commit = (original_code_commit or "").strip()
        normalized_audited_commit = (audited_code_commit or "").strip()
        if normalized_original_commit != normalized_audited_commit:
            return AuditResult.FAILED, "Execution fingerprint mismatch: code_commit differs"

        normalized_original_env = (original_env_fingerprint or "").strip()
        normalized_audited_env = (audited_env_fingerprint or "").strip()
        if normalized_original_env != normalized_audited_env:
            return AuditResult.FAILED, "Execution fingerprint mismatch: env_fingerprint differs"

        # If execution fingerprints are identical, then compare runtime outcomes.
        if original_exit_code == audited_exit_code:
            # Check output similarity (at least 50% overlap in key patterns)
            original_patterns = set(re.findall(r'\b\w+\b', original_stdout.lower()))
            audited_patterns = set(re.findall(r'\b\w+\b', audited_stdout.lower()))

            if original_patterns and audited_patterns:
                overlap = len(original_patterns & audited_patterns) / len(audited_patterns)
                if overlap >= 0.5:
                    return AuditResult.PASSED, f"Output matches (similarity: {overlap:.0%})"

            return AuditResult.SUSPICIOUS, "Exit codes match but output similarity is low"

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
        audited_exit_code: int,
        audited_test_command: Optional[str] = None,
        audited_code_commit: Optional[str] = None,
        audited_env_fingerprint: Optional[str] = None,
    ) -> None:
        """
        Apply audit result: update runner reputation, ban if needed.
        """
        audit.status = "completed"
        audit.audited_stdout = audited_stdout
        audit.audited_exit_code = audited_exit_code
        audit.audited_test_command = audited_test_command
        audit.audited_code_commit = audited_code_commit
        audit.audited_env_fingerprint = audited_env_fingerprint
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
