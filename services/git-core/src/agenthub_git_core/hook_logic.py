import json
import os
import re
import subprocess
import sys
from typing import Optional


DEFAULT_PROTECTED_BRANCH_REFS = {"refs/heads/main", "refs/heads/master"}
PROTECTED_BRANCHES_ENV = "TRACE_COMMIT_PROTECTED_BRANCHES"
PUSH_ACTOR_ENV = "AGENTHUB_PUSH_ACTOR"
ZERO_SHA = "0000000000000000000000000000000000000000"
ALLOWED_PUSH_REF_PATTERNS = (
    re.compile(r"^refs/heads/agent/([^/\s]+)/.+$"),
    re.compile(r"^refs/heads/system/([^/\s]+)/.+$"),
)

MAX_PUSH_COMMITS_ENV = "TRACE_COMMIT_MAX_PUSH_COMMITS"
MAX_PUSH_OBJECTS_ENV = "TRACE_COMMIT_MAX_PUSH_OBJECTS"
MAX_PUSH_OBJECT_BYTES_ENV = "TRACE_COMMIT_MAX_PUSH_OBJECT_BYTES"
MAX_PUSH_TOTAL_OBJECT_BYTES_ENV = "TRACE_COMMIT_MAX_PUSH_TOTAL_OBJECT_BYTES"
MAX_PUSH_QUARANTINE_BYTES_ENV = "TRACE_COMMIT_MAX_PUSH_QUARANTINE_BYTES"
MAX_REPO_SIZE_BYTES_ENV = "TRACE_COMMIT_MAX_REPO_SIZE_BYTES"

DEFAULT_MAX_PUSH_COMMITS = 200
DEFAULT_MAX_PUSH_OBJECTS = 10000
DEFAULT_MAX_PUSH_OBJECT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_PUSH_TOTAL_OBJECT_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_PUSH_QUARANTINE_BYTES = 150 * 1024 * 1024
DEFAULT_MAX_REPO_SIZE_BYTES = 5 * 1024 * 1024 * 1024


def _normalize_protected_branch_ref(branch: str) -> Optional[str]:
    if not branch:
        return None
    if branch.startswith("refs/heads/"):
        return branch
    if branch.startswith("refs/"):
        return None
    return f"refs/heads/{branch}"


def _get_protected_branch_refs():
    protected_refs = set(DEFAULT_PROTECTED_BRANCH_REFS)
    raw_value = os.getenv(PROTECTED_BRANCHES_ENV, "")
    for item in raw_value.split(","):
        normalized = _normalize_protected_branch_ref(item.strip())
        if normalized:
            protected_refs.add(normalized)
    return protected_refs


def _load_protocol_runtime():
    """Load protocol classes from installed/runtime PYTHONPATH only."""
    from agenthub_protocol import TraceCommit, TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES
    from agenthub_protocol.signing import (
        compute_diff_hash_from_patch,
        get_trace_signing_secret,
        is_trace_signature_required,
        verify_trace_commit_signature,
    )
    from agenthub_protocol.validator import TraceValidator

    return (
        TraceCommit,
        TraceValidator,
        TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES,
        compute_diff_hash_from_patch,
        get_trace_signing_secret,
        is_trace_signature_required,
        verify_trace_commit_signature,
    )


def get_commit_message(commit_sha: str) -> str:
    """Read the raw commit message body."""
    cmd = ["git", "log", "-1", "--format=%B", commit_sha]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _extract_ref_actor(ref: str) -> str:
    for pattern in ALLOWED_PUSH_REF_PATTERNS:
        match = pattern.match(ref)
        if match:
            return match.group(1)
    raise ValueError(
        f"Unsupported ref '{ref}'. Allowed refs: refs/heads/agent/<agent_id>/* or refs/heads/system/<actor_id>/*"
    )


def _get_transport_actor() -> str:
    actor = (os.getenv(PUSH_ACTOR_ENV) or "").strip()
    if not actor:
        raise ValueError(f"Missing required transport actor env: {PUSH_ACTOR_ENV}")
    if any(ch.isspace() for ch in actor):
        raise ValueError(f"Invalid transport actor in {PUSH_ACTOR_ENV}: whitespace is not allowed")
    return actor


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"Environment variable {name} must be >= 0")
    return value


def _iter_new_commits(old_sha: str, new_sha: str) -> list[str]:
    rev_range = new_sha if old_sha == ZERO_SHA else f"{old_sha}..{new_sha}"
    try:
        return subprocess.check_output(["git", "rev-list", rev_range]).decode().splitlines()
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Failed to enumerate commits: {str(e)}")


