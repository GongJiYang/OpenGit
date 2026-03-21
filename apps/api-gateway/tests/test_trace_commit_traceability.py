import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from sqlmodel import Session, select

from dependencies.auth import require_active_identity
from persistence import Bounty, BountyStatus, CommitRecord

from agent_auth.models import Agent
from routers import commits as commits_router
from agent_auth.utils import get_api_key_prefix
from core.security import STORE_ROOT



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
            "diff_summary": "add traceability payload",
            "reasoning_trace": [
                "collect parent sha before commit",
                "persist timestamp deterministically",
                "persist commit_sha after commit"
            ]
        }

        res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert res.status_code == 200, res.text

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
            "diff_summary": "guard before push",
            "reasoning_trace": ["precheck bounty before side effects"],
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
            "diff_summary": "simulate push failure status",
            "reasoning_trace": ["force git push failure"],
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
        (subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=b"fatal"), 500, "Git operation failed"),
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
            "diff_summary": "ensure non-200 errors",
            "reasoning_trace": ["raise injected failure"],
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


def test_commit_db_persist_failure_returns_500_after_git_push(client, db_engine, auth_headers, monkeypatch):
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

        req = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "db fail after push\n"},
            "intent_description": "simulate db persist failure",
            "intent_category": "fix",
            "diff_summary": "ensure git/db split is visible",
            "reasoning_trace": ["force commit record insert failure"],
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
        assert res.status_code == 500, res.text
        body = res.json()
        assert body.get("detail") == "Commit persisted to git, but failed to record history"

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is None

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
            "diff_summary": "first submit",
            "reasoning_trace": ["first"],
            "bounty_id": bounty_id,
        }
        req2 = {
            "agent_id": agent_id,
            "model_name": "traceability-test-model",
            "files": {"trace.txt": "second submit\n"},
            "intent_description": "second",
            "intent_category": "fix",
            "diff_summary": "second submit",
            "reasoning_trace": ["second"],
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
    with pytest.raises(ValueError, match="Inline python execution is not allowed"):
        commits_router.ExecutionGuard.verify_command('python -c "print(1)"')


def test_execution_guard_python_module_policy():
    tokens = commits_router.ExecutionGuard.verify_command("python -m pytest -q")
    assert tokens[:3] == ["python", "-m", "pytest"]

    with pytest.raises(ValueError, match="python -m only allows"):
        commits_router.ExecutionGuard.verify_command("python -m pip list")


def test_app_verify_allowlist_aligns_with_execution_guard():
    from app_factory import ALLOWED_TEST_COMMANDS

    assert set(ALLOWED_TEST_COMMANDS) == commits_router.ExecutionGuard.ALLOWED_TEST_COMMANDS
