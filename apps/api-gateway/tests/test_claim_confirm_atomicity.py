from datetime import datetime, timedelta
import secrets

import pytest
from sqlmodel import Session, select

import agent_auth.routers.claim as claim_router_module
from main import app
from agent_auth.models import Agent, AgentStatus, EmailVerification


def _create_pending_agent_with_verification(db_engine, email: str):
    claim_code = f"CF{secrets.token_hex(3).upper()[:6]}"
    token = secrets.token_hex(16)

    with Session(db_engine) as session:
        agent = Agent(
            name="claim-confirm-agent",
            model_name="test-model",
            api_key_hash="not-used",
            api_key_prefix=f"cf{secrets.token_hex(5)}",
            claim_code=claim_code,
            claim_url=f"/api/v1/agents/claim/{claim_code}",
            claim_expires_at=datetime.utcnow() + timedelta(hours=1),
            status=AgentStatus.VERIFYING,
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)

        verification = EmailVerification(
            agent_id=agent.id,
            email=email,
            token=token,
            token_expires_at=datetime.utcnow() + timedelta(minutes=30),
            verified=False,
        )
        session.add(verification)
        session.commit()

        return agent.id, claim_code, token, email


def test_claim_confirm_is_idempotent_under_repeated_calls(client, db_engine):
    email = f"confirm-{secrets.token_hex(3)}@example.com".lower()
    agent_id, claim_code, token, _email = _create_pending_agent_with_verification(db_engine, email)

    first = client.get(f"/api/v1/agents/claim/{claim_code}/confirm?token={token}")
    assert first.status_code == 200
    assert "Claim Successful" in first.text

    second = client.get(f"/api/v1/agents/claim/{claim_code}/confirm?token={token}")
    assert second.status_code == 200
    assert "Link Already Used" in second.text

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.CLAIMED
        assert agent.owner_email == email

        verifications = session.exec(
            select(EmailVerification).where(EmailVerification.agent_id == agent_id)
        ).all()
        assert len(verifications) == 1
        verification = verifications[0]
        assert verification.verified is True
        assert verification.verified_at is not None


def test_claim_confirm_rejects_when_claim_code_mismatch_even_with_valid_token(client, db_engine):
    email = f"mismatch-{secrets.token_hex(3)}@example.com".lower()
    agent_id, _claim_code, token, _email = _create_pending_agent_with_verification(db_engine, email)

    wrong_claim_code = f"WRONG{secrets.token_hex(2).upper()}"
    res = client.get(f"/api/v1/agents/claim/{wrong_claim_code}/confirm?token={token}")

    assert res.status_code == 200
    assert "Invalid Claim" in res.text

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.VERIFYING
        assert agent.owner_email is None

        verification = session.exec(
            select(EmailVerification).where(EmailVerification.agent_id == agent_id)
        ).first()
        assert verification is not None
        assert verification.verified is False


def test_claim_verify_email_is_single_transaction_without_intermediate_commit(client, db_engine):
    claim_code = f"VT{secrets.token_hex(3).upper()[:6]}"
    token = secrets.token_hex(16)

    with Session(db_engine) as session:
        agent = Agent(
            name="verify-atomic-agent",
            model_name="test-model",
            api_key_hash="not-used",
            api_key_prefix=f"va{secrets.token_hex(5)}",
            claim_code=claim_code,
            claim_url=f"/api/v1/agents/claim/{claim_code}",
            claim_expires_at=datetime.utcnow() + timedelta(hours=1),
            status=AgentStatus.PENDING,
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)

        stale = EmailVerification(
            agent_id=agent.id,
            email="stale@example.com",
            token=token,
            token_expires_at=datetime.utcnow() + timedelta(minutes=30),
            verified=False,
        )
        session.add(stale)
        session.commit()

    class _FailingEmailSession:
        def __init__(self, real):
            self._real = real
            self._commit_count = 0

        def exec(self, *args, **kwargs):
            return self._real.exec(*args, **kwargs)

        def add(self, obj):
            return self._real.add(obj)

        def rollback(self):
            return self._real.rollback()

        def close(self):
            return self._real.close()

        def __getattr__(self, item):
            return getattr(self._real, item)

        def commit(self):
            self._commit_count += 1
            if self._commit_count == 1:
                raise RuntimeError("forced-commit-failure")
            return self._real.commit()

    def _override_get_session():
        real_session = Session(db_engine)
        wrapped = _FailingEmailSession(real_session)
        try:
            yield wrapped
        finally:
            wrapped.close()

    app.dependency_overrides[claim_router_module.get_session] = _override_get_session

    try:
        with pytest.raises(RuntimeError, match="forced-commit-failure"):
            client.post(
                f"/api/v1/agents/claim/{claim_code}/verify",
                json={"email": "owner@example.com"},
            )
    finally:
        app.dependency_overrides.pop(claim_router_module.get_session, None)

    with Session(db_engine) as session:
        agent_stmt = select(Agent).where(Agent.claim_code == claim_code)
        agent = session.exec(agent_stmt).first()
        assert agent is not None
        assert agent.status == AgentStatus.PENDING

        records = session.exec(
            select(EmailVerification).where(EmailVerification.agent_id == agent.id)
        ).all()
        assert len(records) == 1
        assert records[0].email == "stale@example.com"
        assert records[0].verified is False
