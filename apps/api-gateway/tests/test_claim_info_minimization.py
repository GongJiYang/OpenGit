from datetime import datetime, timedelta
import secrets

from sqlmodel import Session

from agent_auth.models import Agent, AgentStatus



def _create_pending_agent(db_engine):
    claim_code = f"CI{secrets.token_hex(3).upper()[:6]}"

    with Session(db_engine) as session:
        agent = Agent(
            name="claim-info-agent",
            model_name="claim-info-model",
            api_key_hash="not-used",
            api_key_prefix=f"ci{secrets.token_hex(5)}",
            claim_code=claim_code,
            claim_url=f"/api/v1/agents/claim/{claim_code}",
            claim_expires_at=datetime.utcnow() + timedelta(hours=1),
            status=AgentStatus.PENDING,
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return claim_code



def test_claim_info_endpoint_hides_enumerable_identity_fields(client, db_engine):
    claim_code = _create_pending_agent(db_engine)

    res = client.get(f"/api/v1/agents/claim/{claim_code}/info")

    assert res.status_code == 200, res.text
    body = res.json()
    assert "status" in body
    assert "expires_at" in body

    # Identity / enumerable fields should not be exposed publicly.
    assert "agent_name" not in body
    assert "claim_code" not in body
    assert "model_name" not in body



def test_claim_page_hides_agent_name_model_and_claim_code(client, db_engine):
    claim_code = _create_pending_agent(db_engine)

    res = client.get(f"/api/v1/agents/claim/{claim_code}")

    assert res.status_code == 200
    html = res.text

    # Keep flow usable but remove direct identity/code disclosure from page.
    assert "claim-info-agent" not in html
    assert "claim-info-model" not in html
    assert claim_code not in html
