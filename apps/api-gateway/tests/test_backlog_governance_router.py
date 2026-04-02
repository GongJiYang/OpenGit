import asyncio
import json
from datetime import datetime, timedelta

import bcrypt
import pytest
from sqlmodel import Session

from agent_auth.models import Agent, AgentStatus
from agent_auth.utils import API_KEY_LENGTH, API_KEY_PREFIX, get_api_key_prefix
from core.settings import clear_settings_cache
from persistence import SkillAsyncJob, get_engine
import routers.backlog_governance as backlog_router


@pytest.fixture(autouse=True)
def _clear_settings_between_tests():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _create_claimed_agent_headers(raw_api_key: str):
    hashed = bcrypt.hashpw(raw_api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    claim_code = f"BK{raw_api_key[-6:].upper()}"
    with Session(get_engine()) as session:
        agent = Agent(
            name=f"backlog-router-agent-{raw_api_key[-6:]}",
            model_name="test-model",
            api_key_hash=hashed,
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code=claim_code,
            claim_url=f"/api/v1/agents/claim/{claim_code}",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(agent)
        session.commit()

    return {"X-API-Key": raw_api_key}


def _fetch_backlog_job(job_id: str):
    with Session(get_engine()) as session:
        return session.get(SkillAsyncJob, job_id)


def test_backlog_route_is_mounted(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()
    res = client.get("/api/v1/backlog/health")
    assert res.status_code != 404


def test_backlog_health_requires_governance_mode(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "off")
    clear_settings_cache()
    res = client.get("/api/v1/backlog/health")
    assert res.status_code == 403


def test_backlog_health_configured_flag(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()
    monkeypatch.setenv("BACKLOG_MCP_COMMAND", "python -c \"print('ok')\"")

    res = client.get("/api/v1/backlog/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["governance_mode"] == "observe"


def test_backlog_start_sync_success(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    async def fake_start(self, repo_name: str, payload=None):
        return {"repo": repo_name, "received": payload or {}}

    monkeypatch.setattr(backlog_router.BacklogMcpAdapter, "start", fake_start)

    raw_api_key = API_KEY_PREFIX + ("z" * API_KEY_LENGTH)
    headers = _create_claimed_agent_headers(raw_api_key)

    res = client.post(
        "/api/v1/backlog/start",
        json={"repo_name": "owner/repo-a", "mode": "sync", "args": {"ticket": "BK-1"}},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["message"] == "ok"
    assert body["data"]["repo"] == "owner/repo-a"
    assert body["data"]["received"]["ticket"] == "BK-1"
    assert body["job"]["status"] == "succeeded"


def test_backlog_start_sync_failure_returns_envelope(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    async def fake_start(self, repo_name: str, payload=None):
        raise backlog_router.BacklogMcpAdapterError("boom")

    monkeypatch.setattr(backlog_router.BacklogMcpAdapter, "start", fake_start)

    raw_api_key = API_KEY_PREFIX + ("y" * API_KEY_LENGTH)
    headers = _create_claimed_agent_headers(raw_api_key)

    res = client.post(
        "/api/v1/backlog/start",
        json={"repo_name": "owner/repo-b", "mode": "sync"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "backlog_execution_error"


def test_backlog_start_invalid_mode(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    raw_api_key = API_KEY_PREFIX + ("x" * API_KEY_LENGTH)
    headers = _create_claimed_agent_headers(raw_api_key)

    res = client.post(
        "/api/v1/backlog/start",
        json={"repo_name": "owner/repo-c", "mode": "bad"},
        headers=headers,
    )
    assert res.status_code == 400


def test_backlog_start_requires_auth(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()
    res = client.post("/api/v1/backlog/start", json={"repo_name": "owner/repo-d", "mode": "sync"})
    assert res.status_code == 401


def test_backlog_async_job_queue_and_poll(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    async def fake_start(self, repo_name: str, payload=None):
        return {"repo": repo_name, "ok": True}

    monkeypatch.setattr(backlog_router.BacklogMcpAdapter, "start", fake_start)

    raw_api_key = API_KEY_PREFIX + ("w" * API_KEY_LENGTH)
    headers = _create_claimed_agent_headers(raw_api_key)

    res = client.post(
        "/api/v1/backlog/start",
        json={"repo_name": "owner/repo-e", "mode": "async", "args": {"ticket": "BK-2"}},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["message"] == "job queued"
    job_id = body["job"]["id"]

    rec = _fetch_backlog_job(job_id)
    assert rec is not None
    assert rec.skill_name == "backlog.start"

    poll = client.get(f"/api/v1/backlog/jobs/{job_id}", headers=headers)
    assert poll.status_code == 200
    poll_body = poll.json()
    assert poll_body["ok"] is True
    if poll_body.get("data") is not None:
        assert poll_body["job"]["status"] == "succeeded"
    else:
        assert poll_body["message"] in {"job queued", "job in progress"}


def test_backlog_job_not_found(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    raw_api_key = API_KEY_PREFIX + ("v" * API_KEY_LENGTH)
    headers = _create_claimed_agent_headers(raw_api_key)

    res = client.get("/api/v1/backlog/jobs/unknown", headers=headers)
    assert res.status_code == 404


def test_backlog_job_owner_forbidden(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "observe")
    clear_settings_cache()

    async def fake_start(self, repo_name: str, payload=None):
        return {"repo": repo_name, "ok": True}

    monkeypatch.setattr(backlog_router.BacklogMcpAdapter, "start", fake_start)

    owner_key = API_KEY_PREFIX + ("u" * API_KEY_LENGTH)
    owner_headers = _create_claimed_agent_headers(owner_key)
    other_key = API_KEY_PREFIX + ("t" * API_KEY_LENGTH)
    other_headers = _create_claimed_agent_headers(other_key)

    queued = client.post(
        "/api/v1/backlog/start",
        json={"repo_name": "owner/repo-f", "mode": "async", "args": {}},
        headers=owner_headers,
    )
    assert queued.status_code == 200
    job_id = queued.json()["job"]["id"]

    forbidden = client.get(f"/api/v1/backlog/jobs/{job_id}", headers=other_headers)
    assert forbidden.status_code == 403


def test_backlog_adapter_parses_jsonrpc_lines(monkeypatch):
    adapter = backlog_router.BacklogMcpAdapter(command=["python", "-c", "print('x')"])
    parsed = adapter._parse_response("noise\n{\"jsonrpc\":\"2.0\",\"result\":{\"ok\":true}}\n")
    assert parsed["result"]["ok"] is True


def test_backlog_adapter_call_invokes_subprocess(monkeypatch):
    class _FakeProcess:
        returncode = 0

        async def communicate(self, input_data):
            payload = json.loads(input_data.decode("utf-8").strip())
            assert payload["method"] == "backlog.start"
            return b'{"jsonrpc":"2.0","result":{"done":true}}\n', b""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        assert args[0] == "python"
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    adapter = backlog_router.BacklogMcpAdapter(command=["python", "-c", "print('ok')"], timeout_seconds=1)
    result = asyncio.run(adapter.call("backlog.start", {"repo_name": "owner/repo-g"}))
    assert result["done"] is True
