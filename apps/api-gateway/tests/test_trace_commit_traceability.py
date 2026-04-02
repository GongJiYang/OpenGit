import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from uuid import uuid4
from pathlib import Path

import pytest

from sqlmodel import Session, select
from agenthub_protocol.signing import (
    compute_binding_hash,
    compute_reasoning_hash,
    get_trace_signing_secret,
    sign_trace_commit,
    verify_trace_commit_signature,
)
from agenthub_protocol.schemas import TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES
from agenthub_protocol.validator import TraceValidator

from dependencies.auth import require_active_identity
from persistence import Bounty, BountyStatus, CommitRecord

from agent_auth.models import Agent
from agent_auth.models.platform import Repo, UserAgentBinding
from agent_auth.models.runner import ComputeJob, ComputeJobStatus, ExecutionMode, RepoExecutionConfig
from routers import commits as commits_router
from git_tree_service.service import GitTreeService
from agent_auth.utils import get_api_key_prefix
from core.security import (
    STORE_ROOT,
    GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL,
    GOVERNANCE_ENFORCE_VERIFY_NOT_IMPLEMENTED_DETAIL,
)



def _init_repo_with_one_commit(bare_path: str) -> None:
    tmp = tempfile.mkdtemp(prefix="traceability_seed_")
    try:
        subprocess.run(["git", "init", tmp], check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "seed@test.local"], cwd=tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "seed"], cwd=tmp, check=True, capture_output=True, text=True)
        Path(tmp, "README.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp, check=True, capture_output=True, text=True)

        os.makedirs(bare_path, exist_ok=True)
        subprocess.run(["git", "init", "--bare", bare_path], check=True, capture_output=True, text=True)
        subprocess.run(["git", "remote", "add", "origin", bare_path], cwd=tmp, check=True, capture_output=True, text=True)
        current_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "push", "origin", current_branch], cwd=tmp, check=True, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_commit_trace_json_contains_traceability_fields(client, db_engine, auth_headers):
    repo_name = "traceability-commit-fields"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)
    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "traceability payload\n"},
            "intent_description": "ensure trace fields are persisted",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "add traceability payload",
            "reasoning_trace": [
                "collect parent sha before commit",
                "persist timestamp deterministically",
                "persist commit_sha after commit"
            ],
            "rejected_alternatives": ["skip traceability fields because they are optional"]
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get("quality_warnings") == []
        assert body.get("task_tree_sync", {}).get("attempted") is True
        assert body.get("task_tree_sync", {}).get("status") in {"synced", "failed"}

        with Session(db_engine) as s:
            rec = s.exec(select(CommitRecord).where(CommitRecord.repo_name == repo_name).order_by(CommitRecord.id.desc())).first()
            assert rec is not None
            assert rec.commit_sha
            assert rec.trace_json is not None

            trace_json = rec.trace_json
            assert trace_json.get("commit_sha") == rec.commit_sha
            ts = trace_json.get("timestamp")
            assert ts
            assert datetime.fromisoformat(ts).tzinfo is not None
            assert "parent_sha" in trace_json
            assert trace_json.get("parent_sha")
            assert trace_json.get("tree_hash")
            assert trace_json.get("diff_hash")
            assert trace_json.get("reasoning_hash")
            assert trace_json.get("binding_hash")
            expected_reasoning_hash = compute_reasoning_hash(trace_json.get("reasoning_trace") or [])
            assert trace_json.get("reasoning_hash") == expected_reasoning_hash
            expected_binding_hash = compute_binding_hash(trace_json)
            assert trace_json.get("binding_hash") == expected_binding_hash
            assert trace_json.get("signature")
            signing_secret = get_trace_signing_secret()
            assert signing_secret
            assert verify_trace_commit_signature(trace_json, signing_secret)
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_for_bounty_validates_before_git_push(client, db_engine, auth_headers):
    repo_name = "traceability-precheck-before-push"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="precheck",
                description="",
                reward=1,
                status=BountyStatus.OPEN.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=None,
                test_command="pytest",
                verification_mode="human",
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "should not push\n"},
            "intent_description": "should fail before push",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "guard before push",
            "reasoning_trace": ["precheck bounty before side effects"],
            "rejected_alternatives": ["push first and validate bounty state later"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 403, res.text

        ls_remote = subprocess.run(
            ["git", "ls-remote", bare_path],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"refs/heads/agent/{agent_id}/bounty_{bounty_id}" not in ls_remote.stdout
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_requires_non_empty_intent_vector(client, db_engine, auth_headers):
    repo_name = "traceability-intent-vector-required"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "missing vector\n"},
            "intent_description": "vector is required",
            "intent_category": "fix",
            "intent_vector": [],
            "diff_summary": "reject empty intent vector",
            "reasoning_trace": ["enforce protocol requirement"],
            "rejected_alternatives": ["allow empty vectors by default"],
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 400, res.text
        assert "intent.vector" in res.text
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_returns_quality_warnings_for_weak_trace(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-quality-warning-response"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    monkeypatch.setenv("TRACE_COMMIT_QUALITY_GATE", "warn")

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "weak trace\n"},
            "intent_description": "ensure quality warnings are surfaced",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "add weak trace payload",
            "reasoning_trace": ["single step"],
            "rejected_alternatives": ["skip quality checks"],
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        body = res.json()
        warnings = body.get("quality_warnings")
        assert isinstance(warnings, list)
        assert any("Weak Reasoning" in w for w in warnings)

        with Session(db_engine) as s:
            rec = s.exec(select(CommitRecord).where(CommitRecord.repo_name == repo_name).order_by(CommitRecord.id.desc())).first()
            assert rec is not None
            assert isinstance(rec.trace_json.get("quality_warnings"), list)
            assert any("Weak Reasoning" in w for w in rec.trace_json.get("quality_warnings", []))
    finally:
        monkeypatch.delenv("TRACE_COMMIT_QUALITY_GATE", raising=False)
        shutil.rmtree(bare_path, ignore_errors=True)


def test_pending_verification_requires_authentication(client):
    res = client.get("/api/v1/commits/pending/verification")
    assert res.status_code == 401


def test_commit_git_push_failure_returns_502(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-push-failure-status"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "push should fail\n"},
            "intent_description": "simulate git push failure",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "simulate push failure status",
            "reasoning_trace": ["force git push failure"],
            "rejected_alternatives": ["simulate clone failure instead of push failure"],
        }

        real_run = commits_router.subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "push":
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="simulated push error")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(commits_router.subprocess, "run", fake_run)

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 502, res.text
        body = res.json()
        assert body.get("detail") == "Git push failed"
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


