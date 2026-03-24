from datetime import datetime, timedelta
import secrets

import bcrypt
from sqlmodel import Session

from agent_auth.models import Agent, AgentStatus
from agent_auth.utils import API_KEY_PREFIX, API_KEY_LENGTH, get_api_key_prefix



def _create_agent_with_status(db_engine, status: AgentStatus):
    status_tag = status.value.lower()
    random_tag = secrets.token_hex(3)
    random_part = (status_tag + random_tag + ("z" * API_KEY_LENGTH))[:API_KEY_LENGTH]
    raw_api_key = API_KEY_PREFIX + random_part
    with Session(db_engine) as session:
        agent = Agent(
            name=f"agent-{status_tag}-{random_tag}",
            model_name="test-model",
            api_key_hash=bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code=f"TC{random_tag.upper()}",
            claim_url=f"/api/v1/agents/claim/{random_tag}",
            claim_expires_at=datetime.utcnow() + timedelta(hours=24),
            status=status,
            role="contributor",
        )
        session.add(agent)
        session.commit()
    return raw_api_key



def test_pending_agent_cannot_access_status_endpoint(client, db_engine):
    api_key = _create_agent_with_status(db_engine, AgentStatus.PENDING)

    res = client.get("/api/v1/agents/status", headers={"X-API-Key": api_key})

    assert res.status_code == 403
    assert "not claimed" in res.json()["detail"].lower()



def test_verifying_agent_cannot_access_heartbeat_endpoint(client, db_engine):
    api_key = _create_agent_with_status(db_engine, AgentStatus.VERIFYING)

    res = client.post(
        "/api/v1/agents/heartbeat",
        headers={"X-API-Key": api_key},
        json={"status_message": "alive"},
    )

    assert res.status_code == 403
    assert "not claimed" in res.json()["detail"].lower()



def test_pending_agent_can_regenerate_claim_url(client, db_engine):
    api_key = _create_agent_with_status(db_engine, AgentStatus.PENDING)

    res = client.post("/api/v1/agents/regenerate-claim", headers={"X-API-Key": api_key})

    assert res.status_code == 200, res.text
    body = res.json()
    assert "claim_code" in body
    assert "claim_url" in body
    assert "claim_expires_at" in body
