import json

import skills.api_router as skills_api_router


class _FakeAgent:
    def __init__(self, envelope):
        self.envelope = envelope
        self.agent_id = "api-router"
        self.role = "router"
        self.memory_calls = []

    def use_skill_enveloped(self, skill_name, **kwargs):
        return self.envelope

    def use_skill(self, skill_name, **kwargs):
        self.memory_calls.append((skill_name, kwargs))
        return {"status": "ok"}


def _memory_payload(fake_agent):
    assert len(fake_agent.memory_calls) == 1
    skill_name, kwargs = fake_agent.memory_calls[0]
    assert skill_name == "persistent_memory"
    return kwargs, json.loads(kwargs["content"])


def test_start_sync_success_writes_success_memory(client, auth_headers, monkeypatch):
    fake_agent = _FakeAgent(
        {
            "ok": True,
            "data": {"items": []},
            "message": "ok",
            "meta": {},
        }
    )
    monkeypatch.setattr(skills_api_router, "_AGENT", fake_agent)

    res = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {"token": "secret-value"}},
        headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["ok"] is True

    kwargs, payload = _memory_payload(fake_agent)
    assert kwargs["metadata"] == {
        "skill_name": "list_templates",
        "schema_version": 1,
        "kind": "success_case",
    }
    assert payload["template"] == "skill_success_case_v1"
    assert payload["skill"] == "list_templates"
    assert payload["args"]["token"] == "***"
    assert payload["result_summary"] == {"ok": True, "message": "ok"}
    assert payload["labels"]["trace_id"] is not None
    assert isinstance(payload["labels"]["duration_ms"], int)


def test_start_sync_failed_envelope_writes_failure_memory(client, auth_headers, monkeypatch):
    fake_agent = _FakeAgent(
        {
            "ok": False,
            "data": None,
            "message": "failed to execute",
            "error": {
                "code": "bad_input",
                "reason": "token exploded",
                "retriable": False,
            },
            "meta": {},
        }
    )
    monkeypatch.setattr(skills_api_router, "_AGENT", fake_agent)

    res = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {"token": "secret-value"}},
        headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["ok"] is False

    kwargs, payload = _memory_payload(fake_agent)
    assert kwargs["metadata"] == {
        "skill_name": "list_templates",
        "schema_version": 1,
        "kind": "failure_case",
        "failure_type": "failed_envelope",
    }
    assert payload["template"] == "skill_failure_case_v1"
    assert payload["skill"] == "list_templates"
    assert payload["args"]["token"] == "***"
    assert payload["result_summary"] == {"ok": False, "message": "failed to execute"}
    assert payload["error"] == {
        "code": "bad_input",
        "reason": "token exploded",
        "retriable": False,
    }
    assert payload["labels"]["failure_type"] == "failed_envelope"
    assert payload["labels"]["trace_id"] is not None
    assert isinstance(payload["labels"]["duration_ms"], int)


def test_start_sync_timeout_writes_failure_memory(client, auth_headers, monkeypatch):
    fake_agent = _FakeAgent(
        {
            "ok": True,
            "data": {"items": []},
            "message": "ok",
            "meta": {},
        }
    )
    monkeypatch.setattr(skills_api_router, "_AGENT", fake_agent)
    monkeypatch.setenv("SKILLS_REQUEST_TIMEOUT", "0.001")

    res = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {"token": "secret-value"}},
        headers=auth_headers,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"

    kwargs, payload = _memory_payload(fake_agent)
    assert kwargs["metadata"] == {
        "skill_name": "list_templates",
        "schema_version": 1,
        "kind": "failure_case",
        "failure_type": "timeout",
    }
    assert payload["template"] == "skill_failure_case_v1"
    assert payload["error"]["code"] == "timeout"
    assert payload["error"]["retriable"] is True
    assert payload["labels"]["failure_type"] == "timeout"
    assert payload["args"]["token"] == "***"


def _fake_async_exception(*args, **kwargs):
    raise RuntimeError("boom token")


def test_run_skill_and_store_failed_envelope_writes_failure_memory(monkeypatch):
    fake_agent = _FakeAgent(
        {
            "ok": False,
            "data": None,
            "message": "async failed",
            "error": {
                "code": "async_bad_input",
                "reason": "bad token",
                "retriable": False,
            },
            "meta": {},
        }
    )
    monkeypatch.setattr(skills_api_router, "_AGENT", fake_agent)
    monkeypatch.setattr(skills_api_router, "_set_job_running", lambda job_id: object())
    monkeypatch.setattr(skills_api_router, "_set_job_result", lambda **kwargs: None)
    monkeypatch.setattr(skills_api_router, "_write_audit", lambda *args, **kwargs: None)

    import asyncio

    asyncio.run(
        skills_api_router._run_skill_and_store(
            job_id="job-1",
            name="list_templates",
            args={"token": "secret-value"},
            trace_id="trace-1",
            actor_id="agent-1",
        )
    )

    kwargs, payload = _memory_payload(fake_agent)
    assert kwargs["metadata"] == {
        "skill_name": "list_templates",
        "schema_version": 1,
        "kind": "failure_case",
        "failure_type": "failed_envelope",
    }
    assert payload["template"] == "skill_failure_case_v1"
    assert payload["error"]["code"] == "async_bad_input"
    assert payload["labels"]["failure_type"] == "failed_envelope"
    assert payload["args"]["token"] == "***"


def test_run_skill_and_store_exception_writes_failure_memory(monkeypatch):
    fake_agent = _FakeAgent({"ok": True})
    monkeypatch.setattr(skills_api_router, "_AGENT", fake_agent)
    monkeypatch.setattr(skills_api_router, "_set_job_running", lambda job_id: object())
    monkeypatch.setattr(skills_api_router, "_set_job_result", lambda **kwargs: None)
    monkeypatch.setattr(skills_api_router, "_write_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(fake_agent, "use_skill_enveloped", _fake_async_exception)

    import asyncio

    asyncio.run(
        skills_api_router._run_skill_and_store(
            job_id="job-1",
            name="list_templates",
            args={"token": "secret-value"},
            trace_id="trace-1",
            actor_id="agent-1",
        )
    )

    kwargs, payload = _memory_payload(fake_agent)
    assert kwargs["metadata"] == {
        "skill_name": "list_templates",
        "schema_version": 1,
        "kind": "failure_case",
        "failure_type": "exception",
    }
    assert payload["template"] == "skill_failure_case_v1"
    assert payload["error"]["code"] == "skill_execution_error"
    assert payload["error"]["reason"] == "boom token"
    assert payload["labels"]["failure_type"] == "exception"
    assert payload["args"]["token"] == "***"