@pytest.mark.parametrize(
    "exc,expected_status,expected_detail",
    [
        (subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=b"fatal"), 502, "Git operation failed"),
        (RuntimeError("boom"), 500, "Internal server error"),
    ],
)
def test_commit_failure_paths_never_return_200(client, db_engine, auth_headers, monkeypatch, exc, expected_status, expected_detail):
    repo_name = "traceability-failure-semantics"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "semantics\n"},
            "intent_description": "force failure path",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "ensure non-200 errors",
            "reasoning_trace": ["raise injected failure"],
            "rejected_alternatives": ["allow 200 with success=false to simplify client handling"],
        }

        call_counter = {"count": 0}

        def fake_run(cmd, *args, **kwargs):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise exc
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(commits_router.subprocess, "run", fake_run)

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == expected_status, res.text
        body = res.json()
        assert body.get("detail") == expected_detail
        assert res.status_code != 200
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_invalid_bounty_verification_mode_returns_500_and_rollbacks_bounty(client, db_engine, auth_headers):
    repo_name = "traceability-invalid-verification-mode"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="invalid verification mode",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest",
                verification_mode="legacy_mode",
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "invalid verification mode\n"},
            "intent_description": "surface invalid bounty verification_mode",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "invalid verification mode should fail with HTTP error",
            "reasoning_trace": ["legacy bounty data should not return success payload"],
            "rejected_alternatives": ["store synthetic verification exit_code=-1 and return success"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 500, res.text
        body = res.json()
        assert body.get("detail") == "Invalid bounty verification_mode"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is None

            bounty = s.get(Bounty, bounty_id)
            assert bounty is not None
            assert bounty.status == BountyStatus.IN_PROGRESS.value
            assert bounty.current_steps == 0
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)