def _collect_push_object_sizes(old_sha: str, new_sha: str) -> tuple[int, int, int]:
    old = old_sha if old_sha != ZERO_SHA else ""
    cmd = ["git", "rev-list", "--objects", "--no-object-names", new_sha]
    if old:
        cmd.append(f"^{old}")
    try:
        object_ids = [line.strip() for line in subprocess.check_output(cmd).decode().splitlines() if line.strip()]
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Failed to enumerate pushed objects: {str(e)}")

    if not object_ids:
        return 0, 0, 0

    max_bytes = 0
    total_bytes = 0
    for i in range(0, len(object_ids), 500):
        chunk = object_ids[i : i + 500]
        try:
            sizes_raw = subprocess.check_output(["git", "cat-file", "--batch-check=%(objectsize)"] + chunk).decode().splitlines()
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Failed to inspect object sizes: {str(e)}")
        for size_line in sizes_raw:
            size_val = int(size_line.strip())
            total_bytes += size_val
            if size_val > max_bytes:
                max_bytes = size_val

    return len(object_ids), max_bytes, total_bytes


def _dir_size_bytes(path: str) -> int:
    total = 0
    if not path or not os.path.exists(path):
        return 0
    for root, _, files in os.walk(path):
        for filename in files:
            fp = os.path.join(root, filename)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def _enforce_push_size_limits(old_sha: str, new_sha: str, commit_count: int) -> None:
    max_push_commits = _env_int(MAX_PUSH_COMMITS_ENV, DEFAULT_MAX_PUSH_COMMITS)
    max_push_objects = _env_int(MAX_PUSH_OBJECTS_ENV, DEFAULT_MAX_PUSH_OBJECTS)
    max_push_object_bytes = _env_int(MAX_PUSH_OBJECT_BYTES_ENV, DEFAULT_MAX_PUSH_OBJECT_BYTES)
    max_push_total_object_bytes = _env_int(MAX_PUSH_TOTAL_OBJECT_BYTES_ENV, DEFAULT_MAX_PUSH_TOTAL_OBJECT_BYTES)
    max_push_quarantine_bytes = _env_int(MAX_PUSH_QUARANTINE_BYTES_ENV, DEFAULT_MAX_PUSH_QUARANTINE_BYTES)
    max_repo_size_bytes = _env_int(MAX_REPO_SIZE_BYTES_ENV, DEFAULT_MAX_REPO_SIZE_BYTES)

    if max_push_commits and commit_count > max_push_commits:
        raise ValueError(
            f"Push commit count {commit_count} exceeds limit {max_push_commits}."
        )

    object_count, max_object_size, total_object_size = _collect_push_object_sizes(old_sha, new_sha)
    if max_push_objects and object_count > max_push_objects:
        raise ValueError(
            f"Push object count {object_count} exceeds limit {max_push_objects}."
        )
    if max_push_object_bytes and max_object_size > max_push_object_bytes:
        raise ValueError(
            f"Largest pushed object {max_object_size} bytes exceeds limit {max_push_object_bytes}."
        )
    if max_push_total_object_bytes and total_object_size > max_push_total_object_bytes:
        raise ValueError(
            f"Total pushed object size {total_object_size} bytes exceeds limit {max_push_total_object_bytes}."
        )

    quarantine_dir = os.getenv("GIT_QUARANTINE_PATH", "")
    quarantine_size = _dir_size_bytes(quarantine_dir)
    if max_push_quarantine_bytes and quarantine_size > max_push_quarantine_bytes:
        raise ValueError(
            f"Quarantine pack size {quarantine_size} bytes exceeds limit {max_push_quarantine_bytes}."
        )

    git_dir = os.getenv("GIT_DIR", "")
    repo_size = _dir_size_bytes(git_dir)
    if max_repo_size_bytes and repo_size > max_repo_size_bytes:
        raise ValueError(
            f"Repository size {repo_size} bytes exceeds limit {max_repo_size_bytes}."
        )


