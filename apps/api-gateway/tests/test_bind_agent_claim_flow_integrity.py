from datetime import datetime, timedelta
import secrets

import bcrypt
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from main import app
from agent_auth.models import Agent, AgentStatus
from agent_auth.models.platform import User, UserAgentBinding
from agent_auth.services.user_auth import UserAuthService, get_current_user
from agent_auth.utils import API_KEY_PREFIX, API_KEY_LENGTH, get_api_key_prefix


def _new_agent_api_key() -> str:
    random_part = "".join(
        secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for _ in range(API_KEY_LENGTH)
    )
    return f"{API_KEY_PREFIX}{random_part}"


def _create_pending_agent(db_engine):
    raw_api_key = _new_agent_api_key()
    with Session(db_engine) as session:
        claim_code = f"CB{secrets.token_hex(3).upper()[:6]}"
        agent = Agent(
            name="pending-bind-target",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code=claim_code,
            claim_url="/api/v1/agents/claim/pending",
            claim_expires_at=datetime.utcnow() + timedelta(hours=24),
            status=AgentStatus.PENDING,
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return agent.id, raw_api_key, claim_code


def _create_claimed_agent(db_engine, owner_email: str):
    raw_api_key = _new_agent_api_key()
    with Session(db_engine) as session:
        claim_code = f"CC{secrets.token_hex(3).upper()[:6]}"
        agent = Agent(
            name="claimed-bind-target",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code=claim_code,
            claim_url="/api/v1/agents/claim/claimed",
            claim_expires_at=datetime.utcnow() + timedelta(hours=24),
            status=AgentStatus.CLAIMED,
            owner_email=owner_email.lower(),
            claimed_at=datetime.utcnow(),
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return agent.id, raw_api_key, claim_code


def _override_current_user(user: User):
    async def _dep():
        return user

    app.dependency_overrides[get_current_user] = _dep


def test_bind_agent_rejects_unclaimed_agent(client, db_engine):
    agent_id, raw_api_key, claim_code = _create_pending_agent(db_engine)

    user_email = f"pending-bind-{secrets.token_hex(3)}@example.com".lower()
    with Session(db_engine) as session:
        user = User(
            email=user_email,
            email_verified=False,
            display_name="pending-bind-user",
            password_hash=None,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    _override_current_user(user)

    res = client.post(
        f"/api/v1/auth/bind-agent?agent_id={agent_id}&claim_code={claim_code}",
        headers={
            "X-API-Key": raw_api_key,
        },
    )

    assert res.status_code == 403
    assert "must complete claim verification" in res.json()["detail"].lower()

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.PENDING

        binding = session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent_id)
        ).first()
        assert binding is None


def test_bind_agent_rejects_user_that_does_not_match_claimed_owner(client, db_engine):
    owner_email = f"owner-{secrets.token_hex(3)}@example.com".lower()
    agent_id, raw_api_key, claim_code = _create_claimed_agent(db_engine, owner_email=owner_email)

    attacker_email = f"attacker-{secrets.token_hex(3)}@example.com".lower()
    with Session(db_engine) as session:
        attacker = User(
            email=attacker_email,
            email_verified=False,
            display_name="attacker-user",
            password_hash=None,
        )
        session.add(attacker)
        session.commit()
        session.refresh(attacker)

    _override_current_user(attacker)

    res = client.post(
        f"/api/v1/auth/bind-agent?agent_id={agent_id}&claim_code={claim_code}",
        headers={"X-API-Key": raw_api_key},
    )

    assert res.status_code == 403
    assert "does not match claimed owner" in res.json()["detail"].lower()

    with Session(db_engine) as session:
        binding = session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent_id)
        ).first()
        assert binding is None


