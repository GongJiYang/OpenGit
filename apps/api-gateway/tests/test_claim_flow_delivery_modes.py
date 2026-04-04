from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
import secrets

from sqlmodel import Session

import agent_auth.routers.claim as claim_module
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


def _create_pending_agent(db_engine, name: str = "claim-flow-agent"):
    claim_code = f"CL{secrets.token_hex(3).upper()[:6]}"
    with Session(db_engine) as session:
        agent = Agent(
            name=name,
            model_name="test-model",
            api_key_hash="unused",
            api_key_prefix=f"cf{secrets.token_hex(5)}",
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


def test_claim_verify_returns_dev_console_link(client, db_engine, monkeypatch):
    _agent_id, claim_code = _create_pending_agent(db_engine, name="claim-dev-console")

    class _Delivery:
        delivery_mode = "dev_console"
        verify_url = "http://localhost:8000/api/v1/agents/claim/DEV123/confirm?token=abc"

    async def _fake_send_verification_email(to_email, agent_name, verify_url):
        return _Delivery()

    monkeypatch.setattr(claim_module, "send_verification_email", _fake_send_verification_email)

    res = client.post(
        f"/api/v1/agents/claim/{claim_code}/verify",
        json={"email": "owner@example.com"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body["delivery_mode"] == "dev_console"
    assert body["verify_url"] == "http://localhost:8000/api/v1/agents/claim/DEV123/confirm?token=abc"
    assert "bind" in body["next_step"].lower()


def test_claim_verify_returns_failure_when_delivery_fails(client, db_engine, monkeypatch):
    _agent_id, claim_code = _create_pending_agent(db_engine, name="claim-delivery-failure")

    class _Delivery:
        delivery_mode = "failed"
        verify_url = None

    async def _fake_send_verification_email(to_email, agent_name, verify_url):
        return _Delivery()

    monkeypatch.setattr(claim_module, "send_verification_email", _fake_send_verification_email)

    res = client.post(
        f"/api/v1/agents/claim/{claim_code}/verify",
        json={"email": "owner@example.com"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is False
    assert body["delivery_mode"] == "failed"
    assert body["verify_url"] is None
    assert "retry" in body["next_step"].lower()


def test_claim_confirm_success_page_includes_bind_guidance(client, db_engine):
    _agent_id, claim_code = _create_pending_agent(db_engine, name="claim-success-page")

    verify = client.post(
        f"/api/v1/agents/claim/{claim_code}/verify",
        json={"email": "owner@example.com"},
    )
    assert verify.status_code == 200, verify.text
    verify_body = verify.json()
    verify_url = verify_body["verify_url"]
    assert verify_url is not None

    confirm = client.get(urlparse(verify_url).path + "?" + urlparse(verify_url).query)
    assert confirm.status_code == 200
    assert "Bind Agent" in confirm.text
    assert "Log in to bind agent" in confirm.text
    assert f"claim_code={claim_code}" in confirm.text


def test_oauth_start_encodes_return_to_and_callback_redirects(client, db_engine, monkeypatch):
    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", _FakeAsyncClient)
    agent_id, claim_code = _create_pending_agent(db_engine, name="oauth-return-to")

    start = client.get(
        "/api/v1/oauth/github",
        params={"claim_code": claim_code, "return_to": "/bind-agent?source=oauth"},
        follow_redirects=False,
    )
    assert start.status_code in (302, 307)

    location = start.headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    decoded = oauth_module._decode_oauth_state(state)
    assert decoded["rt"] == "/bind-agent?source=oauth"

    callback = client.get(
        "/api/v1/oauth/github/callback",
        params={"code": "mock-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302, callback.text
    redirect = callback.headers["location"]
    assert redirect.startswith("http://localhost:3000/bind-agent?source=oauth")
    assert f"agent_id={agent_id}" in redirect
    assert f"claim_code={claim_code}" in redirect