def test_commit_db_persist_failure_returns_502_after_git_push(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-db-persist-failure"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="db persist fail rollback",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest",
                verification_mode="human",
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "db fail after push\n"},
            "intent_description": "simulate db persist failure",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "ensure git/db split is visible",
            "reasoning_trace": ["force commit record insert failure"],
            "rejected_alternatives": ["swallow db error after push"],
            "bounty_id": bounty_id,
        }

        pushed_branch = {"name": None}
        original_add = Session.add

        def fake_add(self, obj):
            if isinstance(obj, CommitRecord) and obj.repo_name == repo_name:
                pushed_branch["name"] = obj.branch_name
                raise RuntimeError("simulated db persist failure")
            return original_add(self, obj)

        monkeypatch.setattr(Session, "add", fake_add)

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 502, res.text
        body = res.json()
        assert body.get("detail") == "Commit persisted to git, but failed to record history"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is None

            bounty = s.get(Bounty, bounty_id)
            assert bounty is not None
            assert bounty.status == BountyStatus.IN_PROGRESS.value
            assert bounty.current_steps == 0

        assert pushed_branch["name"]
        ls_remote = subprocess.run(
            ["git", "ls-remote", bare_path],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"refs/heads/{pushed_branch['name']}" in ls_remote.stdout
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_for_same_bounty_generates_unique_branches(client, db_engine, auth_headers):
    repo_name = "traceability-unique-branch-per-submit"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="same bounty double submit",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest",
                verification_mode="human",
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        req1 = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "first submit\n"},
            "intent_description": "first",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "first submit",
            "reasoning_trace": ["first"],
            "rejected_alternatives": ["reuse same branch for all submissions"],
            "bounty_id": bounty_id,
        }
        req2 = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "second submit\n"},
            "intent_description": "second",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "second submit",
            "reasoning_trace": ["second"],
            "rejected_alternatives": ["reuse same branch for all submissions"],
            "bounty_id": bounty_id,
        }

        res1 = client.post(f"/api/v1/repos/{repo_name}/commit", json=req1, headers=auth_headers)
        assert res1.status_code == 200, res1.text

        with Session(db_engine) as s:
            bounty_row = s.get(Bounty, bounty_id)
            bounty_row.status = BountyStatus.IN_PROGRESS.value
            s.add(bounty_row)
            s.commit()

        res2 = client.post(f"/api/v1/repos/{repo_name}/commit", json=req2, headers=auth_headers)
        assert res2.status_code == 200, res2.text

        with Session(db_engine) as s:
            rows = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.asc())
            ).all()
            assert len(rows) >= 2
            b1 = rows[-2].branch_name
            b2 = rows[-1].branch_name
            assert b1 and b2
            assert b1 != b2
            assert b1.startswith(f"agent/{agent_id}/bounty_{bounty_id}-")
            assert b2.startswith(f"agent/{agent_id}/bounty_{bounty_id}-")

        ls_remote = subprocess.run(
            ["git", "ls-remote", bare_path],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"refs/heads/{b1}" in ls_remote.stdout
        assert f"refs/heads/{b2}" in ls_remote.stdout
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_push_branch_conflict_retries_with_new_branch_name(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-branch-conflict-retry"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "branch retry\n"},
            "intent_description": "retry push on branch conflict",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "retry with new branch name",
            "reasoning_trace": ["retry push when non-fast-forward occurs"],
            "rejected_alternatives": ["fail immediately on first push conflict"],
        }

        token_values = iter([
            "1111111111111111",
            "2222222222222222",
            "3333333333333333",
        ])
        monkeypatch.setattr(commits_router.secrets, "token_hex", lambda n: next(token_values))

        real_run = commits_router.subprocess.run
        pushed_refs = []

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and len(cmd) >= 4 and cmd[0] == "git" and cmd[1] == "push":
                ref = cmd[3]
                if ref.startswith(f"agent/{agent_id}/"):
                    pushed_refs.append(ref)
                    if len(pushed_refs) == 1:
                        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="non-fast-forward simulated")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(commits_router.subprocess, "run", fake_run)

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert len(pushed_refs) == 2
        assert pushed_refs[0] != pushed_refs[1]

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is not None
            assert rec.branch_name == pushed_refs[1]

        ls_remote = real_run(
            ["git", "ls-remote", bare_path],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"refs/heads/{pushed_refs[1]}" in ls_remote.stdout
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_pending_verification_hides_stdout_for_non_privileged_agent(client, db_engine, auth_headers):
    repo_name = "traceability-verification-list"

    api_key = auth_headers["X-API-Key"]
    api_key_prefix = get_api_key_prefix(api_key)

    with Session(db_engine) as s:
        agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
        assert agent is not None
        agent_id = str(agent.id)

        bounty = Bounty(
            title="manual verify",
            description="",
            reward=1,
            status=BountyStatus.SUBMITTED.value,
            repo_name=repo_name,
            required_role="contributor",
            assignee=agent_id,
            test_command="pytest",
            verification_mode="human",
        )
        s.add(bounty)
        s.commit()
        s.refresh(bounty)

        rec = CommitRecord(
            repo_name=repo_name,
            commit_sha="abc123",
            agent_id=agent_id,
            bounty_id=bounty.id,
            branch_name="agent/x/demo",
            status="pending",
            model_name="m",
            intent_category="fix",
            intent_description="d",
            diff_summary="s",
            trace_json={"k": "v"},
            verification_exit_code=1,
            verification_stdout="sensitive log",
        )
        s.add(rec)
        s.commit()

    # Override dependency directly for this test
    from main import app
    app.dependency_overrides[require_active_identity] = lambda: type("AgentIdentity", (), {"role": "contributor"})()
    try:
        res = client.get("/api/v1/commits/pending/verification", headers=auth_headers)
        assert res.status_code == 200, res.text
        data = res.json()
        assert isinstance(data, list)
        item = next((x for x in data if x["repo_name"] == repo_name), None)
        assert item is not None
        assert item["verification_stdout"] is None
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_execution_guard_rejects_python_inline_execution():
    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command('python -c "print(1)"')


def test_execution_guard_estimate_cost_increases_with_timeout_and_command_complexity():
    short_simple = commits_router.ExecutionGuard.estimate_cost(
        is_new_session=False,
        command_count=1,
        timeout_seconds=60,
        command_str="pytest",
        sandbox_provider="disabled",
    )
    long_complex = commits_router.ExecutionGuard.estimate_cost(
        is_new_session=False,
        command_count=1,
        timeout_seconds=900,
        command_str="pytest -q -k auth and not slow",
        sandbox_provider="disabled",
    )

    assert long_complex > short_simple


def test_execution_guard_estimate_cost_increases_for_new_session_and_runner_provider():
    base = commits_router.ExecutionGuard.estimate_cost(
        is_new_session=False,
        command_count=1,
        timeout_seconds=300,
        command_str="pytest",
        sandbox_provider="subprocess",
    )
    new_session_runner = commits_router.ExecutionGuard.estimate_cost(
        is_new_session=True,
        command_count=1,
        timeout_seconds=300,
        command_str="pytest",
        sandbox_provider="runner",
    )

    assert new_session_runner > base


def test_execution_guard_estimate_cost_has_floor_for_invalid_inputs():
    estimate = commits_router.ExecutionGuard.estimate_cost(
        is_new_session=False,
        command_count=0,
        timeout_seconds=0,
        command_str="",
        sandbox_provider="unknown",
        cpu_cores=-1,
    )

    assert estimate >= 0.0005


def test_commit_budget_estimation_uses_bounty_estimated_hours(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-budget-estimated-hours"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="budget estimated hours",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest -q",
                verification_mode="human",
                estimated_hours=2,
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        captured = {}

        original_estimate_cost = commits_router.ExecutionGuard.estimate_cost

        def capture_estimate_cost(**kwargs):
            captured.update(kwargs)
            return original_estimate_cost(**kwargs)

        monkeypatch.setattr(commits_router.ExecutionGuard, "estimate_cost", capture_estimate_cost)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "budget uses estimated_hours\n"},
            "intent_description": "verify budget timeout mapping",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "budget estimation coverage",
            "reasoning_trace": ["map estimated_hours to timeout_seconds"],
            "rejected_alternatives": ["fixed timeout for all bounties"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text

        assert captured.get("timeout_seconds") == 7200
        assert captured.get("command_str") == "pytest -q"
        assert captured.get("is_new_session") is True
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_execution_guard_rejects_python_module_policy():
    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("python -m pytest -q")

    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("python -m pip list")



def test_execution_guard_rejects_legacy_test_runners():
    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("tox")

    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("nose")



def test_execution_guard_rejects_python_without_pytest_module():
    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("python")

    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("python script.py")



def test_execution_guard_rejects_python_pytest_positional_target():
    with pytest.raises(ValueError, match="not in the whitelist"):
        commits_router.ExecutionGuard.verify_command("python -m pytest tests/test_sample.py")


def test_execution_guard_sanitize_output_masks_common_secret_patterns():
    raw = "\n".join(
        [
            "authorization: Bearer abcdefghijklmnop",
            "token=plainsecretvalue123",
            "access_token: xyzxyzxyzxyz",
            "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signaturepart",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "sk-abcdefghijklmnopqrstuvwxyz0123456789",
            "url https://example.com/callback?token=abc123&foo=bar",
        ]
    )

    sanitized = commits_router.ExecutionGuard.sanitize_output(raw, max_length=4000)

    assert "abcdefghijklmnop" not in sanitized
    assert "plainsecretvalue123" not in sanitized
    assert "xyzxyzxyzxyz" not in sanitized
    assert "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signaturepart" not in sanitized
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in sanitized
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in sanitized
    assert "?token=abc123" not in sanitized
    assert "[MASKED" in sanitized


def test_execution_guard_sanitize_output_masks_private_key_block():
    raw = """prefix
-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBK...
-----END PRIVATE KEY-----
suffix"""

    sanitized = commits_router.ExecutionGuard.sanitize_output(raw, max_length=4000)

    assert "BEGIN PRIVATE KEY" not in sanitized
    assert "MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBK" not in sanitized
    assert "[MASKED_PRIVATE_KEY]" in sanitized


def test_execution_guard_sanitize_output_uses_head_tail_truncation():
    raw = "head-" + ("x" * 500) + "-tail"

    sanitized = commits_router.ExecutionGuard.sanitize_output(raw, max_length=80)

    assert "head-" in sanitized
    assert "-tail" in sanitized
    assert "[TRUNCATED" in sanitized
    assert len(sanitized) > 80


def test_verify_endpoint_returns_501_when_governance_enforce(client, auth_headers, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "enforce")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
        headers=auth_headers,
    )
    assert res.status_code == 501
    assert res.json().get("detail") == GOVERNANCE_ENFORCE_VERIFY_NOT_IMPLEMENTED_DETAIL


def test_verify_endpoint_rejects_python_inline_execution(client, auth_headers):
    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": 'python -c "print(1)"'},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "not in the whitelist" in (res.json().get("detail") or "")


def test_verify_endpoint_rejects_unapproved_python_module(client, auth_headers):
    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": "python -m pip list"},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "not in the whitelist" in (res.json().get("detail") or "")


def test_verify_endpoint_returns_409_when_runner_provider_enabled(client, auth_headers, monkeypatch):
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "runner")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
        headers=auth_headers,
    )
    assert res.status_code == 409
    assert res.json().get("detail") == "Local verify endpoint is unavailable when APP_SANDBOX_PROVIDER=runner"


