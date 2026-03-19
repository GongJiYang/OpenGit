import time
from typing import Dict, Optional

from sqlmodel import Session, select

from persistence import SkillAsyncJob, get_engine


def _fetch_skill_job(job_id: str) -> Optional[SkillAsyncJob]:
    with Session(get_engine()) as session:
        stmt = select(SkillAsyncJob).where(SkillAsyncJob.job_id == job_id)
        return session.exec(stmt).first()


def test_start_async_queue_and_poll(client, monkeypatch, auth_headers):
    # allow list_templates
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    r = client.post("/api/v1/skills/start", json={"name": "list_templates", "mode": "async", "args": {}}, headers=auth_headers)
    assert r.status_code == 200
    body: Dict = r.json()
    assert body["ok"] is True and body["message"] == "job queued"
    job_id = body["job"]["id"]

    queued = _fetch_skill_job(job_id)
    assert queued is not None
    assert queued.status in ("queued", "running", "succeeded", "failed")
    assert queued.skill_name == "list_templates"
    assert queued.actor_id is not None
    assert queued.args_hash is not None

    # poll until finished (simple loop)
    for _ in range(10):
        pr = client.get(f"/api/v1/skills/jobs/{job_id}", headers=auth_headers)
        assert pr.status_code == 200
        pb = pr.json()
        if pb.get("ok") and pb.get("data") is not None:
            # final envelope reached
            assert "meta" in pb and "trace_id" in pb["meta"]
            break
        time.sleep(0.2)
    else:
        raise AssertionError("job did not finish in time")

    finished = _fetch_skill_job(job_id)
    assert finished is not None
    assert finished.status in ("succeeded", "failed")
    assert finished.result is not None
    assert finished.finished_at is not None
    assert finished.result_hash is not None


def test_jobs_unauthorized(client):
    # missing API key
    res = client.get("/api/v1/skills/jobs/unknown")
    assert res.status_code == 401


def test_jobs_not_found(client, auth_headers):
    res = client.get("/api/v1/skills/jobs/unknown", headers=auth_headers)
    assert res.status_code == 404
