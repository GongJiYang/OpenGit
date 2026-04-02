import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .schemas import (
    PullRequestSpec,
    SUPPORTED_TRACE_COMMIT_PROTOCOL_VERSIONS,
    TRACE_COMMIT_MAX_AGENT_ID_LENGTH,
    TRACE_COMMIT_MAX_CONTEXT_FILE_PATHS,
    TRACE_COMMIT_MAX_DIFF_SUMMARY_LENGTH,
    TRACE_COMMIT_MAX_FILE_PATH_LENGTH,
    TRACE_COMMIT_MAX_INTENT_CATEGORY_LENGTH,
    TRACE_COMMIT_MAX_INTENT_DESCRIPTION_LENGTH,
    TRACE_COMMIT_MAX_INTENT_VECTOR_DIMS,
    TRACE_COMMIT_MAX_MODEL_NAME_LENGTH,
    TRACE_COMMIT_MAX_REASONING_STEPS,
    TRACE_COMMIT_MAX_REASONING_STEP_LENGTH,
    TRACE_COMMIT_MAX_REJECTED_ALTERNATIVES,
    TRACE_COMMIT_MAX_REJECTED_ALTERNATIVE_LENGTH,
    TraceCommit,
)
from .signing import compute_binding_hash, compute_reasoning_hash


