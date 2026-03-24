import logging
import os
import stat

import pytest

from agenthub_git_core import repo_manager as repo_manager_module
from agenthub_git_core.repo_manager import RepoManager, _build_runtime_hook_wrapper


def test_runtime_hook_wrapper_uses_dynamic_runtime_resolution():
    wrapper = _build_runtime_hook_wrapper()

    assert "AgentHub Hook Wrapper (runtime-resolved)" in wrapper
    assert "-m agenthub_git_core.hook_logic" in wrapper
    assert "AGENTHUB_HOOK_PYTHON" in wrapper
    assert "AGENTHUB_GIT_CORE_SRC" in wrapper



def test_runtime_hook_wrapper_does_not_include_monorepo_scan_fallback():
    wrapper = _build_runtime_hook_wrapper()

    assert "SEARCH_DIR" not in wrapper
    assert "services/git-core/src" not in wrapper


def test_install_hook_replaces_legacy_pinned_wrapper(tmp_path):
    storage_root = tmp_path / "repos"
    storage_root.mkdir()
    repo_path = storage_root / "legacy.git"
    hooks_dir = repo_path / "hooks"
    hooks_dir.mkdir(parents=True)

    pinned_script_path = os.path.join(os.path.dirname(repo_manager_module.__file__), "hook_logic.py")
    old_hook = hooks_dir / "pre-receive"
    old_hook.write_text(
        f"#!/bin/sh\n\"/usr/local/bin/python\" \"{pinned_script_path}\"\n",
        encoding="utf-8",
    )

    mgr = RepoManager(str(storage_root))
    mgr.install_hook(str(repo_path))

    content = old_hook.read_text(encoding="utf-8")
    assert "runtime-resolved" in content
    assert pinned_script_path not in content
    assert "-m agenthub_git_core.hook_logic" in content

    mode = old_hook.stat().st_mode
    assert mode & stat.S_IEXEC



def test_create_repo_writes_idempotency_marker_and_reuses_when_token_matches(tmp_path):
    storage_root = tmp_path / "repos"
    storage_root.mkdir()
    mgr = RepoManager(str(storage_root))

    first_path = mgr.create_repo(
        "idem.git",
        actor_id="agent-1",
        idempotency_token="token-123",
        request_id="req-1",
    )
    second_path = mgr.create_repo(
        "idem.git",
        actor_id="agent-1",
        idempotency_token="token-123",
        request_id="req-2",
    )

    assert first_path == second_path
    marker = storage_root / "idem.git" / "hooks" / ".agenthub-create-idempotency-token"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "token-123"



def test_create_repo_emits_audit_logs(caplog, tmp_path):
    storage_root = tmp_path / "repos"
    storage_root.mkdir()
    mgr = RepoManager(str(storage_root))

    with caplog.at_level(logging.INFO):
        mgr.create_repo(
            "audit.git",
            actor_id="agent-42",
            idempotency_token="audit-token",
            request_id="req-audit",
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("[repo_manager][create_repo][start]" in m and "actor=agent-42" in m and "request_id=req-audit" in m for m in messages)
    assert any("[repo_manager][install_hook]" in m and "actor=agent-42" in m and "request_id=req-audit" in m for m in messages)
    assert any("[repo_manager][create_repo][success]" in m and "actor=agent-42" in m and "request_id=req-audit" in m for m in messages)



def test_create_repo_rejects_existing_repo_when_token_differs(tmp_path):
    storage_root = tmp_path / "repos"
    storage_root.mkdir()
    mgr = RepoManager(str(storage_root))

    mgr.create_repo("dup.git", idempotency_token="token-a")
    with pytest.raises(ValueError, match="Repository already exists"):
        mgr.create_repo("dup.git", idempotency_token="token-b")



def test_refresh_existing_hooks_only_updates_git_repositories(tmp_path):
    storage_root = tmp_path / "repos"
    storage_root.mkdir()

    repo_a = storage_root / "a.git"
    repo_b = storage_root / "b.git"
    for repo in (repo_a, repo_b):
        hook_path = repo / "hooks" / "pre-receive"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("legacy", encoding="utf-8")

    non_repo = storage_root / "notes"
    non_repo.mkdir()

    mgr = RepoManager(str(storage_root))
    refreshed = mgr.refresh_existing_hooks()

    assert refreshed == 2
    assert "runtime-resolved" in (repo_a / "hooks" / "pre-receive").read_text(encoding="utf-8")
    assert "runtime-resolved" in (repo_b / "hooks" / "pre-receive").read_text(encoding="utf-8")
    assert not (non_repo / "hooks" / "pre-receive").exists()
