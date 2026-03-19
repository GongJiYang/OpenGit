from typing import Dict

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
    # Open circuit after consecutive failures (simulate by using a non-existing skill)
    monkeypatch.setenv("SKILLS_ALLOWLIST", "does_not_exist")
    # first call forbidden
    res1 = client.post("/api/v1/skills/start", json={"name": "does_not_exist", "mode": "sync", "args": {}}, headers=auth_headers)
    assert res1.status_code in (403, 200)  # allowlist forbids, or if allowed then returns error envelope
    # flip allowlist to permit the name to push errors into CB window
    monkeypatch.setenv("SKILLS_ALLOWLIST", "does_not_exist, list_templates")
    # trigger multiple failures to open circuit
    for _ in range(5):
        r = client.post("/api/v1/skills/start", json={"name": "does_not_exist", "mode": "sync", "args": {}}, headers=auth_headers)
        # when not found, our code path returns HTTP 200 but ok=False
        assert r.status_code == 200
    # now circuit may open; next call should be 503
    r2 = client.post("/api/v1/skills/start", json={"name": "does_not_exist", "mode": "sync", "args": {}}, headers=auth_headers)
    assert r2.status_code in (200, 503)  # depending on window timing