def test_bind_agent_succeeds_for_claimed_owner(client, db_engine):
    owner_email = f"owner-ok-{secrets.token_hex(3)}@example.com".lower()
    agent_id, raw_api_key, claim_code = _create_claimed_agent(db_engine, owner_email=owner_email)

    with Session(db_engine) as session:
        owner = User(
            email=owner_email,
            email_verified=False,
            display_name="owner-user",
            password_hash=None,
        )
        session.add(owner)
        session.commit()
        session.refresh(owner)
        owner_id = owner.id

    _override_current_user(owner)

    res = client.post(
        f"/api/v1/auth/bind-agent?agent_id={agent_id}&claim_code={claim_code}",
        headers={"X-API-Key": raw_api_key},
    )

    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["success"] is True
    assert payload["agent_id"] == str(agent_id)
    assert "bound_at" in payload

    with Session(db_engine) as session:
        binding = session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent_id)
        ).first()
        assert binding is not None
        assert binding.user_id == owner_id
        assert binding.agent_id == agent_id


def test_bind_agent_rejects_claim_code_mismatch(client, db_engine):
    owner_email = f"owner-code-{secrets.token_hex(3)}@example.com".lower()
    agent_id, raw_api_key, _claim_code = _create_claimed_agent(db_engine, owner_email=owner_email)

    with Session(db_engine) as session:
        owner = User(
            email=owner_email,
            email_verified=False,
            display_name="owner-code-user",
            password_hash=None,
        )
        session.add(owner)
        session.commit()
        session.refresh(owner)

    _override_current_user(owner)

    res = client.post(
        f"/api/v1/auth/bind-agent?agent_id={agent_id}&claim_code=WRONG123",
        headers={"X-API-Key": raw_api_key},
    )

    assert res.status_code == 400
    assert "claim code mismatch" in res.json()["detail"].lower()

    with Session(db_engine) as session:
        binding = session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent_id)
        ).first()
        assert binding is None


def test_bind_agent_sets_claimed_owner_fields_atomically(db_engine):
    owner_email = f"owner-fields-{secrets.token_hex(3)}@example.com".lower()
    agent_id, _raw_api_key, _claim_code = _create_claimed_agent(db_engine, owner_email=owner_email)

    github_id = f"gh-{secrets.token_hex(4)}"
    github_login = f"owner_{secrets.token_hex(3)}"

    with Session(db_engine) as session:
        owner = User(
            email=owner_email,
            email_verified=True,
            display_name="owner-fields-user",
            github_id=github_id,
            github_login=github_login,
            password_hash=None,
        )
        session.add(owner)
        session.commit()
        session.refresh(owner)

        agent = session.get(Agent, agent_id)
        assert agent is not None
        agent.owner_github_id = None
        agent.owner_github_login = None
        session.add(agent)
        session.commit()

        service = UserAuthService(session)
        binding = service.bind_agent_to_user(owner, agent)
        assert binding is not None

        persisted = session.get(Agent, agent_id)
        assert persisted is not None
        assert persisted.owner_email == owner_email
        assert persisted.owner_github_id == github_id
        assert persisted.owner_github_login == github_login
        assert persisted.status == AgentStatus.CLAIMED
        assert persisted.claimed_at is not None


def test_bind_agent_concurrency_integrity_error_is_normalized(db_engine, monkeypatch):
    owner_email = f"owner-race-{secrets.token_hex(3)}@example.com".lower()
    agent_id, _raw_api_key, _claim_code = _create_claimed_agent(db_engine, owner_email=owner_email)

    with Session(db_engine) as session:
        owner = User(
            email=owner_email,
            email_verified=True,
            display_name="owner-race-user",
            password_hash=None,
        )
        session.add(owner)
        session.commit()
        session.refresh(owner)

        other = User(
            email=f"other-{secrets.token_hex(3)}@example.com".lower(),
            email_verified=True,
            display_name="other-user",
            password_hash=None,
        )
        session.add(other)
        session.commit()
        session.refresh(other)

        session.add(UserAgentBinding(user_id=other.id, agent_id=agent_id, is_permanent=True))
        session.commit()

        service = UserAuthService(session)
        agent = session.get(Agent, agent_id)
        assert agent is not None

        original_commit = session.commit

        def _race_commit():
            # Simulate race: pre-check passed, but DB reports unique conflict on commit.
            session.rollback()
            raise IntegrityError("insert", {}, Exception("duplicate key"))

        monkeypatch.setattr(session, "commit", _race_commit)
        try:
            with pytest.raises(ValueError, match="Agent is already bound to another user"):
                service.bind_agent_to_user(owner, agent)
        finally:
            monkeypatch.setattr(session, "commit", original_commit)
