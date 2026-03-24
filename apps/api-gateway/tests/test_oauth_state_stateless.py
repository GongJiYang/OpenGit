from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import secrets

from sqlmodel import Session

import agent_auth.routers.oauth as oauth_module
from agent_auth.models import Agent, AgentStatus


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        return _FakeResponse({"access_token": "fake-access-token"})

    async def get(self, url, headers=None):
        if url.endswith("/user"):
            return _FakeResponse({"id": 123456, "login": "octocat", "email": "owner@example.com"})
        if url.endswith("/user/emails"):
            return _FakeResponse([
                {"email": "owner@example.com", "primary": True, "verified": True}
            ])
        return _FakeResponse({})


def _create_pending_agent(db_engine):
    claim_code = f"OC{secrets.token_hex(3).upper()[:6]}"
    with Session(db_engine) as session:
        agent = Agent(
            name="oauth-agent",
            model_name="test-model",
            api_key_hash="unused",
            api_key_prefix=f"oa{secrets.token_hex(5)}",
            claim_code=claim_code,
            claim_url=f"/api/v1/agents/claim/{claim_code}",
            claim_expires_at=datetime.utcnow() + timedelta(minutes=20),
            status=AgentStatus.PENDING,
            role="contributor",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return agent.id, claim_code


def test_oauth_state_is_stateless_and_callback_succeeds(client, db_engine, monkeypatch):
    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", _FakeAsyncClient)
    agent_id, claim_code = _create_pending_agent(db_engine)

    start = client.get(f"/api/v1/oauth/github?claim_code={claim_code}", follow_redirects=False)
    assert start.status_code in (302, 307)

    location = start.headers["location"]
    qs = parse_qs(urlparse(location).query)
    state = qs["state"][0]

    decoded = oauth_module._decode_oauth_state(state)
    assert decoded["aid"] == str(agent_id)
    assert decoded["cc"] == claim_code

    callback = client.get(
        "/api/v1/oauth/github/callback",
        params={"code": "mock-code", "state": state},
    )
    assert callback.status_code == 200, callback.text
    body = callback.json()
    assert body["success"] is True
    assert body["agent_id"] == str(agent_id)
    assert body["owner_email"] == "owner@example.com"

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.CLAIMED
        assert agent.owner_email == "owner@example.com"
        assert agent.owner_github_login == "octocat"
        assert agent.owner_github_id == "123456"


def test_oauth_callback_rejects_tampered_state(client, db_engine):
    agent_id, claim_code = _create_pending_agent(db_engine)
    valid_state = oauth_module._encode_oauth_state(str(agent_id), claim_code)

    tampered = valid_state[:-1] + ("a" if valid_state[-1] != "a" else "b")
    res = client.get(
        "/api/v1/oauth/github/callback",
        params={"code": "mock-code", "state": tampered},
    )

    assert res.status_code == 400
    assert "Invalid or expired OAuth state." in res.text


def test_oauth_callback_rejects_state_when_claim_code_changed(client, db_engine):
    agent_id, claim_code = _create_pending_agent(db_engine)
    state = oauth_module._encode_oauth_state(str(agent_id), claim_code)

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        agent.claim_code = "CHANGED1"
        session.add(agent)
        session.commit()

    res = client.get(
        "/api/v1/oauth/github/callback",
        params={"code": "mock-code", "state": state},
    )

    assert res.status_code == 400
    assert "Invalid OAuth state." in res.text


def test_oauth_callback_is_atomic_when_claim_happens_during_oauth_exchange(client, db_engine, monkeypatch):
    agent_id, claim_code = _create_pending_agent(db_engine)
    state = oauth_module._encode_oauth_state(str(agent_id), claim_code)

    class _RacingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            with Session(db_engine) as session:
                agent = session.get(Agent, agent_id)
                assert agent is not None
                agent.status = AgentStatus.CLAIMED
                agent.owner_email = "racer@example.com"
                agent.owner_github_id = "999"
                agent.owner_github_login = "racer"
                session.add(agent)
                session.commit()
            return _FakeResponse({"access_token": "fake-access-token"})

        async def get(self, url, headers=None):
            if url.endswith("/user"):
                return _FakeResponse({"id": 123456, "login": "octocat", "email": "owner@example.com"})
            if url.endswith("/user/emails"):
                return _FakeResponse([
                    {"email": "owner@example.com", "primary": True, "verified": True}
                ])
            return _FakeResponse({})

    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", _RacingAsyncClient)

    res = client.get(
        "/api/v1/oauth/github/callback",
        params={"code": "mock-code", "state": state},
    )

    assert res.status_code == 400
    assert "Agent is already claimed." in res.text

    with Session(db_engine) as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.CLAIMED
        assert agent.owner_email == "racer@example.com"
        assert agent.owner_github_login == "racer"
        assert agent.owner_github_id == "999"
