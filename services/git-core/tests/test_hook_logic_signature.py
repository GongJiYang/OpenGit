import io
import json
from types import SimpleNamespace

import pytest

from agenthub_protocol.signing import (
    compute_binding_hash,
    compute_diff_hash_from_patch,
    compute_reasoning_hash,
    sign_trace_commit,
)
from agenthub_git_core import hook_logic


def _valid_trace_payload(commit_sha: str = "a" * 40):
    payload = {
        "protocol_version": "1.0",
        "commit_sha": commit_sha,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip structured alternative analysis"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "agent-a", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
        "tree_hash": "f" * 40,
        "diff_hash": compute_diff_hash_from_patch(""),
    }
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])
    payload["binding_hash"] = compute_binding_hash(payload)
    return payload


def _install_runtime(monkeypatch):
    from agenthub_protocol.schemas import TraceCommit
    from agenthub_protocol.validator import TraceValidator
    from agenthub_protocol.signing import (
        compute_diff_hash_from_patch,
        get_trace_signing_secret,
        is_trace_signature_required,
        verify_trace_commit_signature,
    )

    from agenthub_protocol.schemas import TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES

    monkeypatch.setattr(
        hook_logic,
        "_load_protocol_runtime",
        lambda: (
            TraceCommit,
            TraceValidator,
            TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES,
            compute_diff_hash_from_patch,
            get_trace_signing_secret,
            is_trace_signature_required,
            verify_trace_commit_signature,
        ),
    )


def _mock_git(monkeypatch, payload_by_sha, patch_by_sha=None, tree_hash_by_sha=None, object_sizes=None, parent_by_sha=None):
    patches = patch_by_sha or {}
    trees = tree_hash_by_sha or {sha: payload["tree_hash"] for sha, payload in payload_by_sha.items()}
    sizes = object_sizes or {sha: 1024 for sha in payload_by_sha.keys()}
    parents = parent_by_sha or {sha: payload.get("parent_sha", "") for sha, payload in payload_by_sha.items()}

    def fake_check_output(cmd):
        if cmd[:2] == ["git", "rev-list"] and "--objects" not in cmd:
            return "\n".join(payload_by_sha.keys()).encode("utf-8")
        if cmd[:3] == ["git", "rev-list", "--objects"]:
            return "\n".join(payload_by_sha.keys()).encode("utf-8")
        if cmd[:4] == ["git", "show", "-s", "--format=%T"]:
            sha = cmd[-1]
            return trees[sha].encode("utf-8")
        if cmd[:4] == ["git", "show", "-s", "--format=%P"]:
            sha = cmd[-1]
            return parents.get(sha, "").encode("utf-8")
        if cmd[:2] == ["git", "diff-tree"]:
            sha = cmd[-1]
            return patches.get(sha, "").encode("utf-8")
        if cmd[:2] == ["git", "cat-file"]:
            requested = cmd[2:]
            vals = [str(sizes.get(oid, 1024)) for oid in requested]
            return "\n".join(vals).encode("utf-8")
        return b""

    monkeypatch.setattr(hook_logic.subprocess, "check_output", fake_check_output)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "log", "-1"]:
            sha = cmd[-1]
            return SimpleNamespace(stdout=json.dumps(payload_by_sha[sha]))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(hook_logic.subprocess, "run", fake_run)


def test_validate_push_rejects_updates_to_protected_main_branch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/main\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_deletes_to_protected_master_branch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'a'*40} {'0'*40} refs/heads/master\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_allows_non_protected_branch_delete_and_audits(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    target_ref = "refs/heads/agent/agent-a/cleanup"
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'a'*40} {'0'*40} {target_ref}\n"))
    stderr_buffer = io.StringIO()
    monkeypatch.setattr(hook_logic.sys, "stderr", stderr_buffer)

    hook_logic.validate_push()

    assert f"AUDIT: Branch delete requested and allowed for '{target_ref}'" in stderr_buffer.getvalue()