def test_verify_endpoint_returns_503_when_disabled_provider(client, auth_headers, monkeypatch):
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "disabled")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
        headers=auth_headers,
    )
    assert res.status_code == 503
    assert res.json().get("detail") == "Sandbox is disabled"


def test_verify_endpoint_returns_503_when_subprocess_not_allowed_in_strict_mode(client, auth_headers, monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    res = client.post(
        "/api/v1/verify",
        params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
        headers=auth_headers,
    )

    assert res.status_code == 503
    assert res.json().get("detail") == "Subprocess sandbox is not allowed in strict security mode"


def test_verify_endpoint_returns_503_when_subprocess_not_initialized(client, auth_headers, monkeypatch):
    from main import app

    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    original_sandbox = getattr(app.state, "sandbox", None)
    app.state.sandbox = None
    try:
        res = client.post(
            "/api/v1/verify",
            params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
            headers=auth_headers,
        )
    finally:
        app.state.sandbox = original_sandbox

    assert res.status_code == 503
    assert res.json().get("detail") == "Subprocess sandbox is not initialized"


def test_verify_endpoint_returns_503_when_session_manager_not_initialized(client, auth_headers, monkeypatch):
    from main import app

    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    original_sandbox = getattr(app.state, "sandbox", None)
    original_session_manager = getattr(app.state, "session_manager", None)
    app.state.sandbox = object()
    app.state.session_manager = None
    try:
        res = client.post(
            "/api/v1/verify",
            params={"repo_name": "does-not-matter", "cmd": "pytest -q"},
            headers=auth_headers,
        )
    finally:
        app.state.sandbox = original_sandbox
        app.state.session_manager = original_session_manager

    assert res.status_code == 503
    assert res.json().get("detail") == "Session manager is not initialized"


def test_commit_verify_external_returns_403_when_governance_enforce(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "enforce")
    from core.settings import clear_settings_cache

    clear_settings_cache()

    res = client.post(
        "/api/v1/commits/1/verify/external",
        json={"exit_code": 0, "stdout": "ok"},
    )

    assert res.status_code == 403
    assert res.json().get("detail") == GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL


def test_verify_endpoint_logs_use_head_tail_truncation(client, auth_headers, monkeypatch):
    from main import app

    repo_name = "verify-log-truncation"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        monkeypatch.setenv("APP_SECURITY_MODE", "warn")
        monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
        monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")
        from core.settings import clear_settings_cache

        clear_settings_cache()

        original_sandbox = getattr(app.state, "sandbox", None)
        original_session_manager = getattr(app.state, "session_manager", None)

        calls = {}

        class _SessionManagerStub:
            def get_or_create_session(self, agent_id, task_id, repo_path):
                calls["create"] = (agent_id, task_id, repo_path)
                return "sid-1"

            def execute_with_status(self, agent_id, task_id, command):
                calls["execute"] = (agent_id, task_id, command)
                return 1, "HEAD-" + ("z" * 4000) + "-TAIL"

            def close_session(self, agent_id, task_id):
                calls["close"] = (agent_id, task_id)

        app.state.sandbox = object()
        app.state.session_manager = _SessionManagerStub()
        try:
            res = client.post(
                "/api/v1/verify",
                params={"repo_name": repo_name, "cmd": "pytest -q"},
                headers=auth_headers,
            )
        finally:
            app.state.sandbox = original_sandbox
            app.state.session_manager = original_session_manager

        assert res.status_code == 200, res.text
        body = res.json() or {}
        assert body.get("exit_code") == 1
        assert body.get("passed") is False
        assert calls["create"][1] == f"verify:{repo_name}"
        assert calls["execute"][1] == f"verify:{repo_name}"
        assert calls["execute"][2] == "pytest -q"
        assert calls["close"][1] == f"verify:{repo_name}"
        logs = body.get("logs") or ""
        assert "HEAD-" in logs
        assert "-TAIL" in logs
        assert "[TRUNCATED" in logs
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_trace_validator_requires_timezone_aware_timestamp_when_enabled():
    payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip timezone validation"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00",
    }

    with pytest.raises(ValueError, match="timezone-aware"):
        TraceValidator.validate_commit(payload, require_timezone_aware_timestamp=True)


