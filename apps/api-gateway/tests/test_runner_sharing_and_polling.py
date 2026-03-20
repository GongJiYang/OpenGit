from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

import bcrypt
from sqlmodel import Session, select

from agent_auth.models.platform import User
from agent_auth.models.runner import (
    ComputeJob,
    ComputeJobStatus,
    ExecutionMode,
    Runner,
    RunnerPoolType,
    RunnerShareGrant,
    RunnerStatus,
)


def _create_user(session: Session, suffix: str) -> UUID:
    user = User(
        email=f"runner-{suffix}@example.com",
        display_name=f"runner-{suffix}",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


def _create_runner(
    session: Session,
    owner_user_id: UUID,
    pool_type: RunnerPoolType = RunnerPoolType.PRIVATE,
    is_global: bool = True,
    allowed_repo_ids: Optional[List[str]] = None,
) -> Tuple[UUID, str]:
    token = f"ahauth_{uuid4().hex}"
    token_hash = bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    runner = Runner(
        name=f"runner-{uuid4().hex[:8]}",
        owner_user_id=owner_user_id,
        token_hash=token_hash,
        status=RunnerStatus.ONLINE,
        last_heartbeat_at=datetime.utcnow() - timedelta(seconds=5),
        pool_type=pool_type,
        is_global=is_global,
        allowed_repo_ids=allowed_repo_ids or [],
    )
    session.add(runner)
    session.commit()
    session.refresh(runner)
    return runner.id, token


def _create_job(
    session: Session,
    requester_user_id: UUID,
    status: ComputeJobStatus = ComputeJobStatus.PENDING,
    execution_mode: ExecutionMode = ExecutionMode.SELF_HOSTED,
    repo_id: Optional[UUID] = None,
) -> UUID:
    job = ComputeJob(
        bounty_id=f"bounty-{uuid4().hex[:8]}",
        execution_mode=execution_mode,
        test_command="pytest",
        timeout_seconds=300,
        status=status,
        requester_user_id=requester_user_id,
        repo_id=repo_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job.id


def test_poll_jobs_private_runner_accepts_owner_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-private")
        runner_id, token = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        _create_job(session, requester_user_id=owner_id)

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert len(res.json()) == 1

    with Session(get_engine()) as session:
        db_runner = session.get(Runner, runner_id)
        assigned_job = session.exec(select(ComputeJob).where(ComputeJob.runner_id == runner_id)).first()
        assert assigned_job is not None
        assert assigned_job.status == ComputeJobStatus.ASSIGNED
        assert db_runner is not None
        assert db_runner.status == RunnerStatus.BUSY


def test_poll_jobs_private_runner_rejects_non_owner_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-private-deny")
        other_id = _create_user(session, "other-private-deny")
        _, token = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(session, requester_user_id=other_id)

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert res.json() == []

    with Session(get_engine()) as session:
        db_job = session.get(ComputeJob, job_id)
        assert db_job is not None
        assert db_job.status == ComputeJobStatus.PENDING
        assert db_job.runner_id is None


def test_poll_jobs_shared_runner_accepts_granted_user_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-shared-allow")
        grantee_id = _create_user(session, "grantee-shared-allow")
        runner_id, token = _create_runner(session, owner_id, pool_type=RunnerPoolType.SHARED)

        grant = RunnerShareGrant(
            runner_id=runner_id,
            grantee_user_id=grantee_id,
            granted_by_user_id=owner_id,
            can_execute=True,
        )
        session.add(grant)
        session.commit()

        _create_job(session, requester_user_id=grantee_id)

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_poll_jobs_shared_runner_rejects_ungranted_user_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-shared-deny")
        outsider_id = _create_user(session, "outsider-shared-deny")
        _, token = _create_runner(session, owner_id, pool_type=RunnerPoolType.SHARED)
        _create_job(session, requester_user_id=outsider_id)

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert res.json() == []


def test_poll_jobs_platform_runner_accepts_any_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-platform")
        other_id = _create_user(session, "other-platform")
        _, token = _create_runner(session, owner_id, pool_type=RunnerPoolType.PLATFORM)
        _create_job(session, requester_user_id=other_id)

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_update_runner_repos_supports_pool_type(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-update-pool")
        runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)

    res = client.put(
        f"/api/v1/runners/{runner_id}/repos",
        headers={"X-User-Id": str(owner_id)},
        json={"allowed_repo_ids": [], "is_global": True, "pool_type": "shared"},
    )
    assert res.status_code == 200
    assert res.json()["pool_type"] == "shared"


def test_runner_share_grant_crud_requires_owner(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-share-crud")
        grantee_id = _create_user(session, "grantee-share-crud")
        other_id = _create_user(session, "other-share-crud")
        runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)

    bad = client.post(
        f"/api/v1/runners/{runner_id}/shares",
        headers={"X-User-Id": str(other_id)},
        json={"grantee_user_id": str(grantee_id), "can_execute": True},
    )
    assert bad.status_code == 403

    good = client.post(
        f"/api/v1/runners/{runner_id}/shares",
        headers={"X-User-Id": str(owner_id)},
        json={"grantee_user_id": str(grantee_id), "can_execute": True},
    )
    assert good.status_code == 200

    listed = client.get(
        f"/api/v1/runners/{runner_id}/shares",
        headers={"X-User-Id": str(owner_id)},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    deleted = client.delete(
        f"/api/v1/runners/{runner_id}/shares/{grantee_id}",
        headers={"X-User-Id": str(owner_id)},
    )
    assert deleted.status_code == 200