def test_validate_push_rejects_author_agent_id_mismatch_with_ref_actor(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-b")
    monkeypatch.setattr(
        hook_logic.sys,
        "stdin",
        SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-b/feature-test\n"),
    )

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_configured_protected_branch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setenv("TRACE_COMMIT_PROTECTED_BRANCHES", "release, refs/heads/hotfix")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/release\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_ignores_non_head_refs_in_protected_branches_env(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setenv("TRACE_COMMIT_PROTECTED_BRANCHES", "refs/tags/v1,refs/remotes/origin/main")
    monkeypatch.setattr(
        hook_logic.sys,
        "stdin",
        SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"),
    )

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    hook_logic.validate_push()


def test_validate_push_rejects_unsupported_ref_pattern(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/feature/test\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_accepts_system_ref_with_matching_author(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(
        hook_logic.sys,
        "stdin",
        SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/system/agent-a/task-tree-20260323000000-abc123\n"),
    )

    payload = _valid_trace_payload("a" * 40)
    payload["author"]["agent_id"] = "agent-a"
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    hook_logic.validate_push()


def test_validate_push_rejects_system_ref_author_mismatch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(
        hook_logic.sys,
        "stdin",
        SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/system/agent-a/task-tree-20260323000000-abc123\n"),
    )

    payload = _valid_trace_payload("a" * 40)
    payload["author"]["agent_id"] = "agent-b"
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_missing_signature_when_required(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "1")
    monkeypatch.setenv("TRACE_COMMIT_SIGNING_SECRET", "hook-secret")
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_invalid_signature_when_required(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "1")
    monkeypatch.setenv("TRACE_COMMIT_SIGNING_SECRET", "hook-secret")
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    payload["signature"] = "deadbeef"
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_accepts_valid_signature_when_required(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "1")
    monkeypatch.setenv("TRACE_COMMIT_SIGNING_SECRET", "hook-secret")
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    payload["signature"] = sign_trace_commit(payload, "hook-secret")
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    hook_logic.validate_push()


def test_validate_push_skips_signature_when_policy_disabled(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    hook_logic.validate_push()


def test_validate_push_rejects_when_secret_missing_but_required(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "1")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_when_transport_actor_missing(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENTHUB_PUSH_ACTOR", raising=False)
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_when_transport_actor_mismatches_ref_actor(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-b")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_when_commit_sha_missing(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    payload.pop("commit_sha", None)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_when_parent_sha_mismatches_git_parent(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    payload["parent_sha"] = "c" * 40
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(
        monkeypatch,
        {"a" * 40: payload},
        {"a" * 40: patch},
        parent_by_sha={"a" * 40: "b" * 40},
    )

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_when_parent_sha_missing_for_non_root(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    payload.pop("parent_sha", None)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(
        monkeypatch,
        {"a" * 40: payload},
        {"a" * 40: patch},
        parent_by_sha={"a" * 40: "b" * 40},
    )

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_accepts_missing_parent_sha_for_root_commit(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    payload.pop("parent_sha", None)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(
        monkeypatch,
        {"a" * 40: payload},
        {"a" * 40: patch},
        parent_by_sha={"a" * 40: ""},
    )

    hook_logic.validate_push()


def test_validate_push_rejects_tree_hash_mismatch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    payload["tree_hash"] = "f" * 40
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(
        monkeypatch,
        {"a" * 40: payload},
        {"a" * 40: patch},
        {"a" * 40: "e" * 40},
    )

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_excessive_commit_count(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.setenv("TRACE_COMMIT_MAX_PUSH_COMMITS", "1")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload_a = _valid_trace_payload("a" * 40)
    payload_b = _valid_trace_payload("b" * 40)
    _mock_git(monkeypatch, {"a" * 40: payload_a, "b" * 40: payload_b}, {"a" * 40: "", "b" * 40: ""})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_excessive_object_size(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.setenv("TRACE_COMMIT_MAX_PUSH_OBJECT_BYTES", "1024")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: ""}, object_sizes={"a" * 40: 2048})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_excessive_quarantine_size(monkeypatch, tmp_path):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.setenv("TRACE_COMMIT_MAX_PUSH_QUARANTINE_BYTES", "10")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    quarantine = tmp_path / "quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "packfile").write_bytes(b"x" * 32)
    monkeypatch.setenv("GIT_QUARANTINE_PATH", str(quarantine))

    payload = _valid_trace_payload("a" * 40)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: ""})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_accepts_compacted_reasoning_trace_within_message_limit(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    full_reasoning_hash = compute_reasoning_hash(payload["reasoning_trace"])
    payload["reasoning_trace"] = [
        "Reasoning trace abbreviated in git commit message due to byte limit; full trace is persisted in CommitRecord.trace_json.",
        f"reasoning_hash_full={full_reasoning_hash}",
        "reasoning_steps_full=50",
    ]
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    hook_logic.validate_push()


def test_validate_push_rejects_oversized_commit_message(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    oversized_summary = "x" * 70000
    payload["diff_summary"] = oversized_summary
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch(patch)
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1


def test_validate_push_rejects_diff_hash_mismatch(monkeypatch):
    _install_runtime(monkeypatch)
    monkeypatch.setenv("TRACE_COMMIT_SIGNATURE_REQUIRED", "0")
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTHUB_PUSH_ACTOR", "agent-a")
    monkeypatch.setattr(hook_logic.sys, "stdin", SimpleNamespace(read=lambda: f"{'0'*40} {'a'*40} refs/heads/agent/agent-a/feature-test\n"))

    payload = _valid_trace_payload("a" * 40)
    patch = ""
    payload["diff_hash"] = compute_diff_hash_from_patch("changed")
    payload["binding_hash"] = compute_binding_hash(payload)
    _mock_git(monkeypatch, {"a" * 40: payload}, {"a" * 40: patch})

    with pytest.raises(SystemExit) as exc:
        hook_logic.validate_push()
    assert exc.value.code == 1
