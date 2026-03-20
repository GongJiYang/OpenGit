from uuid import uuid4

from sqlmodel import Session

from core.settings import clear_settings_cache
from agent_auth.models.runner import AuditLog, ComputeJob, ComputeJobStatus, ExecutionMode


def _seed_job_and_audit() -> str:
    from persistence import get_engine

    with Session(get_engine()) as session:
        job = ComputeJob(
            bounty_id="bounty-test",
            execution_mode=ExecutionMode.SHARED_LOCAL,
            test_command="pytest",
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        audit = AuditLog(
            job_id=job.id,
            runner_id=uuid4(),
            status="pending",
            reason="periodic_audit",
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        return str(audit.id)


def test_internal_audit_pending_requires_internal_token(client):
    res = client.get("/api/v1/runners/internal/audit/pending")
    assert res.status_code == 422


def test_internal_audit_pending_rejects_invalid_internal_token(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    res = client.get(
        "/api/v1/runners/internal/audit/pending",
        headers={"X-Internal-Token": "wrong"},
    )
    assert res.status_code == 403


def test_internal_audit_pending_accepts_internal_token(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    res = client.get(
        "/api/v1/runners/internal/audit/pending",
        headers={"X-Internal-Token": "internal-secret"},
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_internal_audit_submit_requires_valid_internal_token(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    audit_id = _seed_job_and_audit()

    bad = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={"audit_id": audit_id, "audited_stdout": "ok", "audited_exit_code": 0},
        headers={"X-Internal-Token": "wrong"},
    )
    assert bad.status_code == 403

    good = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={"audit_id": audit_id, "audited_stdout": "ok", "audited_exit_code": 0},
        headers={"X-Internal-Token": "internal-secret"},
    )
    assert good.status_code == 200
    body = good.json()
    assert body["success"] is True
    assert body["audit_id"] == audit_id