def validate_push() -> None:
    """
    Standard Git Pre-Receive Hook.
    Reads (old_sha, new_sha, ref_name) from stdin.
    """
    (
        _,
        TraceValidator,
        max_commit_message_bytes,
        compute_diff_hash_from_patch,
        get_trace_signing_secret,
        is_trace_signature_required,
        verify_trace_commit_signature,
    ) = _load_protocol_runtime()
    signature_required = is_trace_signature_required()
    signing_secret = get_trace_signing_secret()
    protected_branch_refs = _get_protected_branch_refs()

    if signature_required and not signing_secret:
        print("❌ REJECTED: Trace signature required but signing secret is not configured.", file=sys.stderr)
        sys.exit(1)

    try:
        transport_actor_id = _get_transport_actor()
    except ValueError as actor_err:
        print(f"❌ REJECTED: {actor_err}", file=sys.stderr)
        sys.exit(1)

    print("🤖 AgentHub Guard: Inspecting incoming commits...", file=sys.stderr)

    # Read lines from stdin
    input_lines = sys.stdin.read().strip().splitlines()
    if not input_lines:
        print("❌ REJECTED: Empty pre-receive input.", file=sys.stderr)
        sys.exit(1)

    for line in input_lines:
        old_sha, new_sha, ref = line.split()
        try:
            ref_actor_id = _extract_ref_actor(ref)
        except ValueError as ref_err:
            print(f"❌ REJECTED: {ref_err}", file=sys.stderr)
            sys.exit(1)

        if ref_actor_id != transport_actor_id:
            print(
                "❌ REJECTED: transport actor does not match ref actor "
                f"(transport={transport_actor_id}, ref={ref_actor_id}).",
                file=sys.stderr,
            )
            sys.exit(1)

        is_delete = new_sha == ZERO_SHA
        if ref in protected_branch_refs:
            print(
                f"❌ REJECTED: Updates or deletes on protected branch '{ref}' are not allowed.",
                file=sys.stderr,
            )
            sys.exit(1)

        if is_delete:
            print(f"🧾 AUDIT: Branch delete requested and allowed for '{ref}'.", file=sys.stderr)
            continue

        # Validate all commits in the push range
        try:
            commits = _iter_new_commits(old_sha, new_sha)
            _enforce_push_size_limits(old_sha, new_sha, len(commits))
        except ValueError as e:
            print(f"❌ REJECTED: {str(e)}", file=sys.stderr)
            sys.exit(1)

        for commit_sha in commits:
            msg = get_commit_message(commit_sha)
            if len(msg.encode("utf-8")) > max_commit_message_bytes:
                print(
                    "❌ REJECTED: Commit "
                    f"{commit_sha[:7]} message too large "
                    f"(>{max_commit_message_bytes} bytes).",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                data = json.loads(msg)
                author = data.get("author") if isinstance(data, dict) else None
                author_agent_id = author.get("agent_id") if isinstance(author, dict) else None
                if not isinstance(author_agent_id, str) or not author_agent_id.strip():
                    raise ValueError("Protocol Violation: 'author.agent_id' is missing.")
                if author_agent_id != ref_actor_id:
                    raise ValueError("Protocol Violation: branch actor does not match TraceCommit author.agent_id.")

                parent_commit_sha = subprocess.check_output(["git", "show", "-s", "--format=%P", commit_sha]).decode().strip()
                expected_parent_sha = parent_commit_sha.split()[0] if parent_commit_sha else None
                trace = TraceValidator.validate_commit(
                    data,
                    expected_commit_sha=commit_sha,
                    require_commit_sha=True,
                    require_parent_sha=bool(expected_parent_sha),
                    require_timezone_aware_timestamp=True,
                )
                if expected_parent_sha and data.get("parent_sha") != expected_parent_sha:
                    raise ValueError("Protocol Violation: 'parent_sha' does not match git parent SHA.")

                expected_tree_hash = subprocess.check_output(["git", "show", "-s", "--format=%T", commit_sha]).decode().strip()
                if data.get("tree_hash") != expected_tree_hash:
                    raise ValueError("Protocol Violation: 'tree_hash' does not match git tree.")

                diff_patch = subprocess.check_output(
                    ["git", "diff-tree", "--root", "--binary", "--full-index", "-p", "--no-commit-id", commit_sha],
                ).decode("utf-8", errors="replace")
                expected_diff_hash = compute_diff_hash_from_patch(diff_patch)
                if data.get("diff_hash") != expected_diff_hash:
                    raise ValueError("Protocol Violation: 'diff_hash' does not match git patch.")

                if signature_required:
                    if not verify_trace_commit_signature(data, signing_secret):
                        raise ValueError("Protocol Violation: invalid TraceCommit signature.")
                print(f"✅ Protocol Verified: {trace.diff_summary}", file=sys.stderr)
                print(f"🧠 Reasoning Trace: {len(trace.reasoning_trace)} steps", file=sys.stderr)
            except json.JSONDecodeError:
                print(f"❌ REJECTED: Commit {commit_sha[:7]} is not valid JSON.", file=sys.stderr)
                print(
                    "   AgentHub requires all commits to be structured JSON conforming to TraceCommit Schema.",
                    file=sys.stderr,
                )
                sys.exit(1)
            except Exception as e:
                print(f"❌ REJECTED: Commit {commit_sha[:7]} violates AgentHub Protocol.", file=sys.stderr)
                print(f"   Error: {str(e)}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    validate_push()