class TraceValidator:
    """
    Enforces the 'Trace-Commit' protocol.
    Ensures that every commit has a valid reasoning trace and intent.
    """

    SHA1_HEX_RE = re.compile(r"^[0-9a-f]{40}$")
    SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
    DOC_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s]+$")
    ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

    MAX_DIFF_SUMMARY_LENGTH = TRACE_COMMIT_MAX_DIFF_SUMMARY_LENGTH
    MAX_REASONING_STEPS = TRACE_COMMIT_MAX_REASONING_STEPS
    MAX_REASONING_STEP_LENGTH = TRACE_COMMIT_MAX_REASONING_STEP_LENGTH
    MAX_REJECTED_ALTERNATIVES = TRACE_COMMIT_MAX_REJECTED_ALTERNATIVES
    MAX_REJECTED_ALTERNATIVE_LENGTH = TRACE_COMMIT_MAX_REJECTED_ALTERNATIVE_LENGTH
    MAX_CONTEXT_FILE_PATHS = TRACE_COMMIT_MAX_CONTEXT_FILE_PATHS
    MAX_FILE_PATH_LENGTH = TRACE_COMMIT_MAX_FILE_PATH_LENGTH
    MAX_INTENT_DESCRIPTION_LENGTH = TRACE_COMMIT_MAX_INTENT_DESCRIPTION_LENGTH
    MAX_INTENT_CATEGORY_LENGTH = TRACE_COMMIT_MAX_INTENT_CATEGORY_LENGTH
    MAX_INTENT_VECTOR_DIMS = TRACE_COMMIT_MAX_INTENT_VECTOR_DIMS
    MAX_MODEL_NAME_LENGTH = TRACE_COMMIT_MAX_MODEL_NAME_LENGTH
    MAX_AGENT_ID_LENGTH = TRACE_COMMIT_MAX_AGENT_ID_LENGTH
    MAX_TIMESTAMP_FUTURE_SKEW = timedelta(minutes=5)
    MAX_TIMESTAMP_AGE = timedelta(days=3650)

    @staticmethod
    def _is_timezone_aware(ts: datetime) -> bool:
        return ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None

    @staticmethod
    def _validate_sha1(value: str, field_name: str) -> None:
        if not TraceValidator.SHA1_HEX_RE.fullmatch(value.lower()):
            raise ValueError(f"Protocol Violation: '{field_name}' must be 40-char sha1 hex.")

    @staticmethod
    def _validate_sha256(value: str, field_name: str) -> None:
        if not TraceValidator.SHA256_HEX_RE.fullmatch(value.lower()):
            raise ValueError(f"Protocol Violation: '{field_name}' must be 64-char sha256 hex.")

    @staticmethod
    def validate_commit(
        commit_data: Dict[str, Any],
        *,
        expected_commit_sha: Optional[str] = None,
        require_commit_sha: bool = False,
        require_parent_sha: bool = False,
        require_timezone_aware_timestamp: bool = False,
    ) -> TraceCommit:
        """
        Validates raw dictionary data against the TraceCommit schema.
        Raises ValueError if validation fails or logic rules are violated.
        """
        try:
            # 1. Schema Validation
            commit = TraceCommit(**commit_data)
        except ValidationError as e:
            raise ValueError(f"Schema Validation Failed: {e}")

        # 2. Logic Validation

        # Rule 1: protocol_version must be explicitly present and supported
        if "protocol_version" not in commit_data:
            raise ValueError("Protocol Violation: 'protocol_version' is missing.")
        if not commit.protocol_version:
            raise ValueError("Protocol Violation: 'protocol_version' is missing.")
        if commit.protocol_version not in SUPPORTED_TRACE_COMMIT_PROTOCOL_VERSIONS:
            raise ValueError("Protocol Violation: unsupported 'protocol_version'.")

        # Rule 2: diff_summary quality and limits
        if not commit.diff_summary or not commit.diff_summary.strip():
            raise ValueError("Protocol Violation: 'diff_summary' cannot be empty.")
        if len(commit.diff_summary.strip()) < 10:
            raise ValueError("Protocol Violation: 'diff_summary' must be at least 10 characters.")
        if len(commit.diff_summary) > TraceValidator.MAX_DIFF_SUMMARY_LENGTH:
            raise ValueError(
                f"Protocol Violation: 'diff_summary' exceeds max length {TraceValidator.MAX_DIFF_SUMMARY_LENGTH}."
            )

        # Rule 3: Reasoning Trace structure and limits
        if not commit.reasoning_trace or len(commit.reasoning_trace) == 0:
            raise ValueError("Protocol Violation: 'reasoning_trace' cannot be empty. Agents must explain *why*.")
        if len(commit.reasoning_trace) > TraceValidator.MAX_REASONING_STEPS:
            raise ValueError(f"Protocol Violation: 'reasoning_trace' exceeds max steps {TraceValidator.MAX_REASONING_STEPS}.")
        for step in commit.reasoning_trace:
            if not isinstance(step, str) or not step.strip():
                raise ValueError("Protocol Violation: each item in 'reasoning_trace' must be a non-empty string.")
            if len(step) > TraceValidator.MAX_REASONING_STEP_LENGTH:
                raise ValueError(
                    f"Protocol Violation: reasoning step exceeds max length {TraceValidator.MAX_REASONING_STEP_LENGTH}."
                )

        # Rule 4: Intent constraints
        if not commit.intent.description or not commit.intent.description.strip():
            raise ValueError("Protocol Violation: 'intent.description' is missing.")
        if len(commit.intent.description) > TraceValidator.MAX_INTENT_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Protocol Violation: 'intent.description' exceeds max length {TraceValidator.MAX_INTENT_DESCRIPTION_LENGTH}."
            )
        if commit.intent.category is not None:
            if not commit.intent.category.strip():
                raise ValueError("Protocol Violation: 'intent.category' cannot be blank when present.")
            if len(commit.intent.category) > TraceValidator.MAX_INTENT_CATEGORY_LENGTH:
                raise ValueError(
                    f"Protocol Violation: 'intent.category' exceeds max length {TraceValidator.MAX_INTENT_CATEGORY_LENGTH}."
                )
        if not commit.intent.vector:
            raise ValueError("Protocol Violation: 'intent.vector' cannot be empty.")
        if len(commit.intent.vector) > TraceValidator.MAX_INTENT_VECTOR_DIMS:
            raise ValueError(
                f"Protocol Violation: 'intent.vector' exceeds max length {TraceValidator.MAX_INTENT_VECTOR_DIMS}."
            )

        # Rule 5: rejected_alternatives quality and limits
        if not commit.rejected_alternatives or len(commit.rejected_alternatives) == 0:
            raise ValueError("Protocol Violation: 'rejected_alternatives' cannot be empty.")
        if len(commit.rejected_alternatives) > TraceValidator.MAX_REJECTED_ALTERNATIVES:
            raise ValueError(
                f"Protocol Violation: 'rejected_alternatives' exceeds max items {TraceValidator.MAX_REJECTED_ALTERNATIVES}."
            )
        for alt in commit.rejected_alternatives:
            if not isinstance(alt, str) or not alt.strip():
                raise ValueError("Protocol Violation: each item in 'rejected_alternatives' must be a non-empty string.")
            if len(alt) > TraceValidator.MAX_REJECTED_ALTERNATIVE_LENGTH:
                raise ValueError(
                    "Protocol Violation: rejected alternative exceeds max length "
                    f"{TraceValidator.MAX_REJECTED_ALTERNATIVE_LENGTH}."
                )

        # Rule 6: context snapshot constraints
        file_paths = commit.context_snapshot.file_paths
        if not file_paths:
            raise ValueError("Protocol Violation: 'context_snapshot.file_paths' cannot be empty.")
        if len(file_paths) > TraceValidator.MAX_CONTEXT_FILE_PATHS:
            raise ValueError(
                f"Protocol Violation: 'context_snapshot.file_paths' exceeds max items {TraceValidator.MAX_CONTEXT_FILE_PATHS}."
            )
        for p in file_paths:
            if not isinstance(p, str) or not p.strip():
                raise ValueError("Protocol Violation: each file path must be a non-empty string.")
            if len(p) > TraceValidator.MAX_FILE_PATH_LENGTH:
                raise ValueError(
                    f"Protocol Violation: file path exceeds max length {TraceValidator.MAX_FILE_PATH_LENGTH}."
                )
            normalized = p.replace("\\", "/")
            if normalized.startswith("/"):
                raise ValueError("Protocol Violation: absolute file paths are not allowed.")
            if "\x00" in normalized:
                raise ValueError("Protocol Violation: file path contains NUL byte.")
            if any(part == ".." for part in normalized.split("/")):
                raise ValueError("Protocol Violation: file path traversal segment '..' is not allowed.")

        # Rule 6.1: attestable context evidence constraints
        for ref in commit.context_snapshot.doc_references:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("Protocol Violation: each 'context_snapshot.doc_references' item must be a non-empty string.")
            normalized_ref = ref.strip()
            if len(normalized_ref) > TraceValidator.MAX_FILE_PATH_LENGTH:
                raise ValueError(
                    "Protocol Violation: 'context_snapshot.doc_references' item exceeds max length "
                    f"{TraceValidator.MAX_FILE_PATH_LENGTH}."
                )
            if not TraceValidator.DOC_REFERENCE_RE.fullmatch(normalized_ref):
                raise ValueError(
                    "Protocol Violation: 'context_snapshot.doc_references' item must use an attestable URI format "
                    "(scheme://reference)."
                )

        for env_name in commit.context_snapshot.env_vars_accessed:
            if not isinstance(env_name, str) or not env_name.strip():
                raise ValueError("Protocol Violation: each 'context_snapshot.env_vars_accessed' item must be a non-empty string.")
            normalized_env = env_name.strip()
            if len(normalized_env) > 128:
                raise ValueError("Protocol Violation: 'context_snapshot.env_vars_accessed' item exceeds max length 128.")
            if not TraceValidator.ENV_VAR_NAME_RE.fullmatch(normalized_env):
                raise ValueError(
                    "Protocol Violation: 'context_snapshot.env_vars_accessed' item must be uppercase env var name "
                    "([A-Z][A-Z0-9_]*)."
                )

        # Rule 7: author identity constraints
        if not commit.author.agent_id or not commit.author.agent_id.strip():
            raise ValueError("Protocol Violation: 'author.agent_id' is missing.")
        if len(commit.author.agent_id) > TraceValidator.MAX_AGENT_ID_LENGTH:
            raise ValueError(f"Protocol Violation: 'author.agent_id' exceeds max length {TraceValidator.MAX_AGENT_ID_LENGTH}.")
        if any(ch.isspace() for ch in commit.author.agent_id):
            raise ValueError("Protocol Violation: 'author.agent_id' cannot contain whitespace.")

        if not commit.author.model_name or not commit.author.model_name.strip():
            raise ValueError("Protocol Violation: 'author.model_name' is missing.")
        if len(commit.author.model_name) > TraceValidator.MAX_MODEL_NAME_LENGTH:
            raise ValueError(
                f"Protocol Violation: 'author.model_name' exceeds max length {TraceValidator.MAX_MODEL_NAME_LENGTH}."
            )

        # Rule 8: timestamp awareness and reasonableness
        if require_timezone_aware_timestamp and not TraceValidator._is_timezone_aware(commit.timestamp):
            raise ValueError("Protocol Violation: 'timestamp' must be timezone-aware.")

        if TraceValidator._is_timezone_aware(commit.timestamp):
            now_utc = datetime.now(timezone.utc)
            ts_utc = commit.timestamp.astimezone(timezone.utc)
            if ts_utc > now_utc + TraceValidator.MAX_TIMESTAMP_FUTURE_SKEW:
                raise ValueError("Protocol Violation: 'timestamp' is too far in the future.")
            if ts_utc < now_utc - TraceValidator.MAX_TIMESTAMP_AGE:
                raise ValueError("Protocol Violation: 'timestamp' is too old.")

        # Rule 9: commit SHA / parent SHA policy and format checks
        if require_commit_sha and not commit.commit_sha:
            raise ValueError("Protocol Violation: 'commit_sha' is required by policy.")

        if commit.commit_sha:
            TraceValidator._validate_sha1(commit.commit_sha, "commit_sha")

        if commit.parent_sha:
            TraceValidator._validate_sha1(commit.parent_sha, "parent_sha")

        if require_parent_sha and not commit.parent_sha:
            raise ValueError("Protocol Violation: 'parent_sha' is required by policy.")

        if expected_commit_sha:
            TraceValidator._validate_sha1(expected_commit_sha, "expected_commit_sha")
            if commit.commit_sha != expected_commit_sha:
                raise ValueError("Protocol Violation: 'commit_sha' does not match expected commit SHA.")

        # Rule 10: integrity anchor shape and hash consistency
        for field_name in ["tree_hash", "diff_hash", "reasoning_hash", "binding_hash"]:
            field_value = getattr(commit, field_name)
            if field_value is not None:
                if not isinstance(field_value, str):
                    raise ValueError(f"Protocol Violation: '{field_name}' must be a string when present.")
                if field_name == "tree_hash":
                    if len(field_value) == 40:
                        TraceValidator._validate_sha1(field_value, field_name)
                    elif len(field_value) == 64:
                        TraceValidator._validate_sha256(field_value, field_name)
                    else:
                        raise ValueError("Protocol Violation: 'tree_hash' must be 40/64-char git object hash.")
                else:
                    TraceValidator._validate_sha256(field_value, field_name)

        if commit.reasoning_hash:
            expected_reasoning_hash = compute_reasoning_hash(commit.reasoning_trace)
            if commit.reasoning_hash != expected_reasoning_hash:
                raise ValueError("Protocol Violation: 'reasoning_hash' does not match reasoning_trace.")

        if commit.binding_hash:
            expected_binding_hash = compute_binding_hash(commit_data)
            if commit.binding_hash != expected_binding_hash:
                raise ValueError("Protocol Violation: 'binding_hash' does not match bound trace fields.")

        return commit

    @staticmethod
    def check_quality(commit: TraceCommit) -> List[str]:
        """
        Optional: Returns warnings about the quality of the trace.
        """
        warnings = []
        if len(commit.reasoning_trace) < 3:
            warnings.append("Weak Reasoning: Trace has fewer than 3 steps.")

        if len(commit.diff_summary) < 10:
            warnings.append("Weak Summary: Diff summary is too short.")

        if not commit.parent_sha:
            warnings.append("Traceability Warning: 'parent_sha' is missing.")

        if not TraceValidator._is_timezone_aware(commit.timestamp):
            warnings.append("Traceability Warning: 'timestamp' is not timezone-aware.")

        if not commit.commit_sha:
            warnings.append("Traceability Warning: 'commit_sha' is missing.")

        return warnings

    @staticmethod
    def validate_pull_request_spec(pr_data: Dict[str, Any]) -> PullRequestSpec:
        """
        Validates PullRequestSpec and enforces PR-level protocol rules.
        """
        try:
            pr = PullRequestSpec(**pr_data)
        except ValidationError as e:
            raise ValueError(f"Schema Validation Failed: {e}")

        if pr.type in {"fix", "feat"} and not pr.tests_added:
            raise ValueError("Protocol Violation: 'tests_added' must be true for pull request type 'fix' and 'feat'.")

        return pr
