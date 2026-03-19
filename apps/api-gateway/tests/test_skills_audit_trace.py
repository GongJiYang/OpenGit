from typing import Dict


def test_trace_id_roundtrip_and_audit(client, monkeypatch, auth_headers):
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    headers = {**auth_headers, "X-Trace-Id": "abc123"}
    r = client.post(
        "/api/v1/skills/start",
        json={"name": "list_templates", "mode": "sync", "args": {}},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.headers.get("X-Trace-Id") == "abc123"
    body: Dict = r.json()
    assert body.get("meta", {}).get("trace_id") == "abc123"
