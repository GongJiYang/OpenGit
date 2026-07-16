import os
import sys
from datetime import datetime, timedelta

import bcrypt
from fastapi.testclient import TestClient
from sqlmodel import Session, select


# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from main import app  # noqa: E402
from core.settings import clear_settings_cache  # noqa: E402
from agent_auth.models.platform import Repo, RepoMember, MembershipStatus, RepoRole  # noqa: E402
from agent_auth.models import Agent, AgentStatus  # noqa: E402
from agent_auth.utils import get_api_key_prefix, API_KEY_PREFIX, API_KEY_LENGTH  # noqa: E402
from persistence import Bounty, BountyStatus, AuditLog  # noqa: E402


def _create_required_env(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret")
    monkeypatch.setenv("WECHAT_TOKEN", "test-wechat-token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    clear_settings_cache()


def test_mark_preparable_uses_fsm_and_writes_status_transition_audit(monkeypatch, db_engine):
    _create_required_env(monkeypatch)

    architect_key = API_KEY_PREFIX + ("r" * API_KEY_LENGTH)
    repo_full_name = "owner/repo-bounty-mark-preparable-fsm"

    with Session(db_engine) as session:
        architect = Agent(
            name="architect-agent-mark-preparable-fsm",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(architect_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(architect_key),
            claim_code="MRKFSM",
            claim_url="/api/v1/agents/claim/MRKFSM",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(architect)

        repo = Repo(
            full_name=repo_full_name,
            owner="owner",
            name="repo-bounty-mark-preparable-fsm",
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
            title="mark preparable via fsm",
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

        pre_audits = session.exec(
            select(AuditLog).where(
                AuditLog.action == "status_transition",
                AuditLog.target == "bounty",
            )
        ).all()
        assert pre_audits == []

    with TestClient(app) as client:
        res = client.post(
            f"/api/v1/bounties/{bounty_id}/mark-preparable",
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