def test_trace_validator_enforces_commit_sha_when_expected():
    payload = {
        "protocol_version": "1.0",
        "commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip commit_sha match check"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="does not match expected"):
        TraceValidator.validate_commit(payload, expected_commit_sha="c" * 40, require_commit_sha=True)


def test_trace_validator_requires_non_empty_intent_vector():
    payload = {
        "protocol_version": "1.0",
        "commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip intent vector checks"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": []},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match=r"intent\.vector"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_requires_non_empty_rejected_alternatives():
    payload = {
        "protocol_version": "1.0",
        "commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": [],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="rejected_alternatives"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_requires_protocol_version():
    payload = {
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip protocol versioning"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="protocol_version"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_unsupported_protocol_version():
    payload = {
        "protocol_version": "2.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip protocol version gate"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="unsupported 'protocol_version'"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_mismatched_reasoning_hash():
    payload = {
        "protocol_version": "1.0",
        "tree_hash": "f" * 40,
        "diff_hash": "0" * 64,
        "reasoning_hash": "1" * 64,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip integrity hash check"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload["binding_hash"] = compute_binding_hash(payload)

    with pytest.raises(ValueError, match="reasoning_hash"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_mismatched_binding_hash():
    payload = {
        "protocol_version": "1.0",
        "tree_hash": "f" * 40,
        "diff_hash": "0" * 64,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip integrity hash check"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])
    payload["binding_hash"] = compute_binding_hash(payload)
    payload["binding_hash"] = "f" * 64

    with pytest.raises(ValueError, match="binding_hash"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_allows_attestable_doc_references_and_env_vars_accessed():
    payload = {
        "protocol_version": "1.0",
        "tree_hash": "f" * 40,
        "diff_hash": "0" * 64,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip context policy"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": ["internal://spec/checkout-flow"],
            "env_vars_accessed": ["TRACE_COMMIT_SIGNING_SECRET"],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])

    validated = TraceValidator.validate_commit(payload)
    assert validated.context_snapshot.doc_references == ["internal://spec/checkout-flow"]
    assert validated.context_snapshot.env_vars_accessed == ["TRACE_COMMIT_SIGNING_SECRET"]


def test_trace_validator_rejects_non_attestable_doc_reference_format():
    payload = {
        "protocol_version": "1.0",
        "tree_hash": "f" * 40,
        "diff_hash": "0" * 64,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip context policy"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": ["../spec.md"],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])

    with pytest.raises(ValueError, match="doc_references"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_non_uppercase_env_var_name():
    payload = {
        "protocol_version": "1.0",
        "tree_hash": "f" * 40,
        "diff_hash": "0" * 64,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip context policy"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": ["internal_api_token"],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload["reasoning_hash"] = compute_reasoning_hash(payload["reasoning_trace"])

    with pytest.raises(ValueError, match="env_vars_accessed"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_short_diff_summary():
    payload = {
        "protocol_version": "1.0",
        "diff_summary": "short",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip summary quality gate"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="diff_summary"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_file_path_traversal_segment():
    payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip path policy"],
        "context_snapshot": {
            "file_paths": ["../secret.txt"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="traversal"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_author_agent_id_with_whitespace():
    payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip author id policy"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a 1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match=r"author\.agent_id"):
        TraceValidator.validate_commit(payload)


def test_trace_validator_rejects_timestamp_too_far_in_future():
    payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip timestamp sanity check"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2099-01-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="too far in the future"):
        TraceValidator.validate_commit(payload, require_timezone_aware_timestamp=True)


def test_signing_secret_does_not_fallback_to_internal_api_token(monkeypatch):
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-token-for-signing")
    assert get_trace_signing_secret() is None


def test_signing_secret_prefers_trace_secret(monkeypatch):
    monkeypatch.setenv("TRACE_COMMIT_SIGNING_SECRET", "trace-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-token-for-signing")
    assert get_trace_signing_secret() == "trace-secret"


def test_signing_secret_returns_none_when_all_missing(monkeypatch):
    monkeypatch.delenv("TRACE_COMMIT_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    assert get_trace_signing_secret() is None


def test_verify_trace_signature_detects_tampered_author_agent_id():
    payload = {
        "protocol_version": "1.0",
        "commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["allow unsigned trace payload"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "agent-a", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    secret = "trace-secret"
    payload["signature"] = sign_trace_commit(payload, secret)

    tampered = {**payload, "author": {"agent_id": "agent-b", "model_name": "m1"}}
    assert not verify_trace_commit_signature(tampered, secret)


def test_verify_trace_signature_detects_tampered_model_name():
    payload = {
        "protocol_version": "1.0",
        "commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["skip signature integrity checks"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "agent-a", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    secret = "trace-secret"
    payload["signature"] = sign_trace_commit(payload, secret)

    tampered = {**payload, "author": {"agent_id": "agent-a", "model_name": "m2"}}
    assert not verify_trace_commit_signature(tampered, secret)


def test_pull_request_spec_requires_tests_for_fix_and_feat():
    trace_payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["ship without alternatives"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "fix", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    for pr_type in ["fix", "feat"]:
        pr_payload = {
            "title": "PR",
            "type": pr_type,
            "source_branch": "agent/x/branch",
            "tests_added": False,
            "commits": [trace_payload],
        }
        with pytest.raises(ValueError, match="tests_added"):
            TraceValidator.validate_pull_request_spec(pr_payload)


def test_git_tree_service_commit_message_is_trace_commit_json(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-git-tree-service-trace-commit"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="tree sync task",
                description="",
                reward=1,
                status=BountyStatus.OPEN.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=None,
                test_command="pytest",
                verification_mode="human",
            )
            s.add(bounty)
            s.commit()

        pushed = {"called": False, "ref": None}
        generated_commit_message = {"value": None}

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if (
                isinstance(cmd, (list, tuple))
                and len(cmd) >= 4
                and cmd[0] == "git"
                and cmd[1] == "push"
                and cmd[2] == "origin"
            ):
                pushed["called"] = True
                pushed["ref"] = cmd[3]
                generated_commit_message["value"] = real_run(
                    ["git", "log", "-1", "--format=%B", "HEAD"],
                    cwd=kwargs.get("cwd"),
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with Session(db_engine) as s:
            tree_service = GitTreeService(s, STORE_ROOT)
            tree_service.sync_repo_task_tree(repo_name, agent_id)

        assert pushed["called"]
        assert pushed["ref"]
        assert pushed["ref"].startswith("system/task-tree-sync/task-tree-")
        assert generated_commit_message["value"]
        payload = json.loads(generated_commit_message["value"])

        assert payload.get("protocol_version") == "1.0"
        assert payload.get("diff_summary") == "update BOUNTY_TREE.md task graph visualization"
        assert payload.get("author", {}).get("agent_id") == GitTreeService.SYSTEM_TASK_TREE_AGENT_ID
        assert payload.get("automation", {}).get("triggered_by_agent_id") == agent_id
        assert payload.get("context_snapshot", {}).get("file_paths") == ["BOUNTY_TREE.md"]
        assert payload.get("signature")
        assert payload.get("tree_hash")
        assert payload.get("diff_hash")

        signing_secret = get_trace_signing_secret()
        assert signing_secret
        assert verify_trace_commit_signature(payload, signing_secret)
        TraceValidator.validate_commit(payload, require_timezone_aware_timestamp=True)
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_surfaces_task_tree_sync_failure_and_persists_in_trace_json(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-task-tree-sync-failure"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "task tree sync should fail\n"},
            "intent_description": "surface task tree sync failures",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "persist task tree sync failure state",
            "reasoning_trace": ["simulate sync failure after commit record persist"],
            "rejected_alternatives": ["swallow sync failure in warning only"],
        }

        def fake_sync(self, repo_name_arg, trusted_agent_id="system"):
            raise RuntimeError("simulated task tree sync failure")

        monkeypatch.setattr(GitTreeService, "sync_repo_task_tree", fake_sync)

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        body = res.json()
        sync_state = body.get("task_tree_sync") or {}
        assert sync_state.get("attempted") is True
        assert sync_state.get("status") == "failed"
        assert "simulated task tree sync failure" in (sync_state.get("error") or "")

        with Session(db_engine) as s:
            rec = s.exec(select(CommitRecord).where(CommitRecord.repo_name == repo_name).order_by(CommitRecord.id.desc())).first()
            assert rec is not None
            persisted_sync = (rec.trace_json or {}).get("task_tree_sync") or {}
            assert persisted_sync.get("status") == "failed"
            assert "simulated task tree sync failure" in (persisted_sync.get("error") or "")
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_auto_verification_without_local_sandbox_is_deferred(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-auto-verification-deferred"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="auto verify deferred",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest -q",
                verification_mode="auto",
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        monkeypatch.setenv("APP_SANDBOX_PROVIDER", "disabled")
        from core.settings import clear_settings_cache

        clear_settings_cache()

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "auto verification deferred\n"},
            "intent_description": "avoid host execution for auto verification",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "defer auto verification when local sandbox disabled",
            "reasoning_trace": ["auto verification should not execute on host"],
            "rejected_alternatives": ["return 503 and block submission"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        verification = (res.json() or {}).get("verification") or {}
        assert verification.get("exit_code") is None
        assert verification.get("passed") is None
        assert verification.get("execution_mode") == ExecutionMode.SHARED_LOCAL.value
        assert verification.get("execution_mode_source") == "default"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is not None
            assert rec.verification_exit_code is None
            assert rec.verification_stdout == "Auto verification deferred: local sandbox verification is disabled"

        pending = client.get("/api/v1/commits/pending/verification", headers=auth_headers)
        assert pending.status_code == 200, pending.text
        items = pending.json()
        assert isinstance(items, list)
        assert not any(item.get("repo_name") == repo_name for item in items)
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_commit_auto_verification_runner_provider_sets_runner_message(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-auto-verification-runner-provider"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_uuid = agent.id
            agent_id = str(agent.id)

            bounty = Bounty(
                title="auto verify runner provider",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest -q",
                verification_mode="auto",
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

            owner_user_id = uuid4()
            s.add(UserAgentBinding(user_id=owner_user_id, agent_id=agent_uuid))
            s.commit()

        monkeypatch.setenv("APP_SANDBOX_PROVIDER", "runner")
        from core.settings import clear_settings_cache

        clear_settings_cache()

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "runner provider deferred\n"},
            "intent_description": "set runner verification delegation message",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "defer auto verification to runner provider",
            "reasoning_trace": ["runner provider should delegate verification"],
            "rejected_alternatives": ["execute tests locally in API process"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text
        verification = (res.json() or {}).get("verification") or {}
        runner_job_id = verification.get("runner_job_id")
        assert isinstance(runner_job_id, str) and runner_job_id
        assert verification.get("execution_mode") == ExecutionMode.SELF_HOSTED.value
        assert verification.get("execution_mode_source") == "sandbox_provider"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is not None
            assert rec.verification_exit_code is None
            assert rec.verification_stdout == (
                "Runner-based verification required: auto verification is delegated to runner polling"
            )

            job = s.exec(
                select(ComputeJob)
                .where(ComputeJob.submission_id == str(rec.id))
                .order_by(ComputeJob.created_at.desc())
            ).first()
            assert job is not None
            assert str(job.id) == runner_job_id
            assert job.bounty_id == bounty_id
            assert job.execution_mode == ExecutionMode.SELF_HOSTED
            assert job.status == ComputeJobStatus.PENDING
            assert job.code_branch == rec.branch_name
            assert job.code_commit == rec.commit_sha
            assert job.test_command == "pytest -q"
            assert job.requester_agent_id == agent_uuid
            assert job.requester_user_id == owner_user_id
            assert job.requester_type == "agent"
            assert job.timeout_seconds == 3600
            assert (job.env_vars or {}).get("repo_name") == repo_name
            assert (job.env_vars or {}).get("branch_name") == rec.branch_name
            assert (job.env_vars or {}).get("trace_commit_sha") == rec.commit_sha
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)



def test_commit_auto_verification_repo_execution_config_self_hosted_queues_runner_job(
    client, db_engine, auth_headers, monkeypatch
):
    repo_name = "traceability-auto-verification-self-hosted-config"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_uuid = agent.id
            agent_id = str(agent.id)

            repo = Repo(
                full_name=f"trace/{repo_name}",
                name=repo_name,
                owner="trace-owner",
            )
            s.add(repo)
            s.commit()
            s.refresh(repo)
            repo_id = repo.id
            repo_id_str = str(repo.id)

            bounty = Bounty(
                title="auto verify self-hosted config",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                repo_id=repo_id_str,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest -q",
                verification_mode="auto",
                estimated_hours=2,
                current_steps=0,
                max_steps=5,
            )
            s.add(bounty)

            repo_cfg = RepoExecutionConfig(repo_id=repo_id, execution_mode=ExecutionMode.SELF_HOSTED)
            s.add(repo_cfg)

            owner_user_id = uuid4()
            s.add(UserAgentBinding(user_id=owner_user_id, agent_id=agent_uuid))

            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        monkeypatch.setenv("APP_SANDBOX_PROVIDER", "disabled")
        from core.settings import clear_settings_cache

        clear_settings_cache()

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "self-hosted config job\n"},
            "intent_description": "queue runner job via repo execution config",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "self-hosted execution mode should enqueue job",
            "reasoning_trace": ["repo_execution_config has higher priority than provider fallback"],
            "rejected_alternatives": ["only queue jobs when provider=runner"],
            "bounty_id": bounty_id,
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text

        verification = (res.json() or {}).get("verification") or {}
        runner_job_id = verification.get("runner_job_id")
        assert isinstance(runner_job_id, str) and runner_job_id
        assert verification.get("execution_mode") == ExecutionMode.SELF_HOSTED.value
        assert verification.get("execution_mode_source") == "repo_execution_config"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is not None
            assert rec.verification_stdout == (
                "Runner-based verification required: auto verification is delegated to runner polling"
            )

            job = s.exec(
                select(ComputeJob)
                .where(ComputeJob.submission_id == str(rec.id))
                .order_by(ComputeJob.created_at.desc())
            ).first()
            assert job is not None
            assert str(job.id) == runner_job_id
            assert job.bounty_id == bounty_id
            assert job.repo_id == repo_id
            assert job.execution_mode == ExecutionMode.SELF_HOSTED
            assert job.status == ComputeJobStatus.PENDING
            assert job.requester_agent_id == agent_uuid
            assert job.requester_user_id == owner_user_id
            assert job.timeout_seconds == 7200
            assert job.code_branch == rec.branch_name
            assert job.code_commit == rec.commit_sha
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)



def test_commit_compacts_reasoning_trace_for_git_message_when_payload_too_large(client, db_engine, auth_headers):
    repo_name = "traceability-commit-message-too-large"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        long_step = "x" * 2000
        reasoning_steps_needed = (TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES // 2000) + 8
        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "oversized trace\n"},
            "intent_description": "compact oversized trace commit message payload",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "compact oversize trace commit payload before git commit",
            "reasoning_trace": [long_step for _ in range(reasoning_steps_needed)],
            "rejected_alternatives": ["keep unbounded trace payload in git commit message"],
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text

        body = res.json()
        assert body.get("success") is True

        with Session(db_engine) as s:
            rec = s.exec(select(CommitRecord).where(CommitRecord.repo_name == repo_name).order_by(CommitRecord.id.desc())).first()
            assert rec is not None
            trace_json = rec.trace_json or {}
            git_message_trace = trace_json.get("git_message_trace") or {}
            assert git_message_trace.get("reasoning_trace_compacted") is True
            compact_reasoning_trace = git_message_trace.get("reasoning_trace")
            assert isinstance(compact_reasoning_trace, list)
            assert len(compact_reasoning_trace) == 3
            assert compact_reasoning_trace[0].startswith("Reasoning trace abbreviated")
            assert (trace_json.get("reasoning_trace") or []) == req["reasoning_trace"]
            assert trace_json.get("reasoning_hash") == compute_reasoning_hash(req["reasoning_trace"])
            assert trace_json.get("signature")
            signing_secret = get_trace_signing_secret()
            assert signing_secret
            assert verify_trace_commit_signature(trace_json, signing_secret)

            trace_from_git = subprocess.run(
                ["git", "show", "-s", "--format=%B", rec.commit_sha],
                cwd=bare_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            trace_from_git_json = json.loads(trace_from_git)
            git_reasoning = trace_from_git_json.get("reasoning_trace") or []
            assert len(git_reasoning) == 3
            assert isinstance(git_reasoning[0], str)
            assert git_reasoning[0].startswith("Reasoning trace abbreviated")
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)


def test_pull_request_spec_allows_tests_flag_for_other_types():
    trace_payload = {
        "protocol_version": "1.0",
        "diff_summary": "summary long enough",
        "reasoning_trace": ["step1"],
        "rejected_alternatives": ["ship without alternatives"],
        "context_snapshot": {
            "file_paths": ["a.py"],
            "doc_references": [],
            "env_vars_accessed": [],
            "library_versions": {},
        },
        "intent": {"description": "do thing", "category": "refactor", "vector": [0.0]},
        "author": {"agent_id": "a1", "model_name": "m1"},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    pr_payload = {
        "title": "PR",
        "type": "refactor",
        "source_branch": "agent/x/branch",
        "tests_added": False,
        "commits": [trace_payload],
    }
    pr = TraceValidator.validate_pull_request_spec(pr_payload)
    assert pr.type == "refactor"
    assert pr.tests_added is False


def test_commit_enforce_quality_gate_rejects_weak_trace_before_push(client, db_engine, auth_headers, monkeypatch):
    repo_name = "traceability-quality-gate-enforce"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    monkeypatch.setenv("TRACE_COMMIT_QUALITY_GATE", "enforce")

    try:
        _init_repo_with_one_commit(bare_path)

        api_key = auth_headers["X-API-Key"]
        api_key_prefix = get_api_key_prefix(api_key)

        with Session(db_engine) as s:
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "weak trace should be blocked\n"},
            "intent_description": "reject weak trace when quality gate is enforce",
            "intent_category": "fix",
            "intent_vector": [0.0],
            "diff_summary": "add weak trace payload",
            "reasoning_trace": ["single step"],
            "rejected_alternatives": ["skip quality checks"],
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 400, res.text
        body = res.json()
        detail = body.get("detail")
        assert isinstance(detail, dict)
        assert detail.get("message") == "Trace quality gate failed"
        warnings = detail.get("quality_warnings")
        assert isinstance(warnings, list)
        assert any("Weak Reasoning" in w for w in warnings)

        with Session(db_engine) as s:
            rec = s.exec(select(CommitRecord).where(CommitRecord.repo_name == repo_name).order_by(CommitRecord.id.desc())).first()
            assert rec is None

        ls_remote = subprocess.run(
            ["git", "ls-remote", bare_path],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"refs/heads/agent/{agent_id}/" not in ls_remote.stdout
    finally:
        monkeypatch.delenv("TRACE_COMMIT_QUALITY_GATE", raising=False)
        shutil.rmtree(bare_path, ignore_errors=True)
