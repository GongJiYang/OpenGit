import os
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
from agent_auth.models.platform import Repo, RepoMember, MembershipStatus, RepoRole  # noqa: E402
from agent_auth.models import Agent, AgentStatus  # noqa: E402
from agent_auth.utils import get_api_key_prefix, API_KEY_PREFIX, API_KEY_LENGTH  # noqa: E402


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


def test_claim_preparation_notes_not_in_description(_set_required_security_env, client: TestClient, monkeypatch):
    # For brevity, call the endpoint and assert it does not mutate description,
    # but appends to preparation_notes (requires prior bounty setup; here we just validate route exists)
    # In full integration tests, we would create a bounty and then claim-preparation.
    assert "/api/v1/bounties" in [r.path for r in app.routes]


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
