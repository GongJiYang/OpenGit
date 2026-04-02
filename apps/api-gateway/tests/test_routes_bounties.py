import os
import shutil
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from main import app  # noqa: E402
from core.settings import clear_settings_cache  # noqa: E402
from core.security import STORE_ROOT  # noqa: E402
from agent_auth.models.platform import Repo, RepoMember, MembershipStatus, RepoRole, UserRole  # noqa: E402
from agent_auth.models import Agent, AgentStatus  # noqa: E402
from agent_auth.utils import get_api_key_prefix, API_KEY_PREFIX, API_KEY_LENGTH  # noqa: E402
from git_tree_service.service import GitTreeService  # noqa: E402
from persistence import AuditLog, Bounty, BountyStatus  # noqa: E402


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


def test_mark_preparable_requires_admin_or_repo_architect(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("m" * API_KEY_LENGTH)
    contributor_key = API_KEY_PREFIX + ("n" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-mark-preparable-authz"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-mark-preparable",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="MARK01",
            claim_url="/api/v1/agents/claim/MARK01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        contributor = Agent(
            name="contributor-agent-mark-preparable",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="MARK02",
            claim_url="/api/v1/agents/claim/MARK02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(architect)
        session.add(contributor)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-mark-preparable-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(contributor)
        session.refresh(repo)

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

        bounty = Bounty(
            title="pending bounty",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.PENDING.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    forbidden = client.post(
        f"/api/v1/bounties/{bounty_id}/mark-preparable",
        headers={"X-API-Key": contributor_key},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Forbidden: admin or repo architect required"

    allowed = client.post(
        f"/api/v1/bounties/{bounty_id}/mark-preparable",
        headers={"X-API-Key": architect_key},
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == BountyStatus.READY_FOR_PREPARATION.value


def test_claim_preparation_requires_repo_membership(_set_required_security_env, client: TestClient, db_engine):
    contributor_key = API_KEY_PREFIX + ("o" * API_KEY_LENGTH)
    outsider_key = API_KEY_PREFIX + ("p" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-claim-prep-authz"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-claim-preparation",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="CLP01",
            claim_url="/api/v1/agents/claim/CLP01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        outsider = Agent(
            name="outsider-agent-claim-preparation",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(outsider_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(outsider_key),
            claim_code="CLP02",
            claim_url="/api/v1/agents/claim/CLP02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(contributor)
        session.add(outsider)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-claim-prep-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(outsider)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="ready bounty",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.READY_FOR_PREPARATION.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id
        contributor_id = str(contributor.id)
        outsider_id = str(outsider.id)

    outsider_res = client.post(
        f"/api/v1/bounties/{bounty_id}/claim-preparation",
        json={"agent_id": outsider_id, "preparation_notes": "x"},
        headers={"X-API-Key": outsider_key},
    )
    assert outsider_res.status_code == 403
    assert outsider_res.json()["detail"] == "Forbidden: Not a member of this repository"

    ok_res = client.post(
        f"/api/v1/bounties/{bounty_id}/claim-preparation",
        json={"agent_id": contributor_id, "preparation_notes": "prep"},
        headers={"X-API-Key": contributor_key},
    )
    assert ok_res.status_code == 200

    with Session(db_engine) as session:
        audits = session.exec(
            select(AuditLog).where(
                AuditLog.action == "status_transition",
                AuditLog.target == "bounty",
            )
        ).all()
        matching = []
        for entry in audits:
            detail = entry.detail or {}
            if (
                detail.get("bounty_id") == bounty_id
                and detail.get("from") == BountyStatus.READY_FOR_PREPARATION.value
                and detail.get("to") == BountyStatus.READY_FOR_PREPARATION.value
            ):
                matching.append(entry)

        assert len(matching) == 1


def test_claim_preparation_returns_409_on_second_competing_claim(_set_required_security_env, client: TestClient, db_engine):
    first_key = API_KEY_PREFIX + ("g" * API_KEY_LENGTH)
    second_key = API_KEY_PREFIX + ("h" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-claim-prep-race"

    with Session(db_engine) as session:
        first_agent = Agent(
            name="contributor-agent-claim-preparation-first",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(first_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(first_key),
            claim_code="CLR01",
            claim_url="/api/v1/agents/claim/CLR01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        second_agent = Agent(
            name="contributor-agent-claim-preparation-second",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(second_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(second_key),
            claim_code="CLR02",
            claim_url="/api/v1/agents/claim/CLR02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(first_agent)
        session.add(second_agent)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-claim-prep-race",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(first_agent)
        session.refresh(second_agent)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=first_agent.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=second_agent.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="ready bounty race",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.READY_FOR_PREPARATION.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id
        first_agent_id = str(first_agent.id)
        second_agent_id = str(second_agent.id)

    first_res = client.post(
        f"/api/v1/bounties/{bounty_id}/claim-preparation",
        json={"agent_id": first_agent_id, "preparation_notes": "first"},
        headers={"X-API-Key": first_key},
    )
    assert first_res.status_code == 200, first_res.text

    second_res = client.post(
        f"/api/v1/bounties/{bounty_id}/claim-preparation",
        json={"agent_id": second_agent_id, "preparation_notes": "second"},
        headers={"X-API-Key": second_key},
    )
    assert second_res.status_code == 409, second_res.text
    assert second_res.json()["detail"] == "Bounty already claimed for preparation"

    with Session(db_engine) as session:
        refreshed = session.get(Bounty, bounty_id)
        assert refreshed is not None
        assert refreshed.assignee == first_agent_id
        assert refreshed.status == BountyStatus.READY_FOR_PREPARATION.value


def test_cancel_and_restore_allow_repo_architect(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("u" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-cancel-restore-architect"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-cancel-restore",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="CRA01",
            claim_url="/api/v1/agents/claim/CRA01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-cancel-restore-architect",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="cancel restore by architect",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    cancel_res = client.post(
        f"/api/v1/bounties/{bounty_id}/cancel",
        json={"reason": "stop"},
        headers={"X-API-Key": architect_key},
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert bounty_id in cancel_res.json()["cancelled"]

    restore_res = client.post(
        f"/api/v1/bounties/{bounty_id}/restore",
        json={},
        headers={"X-API-Key": architect_key},
    )
    assert restore_res.status_code == 200, restore_res.text
    assert restore_res.json()["status"] == BountyStatus.OPEN.value


def test_cancel_restore_require_admin_or_repo_architect(_set_required_security_env, client: TestClient, db_engine):
    contributor_key = API_KEY_PREFIX + ("v" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-cancel-restore-authz"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-cancel-restore-authz",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="CRB01",
            claim_url="/api/v1/agents/claim/CRB01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(contributor)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-cancel-restore-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="cancel restore forbidden",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    cancel_denied = client.post(
        f"/api/v1/bounties/{bounty_id}/cancel",
        json={"reason": "no"},
        headers={"X-API-Key": contributor_key},
    )
    assert cancel_denied.status_code == 403
    assert cancel_denied.json()["detail"] == "Forbidden: admin or repo architect required"

    restore_denied = client.post(
        f"/api/v1/bounties/{bounty_id}/restore",
        json={},
        headers={"X-API-Key": contributor_key},
    )
    assert restore_denied.status_code == 403
    assert restore_denied.json()["detail"] == "Forbidden: admin or repo architect required"


def test_restore_returns_pending_when_dependencies_not_completed(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("w" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-restore-pending"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-restore-pending",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="CRC01",
            claim_url="/api/v1/agents/claim/CRC01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-restore-pending",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        dependency = Bounty(
            title="blocking dep",
            description="",
            reward=1,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(dependency)
        session.commit()
        session.refresh(dependency)

        cancelled = Bounty(
            title="cancelled child",
            description="",
            reward=1,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.CANCELLED.value,
            dependencies=[dependency.id],
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(cancelled)
        session.commit()
        session.refresh(cancelled)
        cancelled_id = cancelled.id

    restore_res = client.post(
        f"/api/v1/bounties/{cancelled_id}/restore",
        json={},
        headers={"X-API-Key": architect_key},
    )
    assert restore_res.status_code == 200, restore_res.text
    assert restore_res.json()["status"] == BountyStatus.PENDING.value


def test_cancel_and_restore_allow_admin_without_repo_membership(_set_required_security_env, client: TestClient, db_engine):
    from app_factory import require_active_identity

    repo_full_name = "owner/repo-bounty-cancel-restore-admin"

    with Session(db_engine) as session:
        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-cancel-restore-admin",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

        bounty = Bounty(
            title="cancel restore by admin",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    client.app.dependency_overrides[require_active_identity] = lambda: type("MockAdmin", (), {"id": "admin-user-1", "role": UserRole.ADMIN})()
    try:
        cancel_res = client.post(
            f"/api/v1/bounties/{bounty_id}/cancel",
            json={"reason": "admin stop"},
        )
        assert cancel_res.status_code == 200, cancel_res.text

        restore_res = client.post(
            f"/api/v1/bounties/{bounty_id}/restore",
            json={},
        )
        assert restore_res.status_code == 200, restore_res.text
        assert restore_res.json()["status"] == BountyStatus.OPEN.value
    finally:
        client.app.dependency_overrides.pop(require_active_identity, None)


def test_governance_transition_cancels_and_restores_bounty(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("x" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-governance-transition"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-governance-transition",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="GVT01",
            claim_url="/api/v1/agents/claim/GVT01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-governance-transition",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="governance transition path",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    cancel_res = client.post(
        f"/api/v1/bounties/{bounty_id}/governance-transition",
        json={"to_status": "cancelled", "reason": "governance stop"},
        headers={"X-API-Key": architect_key},
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["status"] == BountyStatus.CANCELLED.value

    restore_res = client.post(
        f"/api/v1/bounties/{bounty_id}/governance-transition",
        json={"to_status": "open"},
        headers={"X-API-Key": architect_key},
    )
    assert restore_res.status_code == 200, restore_res.text
    assert restore_res.json()["status"] == BountyStatus.OPEN.value


def test_governance_transition_marks_pending_to_ready_for_preparation(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("y" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-governance-mark-prep"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-governance-mark-prep",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="GVT02",
            claim_url="/api/v1/agents/claim/GVT02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-governance-mark-prep",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="governance mark preparable",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.PENDING.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    res = client.post(
        f"/api/v1/bounties/{bounty_id}/governance-transition",
        json={"to_status": "ready_for_preparation"},
        headers={"X-API-Key": architect_key},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == BountyStatus.READY_FOR_PREPARATION.value

    with Session(db_engine) as session:
        audits = session.exec(
            select(AuditLog).where(
                AuditLog.action == "status_transition",
                AuditLog.target == "bounty",
            )
        ).all()

        matching = []
        for entry in audits:
            detail = entry.detail or {}
            if (
                detail.get("bounty_id") == bounty_id
                and detail.get("from") == BountyStatus.PENDING.value
                and detail.get("to") == BountyStatus.READY_FOR_PREPARATION.value
            ):
                matching.append(entry)

        assert len(matching) == 1


def test_governance_transition_submitted_to_completed_unlocks_dependents(_set_required_security_env, client: TestClient, db_engine):
    architect_key = API_KEY_PREFIX + ("u" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-governance-completed-unlock"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-governance-completed-unlock",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="GVT04",
            claim_url="/api/v1/agents/claim/GVT04",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-governance-completed-unlock",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        parent = Bounty(
            title="submitted parent",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.SUBMITTED.value,
            assignee=str(architect.id),
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(parent)
        session.commit()
        session.refresh(parent)

        child = Bounty(
            title="pending child",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.PENDING.value,
            dependencies=[parent.id],
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(child)
        session.commit()
        session.refresh(child)

        parent_id = parent.id
        child_id = child.id

    res = client.post(
        f"/api/v1/bounties/{parent_id}/governance-transition",
        json={"to_status": "completed", "reason": "review approved"},
        headers={"X-API-Key": architect_key},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == BountyStatus.COMPLETED.value

    with Session(db_engine) as session:
        refreshed_parent = session.get(Bounty, parent_id)
        refreshed_child = session.get(Bounty, child_id)
        assert refreshed_parent is not None
        assert refreshed_child is not None
        assert refreshed_parent.status == BountyStatus.COMPLETED.value
        assert refreshed_child.status == BountyStatus.OPEN.value

        audits = session.exec(
            select(AuditLog).where(
                AuditLog.action == "status_transition",
                AuditLog.target == "bounty",
            )
        ).all()

        submitted_to_completed = []
        pending_to_open = []
        for entry in audits:
            detail = entry.detail or {}
            if (
                detail.get("bounty_id") == parent_id
                and detail.get("from") == BountyStatus.SUBMITTED.value
                and detail.get("to") == BountyStatus.COMPLETED.value
            ):
                submitted_to_completed.append(entry)
            if (
                detail.get("bounty_id") == child_id
                and detail.get("from") == BountyStatus.PENDING.value
                and detail.get("to") == BountyStatus.OPEN.value
            ):
                pending_to_open.append(entry)

        assert len(submitted_to_completed) == 1
        assert len(pending_to_open) == 1


def test_governance_transition_denies_non_admin_architect(_set_required_security_env, client: TestClient, db_engine):
    contributor_key = API_KEY_PREFIX + ("z" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-governance-authz"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-governance-authz",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="GVT03",
            claim_url="/api/v1/agents/claim/GVT03",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(contributor)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-governance-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )

        bounty = Bounty(
            title="governance deny",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    denied = client.post(
        f"/api/v1/bounties/{bounty_id}/governance-transition",
        json={"to_status": "cancelled", "reason": "no rights"},
        headers={"X-API-Key": contributor_key},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Forbidden: admin or repo architect required"


def test_create_bounty_allows_admin_user_without_agent_key(_set_required_security_env, client: TestClient, db_engine):
    from app_factory import require_active_identity

    repo_full_name = "owner/repo-bounty-admin-create"

    with Session(db_engine) as session:
        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-admin-create",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()

    payload = {
        "title": "Admin created bounty",
        "description": "",
        "reward": 42,
        "repo_name": repo_full_name,
        "required_role": "contributor",
        "test_command": "pytest",
        "verification_mode": "auto",
    }

    client.app.dependency_overrides[require_active_identity] = lambda: type("MockAdmin", (), {"id": "admin-user-1", "role": UserRole.ADMIN})()
    try:
        response = client.post(
            "/api/v1/bounties",
            json=payload,
        )
    finally:
        client.app.dependency_overrides.pop(require_active_identity, None)

    assert response.status_code == 200, response.text


def test_create_decomposed_requires_admin_or_repo_architect(_set_required_security_env, client: TestClient, db_engine):
    contributor_key = API_KEY_PREFIX + ("r" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-decomposed-authz"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-decomposed-authz",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="DCP01",
            claim_url="/api/v1/agents/claim/DCP01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(contributor)
        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-decomposed-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )
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
            "test_command": "pytest",
            "verification_mode": "auto",
        },
    }

    try:
        denied = client.post(
            "/api/v1/bounties/decomposed",
            json=payload,
            headers={"X-API-Key": contributor_key},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Forbidden: admin or repo architect required"
    finally:
        shutil.rmtree(repo_fs_path, ignore_errors=True)


def test_decompose_task_agent_id_guard_matches_admin_architect_policy(_set_required_security_env, client: TestClient, db_engine):
    from app_factory import require_active_identity

    contributor_key = API_KEY_PREFIX + ("k" * API_KEY_LENGTH)
    architect_key = API_KEY_PREFIX + ("l" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-decompose-task-agent-id"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-decompose-task",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="DCP02",
            claim_url="/api/v1/agents/claim/DCP02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        architect = Agent(
            name="architect-agent-decompose-task",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="DCP03",
            claim_url="/api/v1/agents/claim/DCP03",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(contributor)
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-decompose-task-agent-id",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        parent = Bounty(
            title="parent for decompose",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.OPEN.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(parent)
        session.commit()
        session.refresh(parent)

        parent_id = parent.id
        contributor_id = str(contributor.id)
        architect_id = str(architect.id)

    sub_tasks_payload = [
        {
            "title": "child task",
            "description": "",
            "reward": 1,
            "required_role": "contributor",
            "estimated_hours": 1,
            "track": "backend",
            "test_command": "pytest",
            "verification_mode": "auto",
        }
    ]

    denied = client.post(
        f"/api/v1/bounties/{parent_id}/decompose",
        params={"agent_id": contributor_id},
        json=sub_tasks_payload,
        headers={"X-API-Key": contributor_key},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Forbidden: admin or repo architect required"

    architect_mismatch = client.post(
        f"/api/v1/bounties/{parent_id}/decompose",
        params={"agent_id": "mismatched-agent-id"},
        json=sub_tasks_payload,
        headers={"X-API-Key": architect_key},
    )
    assert architect_mismatch.status_code == 403
    assert architect_mismatch.json()["detail"] == "Agent ID mismatch"

    allowed = client.post(
        f"/api/v1/bounties/{parent_id}/decompose",
        params={"agent_id": architect_id},
        json=sub_tasks_payload,
        headers={"X-API-Key": architect_key},
    )
    assert allowed.status_code == 200, allowed.text

    client.app.dependency_overrides[require_active_identity] = lambda: type("MockAdmin", (), {"id": "admin-user-1", "role": UserRole.ADMIN})()
    try:
        admin_allowed = client.post(
            f"/api/v1/bounties/{parent_id}/decompose",
            params={"agent_id": "any-agent-id"},
            json=sub_tasks_payload,
        )
    finally:
        client.app.dependency_overrides.pop(require_active_identity, None)

    assert admin_allowed.status_code == 200, admin_allowed.text


def test_activate_from_preparation_allows_internal_token_without_identity(_set_required_security_env, client: TestClient, db_engine):
    from dependencies.auth import require_active_identity_optional

    repo_full_name = "owner/repo-bounty-activate-internal-token"

    with Session(db_engine) as session:
        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-activate-internal-token",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

        dep = Bounty(
            title="completed dep",
            description="",
            reward=1,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.COMPLETED.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(dep)
        session.commit()
        session.refresh(dep)

        bounty = Bounty(
            title="ready bounty",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.READY_FOR_PREPARATION.value,
            dependencies=[dep.id],
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
        bounty_id = bounty.id

    client.app.dependency_overrides[require_active_identity_optional] = lambda: None
    try:
        res = client.post(
            f"/api/v1/bounties/{bounty_id}/activate-from-preparation",
            headers={"X-Internal-Token": "test-internal-token"},
        )
    finally:
        client.app.dependency_overrides.pop(require_active_identity_optional, None)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == BountyStatus.OPEN.value


def test_activate_from_preparation_requires_architect_or_admin_without_internal_token(_set_required_security_env, client: TestClient, db_engine):
    contributor_key = API_KEY_PREFIX + ("i" * API_KEY_LENGTH)
    architect_key = API_KEY_PREFIX + ("j" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-activate-authz"

    with Session(db_engine) as session:
        contributor = Agent(
            name="contributor-agent-activate-authz",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(contributor_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(contributor_key),
            claim_code="ACT01",
            claim_url="/api/v1/agents/claim/ACT01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        architect = Agent(
            name="architect-agent-activate-authz",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="ACT02",
            claim_url="/api/v1/agents/claim/ACT02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(contributor)
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-activate-authz",
            is_private=False,
            is_active=True,
        )
        session.add(repo)
        session.commit()
        session.refresh(contributor)
        session.refresh(architect)
        session.refresh(repo)

        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=contributor.id,
                role=RepoRole.CONTRIBUTOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.add(
            RepoMember(
                repo_id=repo.id,
                agent_id=architect.id,
                role=RepoRole.ARCHITECT,
                status=MembershipStatus.ACTIVE,
            )
        )

        dep = Bounty(
            title="completed dep",
            description="",
            reward=1,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.COMPLETED.value,
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(dep)
        session.commit()
        session.refresh(dep)

        denied_bounty = Bounty(
            title="ready denied",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.READY_FOR_PREPARATION.value,
            dependencies=[dep.id],
            test_command="pytest",
            verification_mode="auto",
        )
        allowed_bounty = Bounty(
            title="ready allowed",
            description="",
            reward=10,
            repo_name=repo_full_name,
            repo_id=str(repo.id),
            required_role=RepoRole.CONTRIBUTOR.value,
            status=BountyStatus.READY_FOR_PREPARATION.value,
            dependencies=[dep.id],
            test_command="pytest",
            verification_mode="auto",
        )
        session.add(denied_bounty)
        session.add(allowed_bounty)
        session.commit()
        session.refresh(denied_bounty)
        session.refresh(allowed_bounty)
        denied_bounty_id = denied_bounty.id
        allowed_bounty_id = allowed_bounty.id

    denied = client.post(
        f"/api/v1/bounties/{denied_bounty_id}/activate-from-preparation",
        headers={"X-API-Key": contributor_key},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Forbidden: admin or repo architect required"

    allowed = client.post(
        f"/api/v1/bounties/{allowed_bounty_id}/activate-from-preparation",
        headers={"X-API-Key": architect_key},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == BountyStatus.OPEN.value


def test_bounty_analyze_rejects_invalid_json_and_returns_retry_prompt(_set_required_security_env, client: TestClient, db_engine):
    analyzer_key = API_KEY_PREFIX + ("s" * API_KEY_LENGTH)

    with Session(db_engine) as session:
        analyzer = Agent(
            name="analyzer-agent-invalid-json",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(analyzer_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(analyzer_key),
            claim_code="ANL01",
            claim_url="/api/v1/agents/claim/ANL01",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
            reputation_score=100,
            validation_violations=0,
        )
        session.add(analyzer)
        session.commit()
        session.refresh(analyzer)
        analyzer_id = analyzer.id

    res = client.post(
        "/api/v1/bounties/dummy-bounty/analyze",
        json={"options_json": "not-json"},
        headers={"X-API-Key": analyzer_key},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert body["is_valid"] is False
    assert body["retry_prompt"]
    assert "Invalid JSON format" in (body["error_message"] or "")

    with Session(db_engine) as session:
        updated = session.get(Agent, analyzer_id)
        assert updated is not None
        assert updated.validation_violations == 1
        assert updated.reputation_score == 90


def test_bounty_analyze_accepts_valid_options_and_recovers_reputation(_set_required_security_env, client: TestClient, db_engine):
    analyzer_key = API_KEY_PREFIX + ("t" * API_KEY_LENGTH)

    with Session(db_engine) as session:
        analyzer = Agent(
            name="analyzer-agent-valid-json",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(analyzer_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(analyzer_key),
            claim_code="ANL02",
            claim_url="/api/v1/agents/claim/ANL02",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
            reputation_score=95,
            validation_violations=2,
        )
        session.add(analyzer)
        session.commit()
        session.refresh(analyzer)
        analyzer_id = analyzer.id

    valid_options = (
        '[{"option":"方案A","reason":"成本最低"},'
        '{"option":"方案B","reason":"风险最低"},'
        '{"option":"方案C","reason":"上线最快"}]'
    )

    res = client.post(
        "/api/v1/bounties/dummy-bounty/analyze",
        json={"options_json": valid_options},
        headers={"X-API-Key": analyzer_key},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["is_valid"] is True
    assert isinstance(body["parsed_options"], list)
    assert len(body["parsed_options"]) == 3
    assert body["reputation_score"] == 100

    with Session(db_engine) as session:
        updated = session.get(Agent, analyzer_id)
        assert updated is not None
        assert updated.validation_violations == 0
        assert updated.reputation_score == 100
