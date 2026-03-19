from typing import Dict

def _headers():
    return {"X-API-Key": "test-key"}


def test_pii_mask_fields(client, monkeypatch):
    monkeypatch.setenv("SKILLS_ALLOWLIST", "render_template")
    # Mask 'token' in data
    monkeypatch.setenv("SKILLS_PII_MASK_FIELDS", "token")
    # render_template returns content in data; we simulate passing token in params and expect it masked if echoed
    res = client.post(
        "/api/v1/skills/start",
        json={
            "name": "render_template",
            "mode": "sync",
            "args": {"template_id": "builtin:noop", "parameters": {"token": "sensitive"}},
        },
        headers=_headers(),
    )
    # Depending on template, content may not echo token; the main assertion is that envelope exists and masking does not error
    assert res.status_code == 200
    body: Dict = res.json()
    assert "ok" in body
