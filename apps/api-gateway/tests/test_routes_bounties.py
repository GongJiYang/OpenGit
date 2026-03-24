import os
import shutil
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from main import app  # noqa: E402
from core.settings import clear_settings_cache  # noqa: E402
from core.security import STORE_ROOT  # noqa: E402
from agent_auth.models.platform import Repo, RepoMember, MembershipStatus, RepoRole  # noqa: E402
from agent_auth.models import Agent, AgentStatus  # noqa: E402
from agent_auth.utils import get_api_key_prefix, API_KEY_PREFIX, API_KEY_LENGTH  # noqa: E402
from git_tree_service.service import GitTreeService  # noqa: E402
from persistence import Bounty, BountyStatus  # noqa: E402


@pytest.fixture()
def _set_required_security_env(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret")
    monkeypatch.setenv("WECHAT_TOKEN", "test-wechat-token")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-github-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-github-client-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    clear_settings_cache()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_list_bounties_defaults_to_open(_set_required_security_env, client: TestClient):
    r = client.get("/api/v1/bounties")
    assert r.status_code == 200
    # Should return a list (empty or items with status 'open')
    assert isinstance(r.json(), list)


def test_claim_preparation_notes_not_in_description(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("p" * API_KEY_LENGTH)
    contributor_key = API_KEY_PREFIX + ("q" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-preparation-notes"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-preparation",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="PREP01",
            claim_url="/api/v1/agents/claim/PREP01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        contributor = Agent(
            name="contributor-agent-preparation",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="PREP02",
            claim_url="/api/v1/agents/claim/PREP02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(architect)
        session.add(contributor)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-preparation-notes",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(contributor)
        session.refresh(repo)
        contributor_id = str(contributor.id)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.commit()

    create_payload = {
        "title": "Preparation notes separation",
        "description": "Original task definition should stay clean.",
        "reward": 100,
        "repo_name": repo_full_name,
        "required_role": "contributor",
        "test_command": "pytest",
        "verification_mode": "auto",
    }

    created_res = client.post(
        "/api/v1/bounties",
        json=create_payload,
        headers={"X-API-Key": architect_key},
    )
    assert created_res.status_code == 200, created_res.text
    bounty_id = created_res.json()["id"]

    with Session(db_engine) as session:
        bounty_before = session.get(Bounty, bounty_id)
        assert bounty_before is not None
        bounty_before.status = BountyStatus.PENDING.value
        session.add(bounty_before)
        session.commit()

    mark_res = client.post(
        f"/api/v1/bounties/{bounty_id}/mark-preparable",
        headers={"X-API-Key": architect_key},
    )
    assert mark_res.status_code == 200, mark_res.text

    notes = "Prep note: identify migration impact and test matrix"
    claim_res = client.post(
        f"/api/v1/bounties/{bounty_id}/claim-preparation",
        json={
            "agent_id": contributor_id,
            "preparation_notes": notes,
        },
        headers={"X-API-Key": contributor_key},
    )

    # If payload agent_id used fallback, assert success and inspect persisted model directly.
    assert claim_res.status_code == 200, claim_res.text

    with Session(db_engine) as session:
        bounty = session.get(Bounty, bounty_id)
        assert bounty is not None
        assert bounty.status == BountyStatus.READY_FOR_PREPARATION.value
        assert bounty.description == "Original task definition should stay clean."
        assert bounty.preparation_notes
        assert bounty.preparation_notes[-1]["notes"] == notes
        assert bounty.preparation_notes[-1]["agent_id"] == contributor_id


def test_create_bounty_persists_canonical_repo_id(_set_required_security_env, client: TestClient, db_engine):
    raw_api_key = API_KEY_PREFIX + ("c" * API_KEY_LENGTH)

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH01",
            claim_url="/api/v1/agents/claim/ARCH01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name="owner/repo-bounty-route-test",
            owner="owner",
            name="repo-bounty-route-test",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)
        expected_repo_id = str(repo.id)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    payload = {
        "title": "Canonical repo id",
        "description": "",
        "reward": 100,
        "repo_name": "owner/repo-bounty-route-test",
        "required_role": "contributor",
        "test_command": "pytest",
        "verification_mode": "auto",
    }

    response = client.post(
        "/api/v1/bounties",
        json=payload,
        headers={"X-API-Key": raw_api_key},
    )
    assert response.status_code == 200
    created = response.json()
    assert created["repo_name"] == "owner/repo-bounty-route-test"
    assert created["repo_id"] == expected_repo_id


def test_create_decomposed_bounties_surfaces_task_tree_sync_failure(_set_required_security_env, client: TestClient, db_engine, monkeypatch):
    raw_api_key = API_KEY_PREFIX + ("e" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-decomposed-sync"

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent-decomposed",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH03",
            claim_url="/api/v1/agents/claim/ARCH03",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-decomposed-sync",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    repo_fs_path = os.path.abspath(os.path.join(STORE_ROOT, repo_full_name))
    os.makedirs(repo_fs_path, exist_ok=True)

    def fake_sync(self, repo_name, trusted_agent_id="system"):
        raise RuntimeError("simulated decomposed task tree sync failure")

    monkeypatch.setattr(GitTreeService, "sync_repo_task_tree", fake_sync)

    payload = {
        "repo_name": repo_full_name,
        "root_task": {
            "title": "Root task",
            "description": "",
            "reward": 10,
            "required_role": "contributor",
            "dependencies": [],
            "children": [],
            "test_command": "pytest",
            "verification_mode": "auto",
        },
    }

    try:
        response = client.post(
            "/api/v1/bounties/decomposed",
            json=payload,
            headers={"X-API-Key": raw_api_key},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        sync_state = body.get("task_tree_sync") or {}
        assert sync_state.get("attempted") is True
        assert sync_state.get("status") == "failed"
        assert "simulated decomposed task tree sync failure" in (sync_state.get("error") or "")
    finally:
        shutil.rmtree(repo_fs_path, ignore_errors=True)



def test_create_bounty_rejects_python_inline_test_command(_set_required_security_env, client: TestClient, db_engine):
    raw_api_key = API_KEY_PREFIX + ("f" * API_KEY_LENGTH)

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent-inline-cmd",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH04",
            claim_url="/api/v1/agents/claim/ARCH04",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name="owner/repo-bounty-inline-cmd",
            owner="owner",
            name="repo-bounty-inline-cmd",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    payload = {
        "title": "Inline python should be rejected",
        "description": "",
        "reward": 100,
        "repo_name": "owner/repo-bounty-inline-cmd",
        "required_role": "contributor",
        "test_command": 'python -c "print(1)"',
        "verification_mode": "auto",
    }

    response = client.post(
        "/api/v1/bounties",
        json=payload,
        headers={"X-API-Key": raw_api_key},
    )
    assert response.status_code == 400
    assert "not in the whitelist" in (response.json().get("detail") or "")


def test_create_decomposed_bounties_rejects_inline_python_test_command(_set_required_security_env, client: TestClient, db_engine):
    raw_api_key = API_KEY_PREFIX + ("g" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-decomposed-inline-cmd"

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent-decomposed-inline-cmd",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH05",
            claim_url="/api/v1/agents/claim/ARCH05",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-decomposed-inline-cmd",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    repo_fs_path = os.path.abspath(os.path.join(STORE_ROOT, repo_full_name))
    os.makedirs(repo_fs_path, exist_ok=True)

    payload = {
        "repo_name": repo_full_name,
        "root_task": {
            "title": "Root task",
            "description": "",
            "reward": 10,
            "required_role": "contributor",
            "dependencies": [],
            "children": [],
            "test_command": 'python -c "print(1)"',
            "verification_mode": "auto",
        },
    }

    try:
        response = client.post(
            "/api/v1/bounties/decomposed",
            json=payload,
            headers={"X-API-Key": raw_api_key},
        )
        assert response.status_code == 400
        assert "not in the whitelist" in (response.json().get("detail") or "")
    finally:
        shutil.rmtree(repo_fs_path, ignore_errors=True)


def test_create_decomposed_bounties_rejects_invalid_verification_mode(_set_required_security_env, client: TestClient, db_engine):
    raw_api_key = API_KEY_PREFIX + ("h" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-decomposed-invalid-verify"

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent-decomposed-invalid-verify",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH06",
            claim_url="/api/v1/agents/claim/ARCH06",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-decomposed-invalid-verify",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    repo_fs_path = os.path.abspath(os.path.join(STORE_ROOT, repo_full_name))
    os.makedirs(repo_fs_path, exist_ok=True)

    payload = {
        "repo_name": repo_full_name,
        "root_task": {
            "title": "Root task",
            "description": "",
            "reward": 10,
            "required_role": "contributor",
            "dependencies": [],
            "children": [],
            "test_command": "pytest -q",
            "verification_mode": "manual",
        },
    }

    try:
        response = client.post(
            "/api/v1/bounties/decomposed",
            json=payload,
            headers={"X-API-Key": raw_api_key},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid verification_mode"
    finally:
        shutil.rmtree(repo_fs_path, ignore_errors=True)


def test_create_bounty_rejects_repo_id_mismatch(_set_required_security_env, client: TestClient, db_engine):
    raw_api_key = API_KEY_PREFIX + ("d" * API_KEY_LENGTH)

    with Session(db_engine) as session:
        agent = Agent(
            name="architect-agent-mismatch",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="ARCH02",
            claim_url="/api/v1/agents/claim/ARCH02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name="owner/repo-bounty-mismatch",
            owner="owner",
            name="repo-bounty-mismatch",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)

        member = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(member)
        session.commit()

    payload = {
        "title": "Mismatched repo id",
        "description": "",
        "reward": 100,
        "repo_name": "owner/repo-bounty-mismatch",
        "repo_id": str(uuid4()),
        "required_role": "contributor",
        "test_command": "pytest",
        "verification_mode": "auto",
    }

    response = client.post(
        "/api/v1/bounties",
        json=payload,
        headers={"X-API-Key": raw_api_key},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "repo_id does not match repo_name"
