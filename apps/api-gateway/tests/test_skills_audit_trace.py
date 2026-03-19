from typing import Dict

def _headers():
    return {"X-API-Key": "test-key", "X-Trace-Id": "abc123"}


def test_trace_id_roundtrip_and_audit(client, monkeypatch):
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    r = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {}},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert r.headers.get("X-Trace-Id") == "abc123"
    body: Dict = r.json()
    assert body.get("meta", {}).get("trace_id") == "abc123"
