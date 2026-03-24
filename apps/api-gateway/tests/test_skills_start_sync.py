from typing import Dict

import skills.api_router as skills_api_router


def test_start_sync_ok(client, auth_headers):
    # allow list_templates
    res = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "sync", "args": {}}, headers=auth_headers)
    assert res.status_code == 200
    body: Dict = res.json()
    assert body["ok"] is True
    assert isinstance(body.get("message"), str)
    assert "meta" in body and "trace_id" in body["meta"] and "duration_ms" in body["meta"]
    assert "job" in body

def test_start_sync_unauthorized(client):
    res = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "sync", "args": {}})
    assert res.status_code == 401


def test_start_sync_forbidden_by_allowlist(client, monkeypatch, auth_headers):
    monkeypatch.setenv("SKILLS_ALLOWLIST", "read_file@1.0.0")
    res = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "sync", "args": {}}, headers=auth_headers)
    assert res.status_code == 403


def test_start_sync_invalid_mode(client, auth_headers):
    res = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "invalid", "args": {}}, headers=auth_headers)
    assert res.status_code == 400


def test_start_sync_timeout(client, monkeypatch, auth_headers):
    # Use a template skill but force a very low timeout
    monkeypatch.setenv("SKILLS_REQUEST_TIMEOUT", "0.001")
    res = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "sync", "args": {}}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"
    assert body["error"]["retriable"] is True


def test_start_sync_circuit_breaker(client, monkeypatch, auth_headers):
    # Configure deterministic CB behavior for this test only.
    monkeypatch.setenv("SKILLS_ALLOWLIST", "does_not_exist")
    monkeypatch.setattr(skills_api_router, "_CB_ENABLED", True)
    monkeypatch.setattr(skills_api_router, "_CB_WINDOW", 3)
    monkeypatch.setattr(skills_api_router, "_CB_FAIL_RATE", 0.5)
    monkeypatch.setattr(skills_api_router, "_CB_OPEN_SECS", 5)
    skills_api_router._CB_RECENT.clear()
    skills_api_router._CB_OPEN_UNTIL.clear()

    # Allowlist hit must be rejected deterministically.
    res1 = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {}},
        headers=auth_headers,
    )
    assert res1.status_code == 403

    # Now allow does_not_exist so repeated failed envelopes feed CB window.
    monkeypatch.setenv("SKILLS_ALLOWLIST", "does_not_exist, list_templates")
    for _ in range(3):
        r = client.post(
            "/api/v1/skills/start",
            json={"name": "does_not_exist", "mode": "sync", "args": {}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False

    # Window is full with failures; CB must be open now.
    r2 = client.post(
        "/api/v1/skills/start",
        json={"name": "does_not_exist", "mode": "sync", "args": {}},
        headers=auth_headers,
    )
    assert r2.status_code == 503
    assert r2.json().get("detail") == "Circuit open for skill; please retry later"
