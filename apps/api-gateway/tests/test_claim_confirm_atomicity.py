from datetime import datetime, timedelta
import secrets

from sqlmodel import Session, select

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
