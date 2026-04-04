import json

from sqlmodel import Session, select

from core.settings import clear_settings_cache
from agent_auth.models import Agent, AgentRegisterRequest



def test_register_rejects_invalid_role(client):
    res = client.post(
        "/api/v1/agents/register",
        json={
            "name": "bad-role-agent",
            "model_name": "test-model",
            "role": "godmode",
        },
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "Invalid role" in detail



def test_register_normalizes_valid_role_before_persist(client, db_engine):
    res = client.post(
        "/api/v1/agents/register",
        json={
            "name": "ok-role-agent",
            "model_name": "test-model",
            "role": "  ReVieWer  ",
        },
    )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["role"] == "reviewer"

    with Session(db_engine) as session:
        stmt = select(Agent).where(Agent.name == "ok-role-agent")
        agent = session.exec(stmt).first()
        assert agent is not None
        assert agent.role == "reviewer"



def test_register_accepts_tester_role_and_loads_prompt(client, db_engine):
    res = client.post(
        "/api/v1/agents/register",
        json={
            "name": "tester-role-agent",
            "model_name": "test-model",
            "role": " tester ",
        },
    )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["role"] == "tester"
    assert isinstance(data["role_prompt"], str)
    assert len(data["role_prompt"]) > 0

    with Session(db_engine) as session:
        stmt = select(Agent).where(Agent.name == "tester-role-agent")
        agent = session.exec(stmt).first()
        assert agent is not None
        assert agent.role == "tester"


def test_register_rate_limit_blocks_burst(client, monkeypatch):
    monkeypatch.setenv("AGENT_REGISTER_RATE_LIMIT", "2/minute")
    clear_settings_cache()

    try:
        def make_payload(i: int):
            return {
                "name": f"burst-agent-{i}",
                "model_name": "test-model",
                "role": "contributor",
            }

        first = client.post("/api/v1/agents/register", json=make_payload(1))
        second = client.post("/api/v1/agents/register", json=make_payload(2))
        third = client.post("/api/v1/agents/register", json=make_payload(3))

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert third.status_code == 429, third.text
    finally:
        clear_settings_cache()


def test_register_name_rate_limit_blocks_repeated_same_name(client, monkeypatch):
    monkeypatch.setenv("AGENT_REGISTER_RATE_LIMIT", "20/minute")
    monkeypatch.setenv("AGENT_REGISTER_NAME_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AGENT_REGISTER_NAME_WINDOW_SECONDS", "300")
    clear_settings_cache()

    try:
        payload = {
            "name": "same-name-agent",
            "model_name": "test-model",
            "role": "contributor",
        }

        first = client.post("/api/v1/agents/register", json=payload)
        second = client.post("/api/v1/agents/register", json=payload)
        third = client.post("/api/v1/agents/register", json=payload)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert third.status_code == 429, third.text
        assert "agent name" in third.json().get("detail", "").lower()
    finally:
        clear_settings_cache()


def test_register_name_rate_limit_does_not_block_different_names(client, monkeypatch):
    monkeypatch.setenv("AGENT_REGISTER_RATE_LIMIT", "20/minute")
    monkeypatch.setenv("AGENT_REGISTER_NAME_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AGENT_REGISTER_NAME_WINDOW_SECONDS", "300")
    clear_settings_cache()

    try:
        payload_a = {
            "name": "name-a",
            "model_name": "test-model",
            "role": "contributor",
        }
        payload_b = {
            "name": "name-b",
            "model_name": "test-model",
            "role": "contributor",
        }

        first = client.post("/api/v1/agents/register", json=payload_a)
        second = client.post("/api/v1/agents/register", json=payload_a)
        third = client.post("/api/v1/agents/register", json=payload_b)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert third.status_code == 201, third.text
    finally:
        clear_settings_cache()



def test_register_request_uses_profile_field_with_metadata_alias():
    assert "profile" in AgentRegisterRequest.model_fields
    assert "metadata" not in AgentRegisterRequest.model_fields
    assert AgentRegisterRequest.model_fields["profile"].alias == "metadata"



def test_register_accepts_metadata_alias_and_persists_metadata_json(client, db_engine):
    metadata = {
        "team": "security",
        "priority": 1,
        "capabilities": ["review", "fix"],
    }

    res = client.post(
        "/api/v1/agents/register",
        json={
            "name": "metadata-alias-agent",
            "model_name": "test-model",
            "role": "contributor",
            "metadata": metadata,
        },
    )

    assert res.status_code == 201, res.text

    with Session(db_engine) as session:
        stmt = select(Agent).where(Agent.name == "metadata-alias-agent")
        agent = session.exec(stmt).first()
        assert agent is not None
        assert agent.metadata_json is not None
        assert json.loads(agent.metadata_json) == metadata
