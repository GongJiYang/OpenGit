import os
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import pytest
from sqlmodel import SQLModel, Session, create_engine, select


# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from persistence import Bounty, BountyStatus, AuditLog  # noqa: E402
from agent_auth.services.scheduler import cleanup_expired_temporary_claims, setup_scheduled_tasks  # noqa: E402
import agent_auth.services.scheduler as scheduler_module  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _create_bounty(s: Session, *, status: str, temporary: bool, expires_at):
    bounty = Bounty(
        title="Temporary Claim Cleanup",
        description="",
        reward=1,
        repo_name="owner/repo",
        required_role="contributor",
        status=status,
        assignee="agent-temp" if status == BountyStatus.IN_PROGRESS.value else None,
        is_temporary_claim=temporary,
        claim_expires_at=expires_at,
        test_command="pytest",
        verification_mode="auto",
    )
    s.add(bounty)
    s.commit()
    s.refresh(bounty)
    return bounty


def test_scheduler_cleanup_expired_temporary_claims_uses_fsm_and_emits_audit(session: Session):
    expired = _create_bounty(
        session,
        status=BountyStatus.IN_PROGRESS.value,
        temporary=True,
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )

    result = cleanup_expired_temporary_claims(session)

    assert result["released_count"] == 1
    refreshed = session.get(Bounty, expired.id)
    assert refreshed is not None
    assert refreshed.status == BountyStatus.OPEN.value
    assert refreshed.assignee is None
    assert refreshed.is_temporary_claim is False
    assert refreshed.claim_expires_at is None

    audits = session.exec(select(AuditLog).where(AuditLog.action == "status_transition")).all()
    assert len(audits) == 1
    detail = audits[0].detail or {}
    assert detail.get("bounty_id") == expired.id
    assert detail.get("from") == BountyStatus.IN_PROGRESS.value
    assert detail.get("to") == BountyStatus.OPEN.value
    assert detail.get("actor_type") == "system"


def test_scheduler_cleanup_expired_temporary_claims_ignores_non_temporary_in_progress(session: Session):
    regular = _create_bounty(
        session,
        status=BountyStatus.IN_PROGRESS.value,
        temporary=False,
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )

    result = cleanup_expired_temporary_claims(session)

    assert result["released_count"] == 0
    refreshed = session.get(Bounty, regular.id)
    assert refreshed is not None
    assert refreshed.status == BountyStatus.IN_PROGRESS.value

    audits = session.exec(select(AuditLog).where(AuditLog.action == "status_transition")).all()
    assert audits == []


def test_scheduler_job_uses_cleanup_function_path(monkeypatch):
    captured = {}

    class _FakeSessionCtx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    def _session_factory():
        return _FakeSessionCtx()

    def _fake_cleanup(_session):
        captured["called"] = True
        return {"released_count": 0, "checked_at": "now"}

    monkeypatch.setattr("agent_auth.services.scheduler.cleanup_expired_temporary_claims", _fake_cleanup)

    scheduler = setup_scheduled_tasks(_session_factory)
    try:
        job = scheduler.get_job("temporary_claim_cleanup")
        assert job is not None
        job.func()
    finally:
        if isinstance(scheduler, AsyncIOScheduler) and scheduler.running:
            scheduler.shutdown(wait=False)

    assert captured.get("called") is True


def test_scheduler_job_semantics_match_cleanup_function():
    def _seed_dataset(s: Session):
        temp = _create_bounty(
            s,
            status=BountyStatus.IN_PROGRESS.value,
            temporary=True,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        regular = _create_bounty(
            s,
            status=BountyStatus.IN_PROGRESS.value,
            temporary=False,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        return temp.id, regular.id

    # Baseline: direct cleanup function path
    baseline_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(baseline_engine)
    with Session(baseline_engine) as baseline_session:
        baseline_temp_id, baseline_regular_id = _seed_dataset(baseline_session)
        expected = cleanup_expired_temporary_claims(baseline_session)
        expected_audit_count = len(
            baseline_session.exec(select(AuditLog).where(AuditLog.action == "status_transition")).all()
        )
        expected_temp = baseline_session.get(Bounty, baseline_temp_id)
        expected_regular = baseline_session.get(Bounty, baseline_regular_id)

    # Actual: scheduler job path
    actual_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(actual_engine)
    with Session(actual_engine) as actual_session:
        actual_temp_id, actual_regular_id = _seed_dataset(actual_session)

        class _SessionCtx:
            def __enter__(self):
                return actual_session

            def __exit__(self, exc_type, exc, tb):
                return False

        scheduler_module._scheduler = None
        scheduler = setup_scheduled_tasks(lambda: _SessionCtx())
        try:
            job = scheduler.get_job("temporary_claim_cleanup")
            assert job is not None
            job.func()
        finally:
            if isinstance(scheduler, AsyncIOScheduler) and scheduler.running:
                scheduler.shutdown(wait=False)
            scheduler_module._scheduler = None

        actual_audit_count = len(
            actual_session.exec(select(AuditLog).where(AuditLog.action == "status_transition")).all()
        )
        actual_temp = actual_session.get(Bounty, actual_temp_id)
        actual_regular = actual_session.get(Bounty, actual_regular_id)

    assert expected["released_count"] == 1
    assert expected_audit_count == 1
    assert actual_audit_count == expected_audit_count

    assert actual_temp is not None and expected_temp is not None
    assert actual_temp.status == expected_temp.status == BountyStatus.OPEN.value
    assert actual_temp.assignee == expected_temp.assignee is None
    assert actual_temp.is_temporary_claim == expected_temp.is_temporary_claim is False
    assert actual_temp.claim_expires_at == expected_temp.claim_expires_at is None

    assert actual_regular is not None and expected_regular is not None
    assert actual_regular.status == expected_regular.status == BountyStatus.IN_PROGRESS.value
    assert actual_regular.assignee == expected_regular.assignee == "agent-temp"
