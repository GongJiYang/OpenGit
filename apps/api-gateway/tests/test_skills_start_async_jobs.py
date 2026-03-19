import time
from typing import Dict

def _headers():
    return {"X-API-Key": "test-key"}


def test_start_async_queue_and_poll(client, monkeypatch):
    # allow list_templates
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    r = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "async", "args": {}}, headers=_headers())
    assert r.status_code == 200
    body: Dict = r.json()
    assert body["ok"] is True and body["message"] == "job queued"
    job_id = body["job"]["id"]

    # poll until finished (simple loop)
    for _ in range(10):
        pr = client.get(f"/api/v1/skills/jobs/{job_id}", headers=_headers())
        assert pr.status_code == 200
        pb = pr.json()
        if pb.get("ok") and pb.get("data") is not None:
            # final envelope reached
            assert "meta" in pb and "trace_id" in pb["meta"]
            break
        time.sleep(0.2)
    else:
        raise AssertionError("job did not finish in time")


def test_jobs_unauthorized(client):
    # missing API key
    res = client.get("/api/v1/skills/jobs/unknown")
    assert res.status_code == 401


def test_jobs_not_found(client):
    res = client.get("/api/v1/skills/jobs/unknown", headers=_headers())
    assert res.status_code == 404
