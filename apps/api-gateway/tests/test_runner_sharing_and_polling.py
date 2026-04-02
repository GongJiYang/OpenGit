import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

import bcrypt
from sqlmodel import Session, select

from agent_auth.models import Agent
from agent_auth.models.platform import MembershipStatus, Repo, RepoMember, RepoRole, User, UserAgentBinding
from dependencies.auth import require_active_identity
from main import app
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
        token_lookup=hashlib.sha256(token.encode("utf-8")).hexdigest(),
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
    requester_agent_id: Optional[UUID] = None,
) -> UUID:
    job = ComputeJob(
        bounty_id=f"bounty-{uuid4().hex[:8]}",
        execution_mode=execution_mode,
        test_command="pytest",
        timeout_seconds=300,
        status=status,
        requester_user_id=requester_user_id,
        requester_agent_id=requester_agent_id,
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


def test_poll_jobs_shared_runner_accepts_agent_bound_owner_requester(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        runner_owner_id = _create_user(session, "owner-shared-bound")
        bound_user_id = _create_user(session, "bound-shared-bound")
        bound_agent = Agent(
            name=f"agent-{uuid4().hex[:8]}",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix=f"prefix-{uuid4().hex[:6]}",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim",
            claim_expires_at=datetime.utcnow() + timedelta(days=1),
            status="claimed",
            role="contributor",
        )
        session.add(bound_agent)
        session.commit()
        session.refresh(bound_agent)

        session.add(UserAgentBinding(user_id=bound_user_id, agent_id=bound_agent.id))
        session.commit()

        runner_id, token = _create_runner(session, runner_owner_id, pool_type=RunnerPoolType.SHARED)
        session.add(
            RunnerShareGrant(
                runner_id=runner_id,
                grantee_user_id=bound_user_id,
                granted_by_user_id=runner_owner_id,
                can_execute=True,
            )
        )
        session.commit()

        _create_job(
            session,
            requester_user_id=bound_user_id,
            requester_agent_id=bound_agent.id,
        )

    res = client.get("/api/v1/runners/poll-jobs", headers={"X-Runner-Token": token})
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_poll_jobs_rejects_invalid_token_without_full_scan(client, monkeypatch):
    from persistence import get_engine
    from agent_auth.routers import runner as runner_router

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-invalid-token")
        _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)

    call_counter = {"count": 0}
    original_verify = runner_router._verify_token

    def _counted_verify(token: str, token_hash: str) -> bool:
        call_counter["count"] += 1
        return original_verify(token, token_hash)

    monkeypatch.setattr(runner_router, "_verify_token", _counted_verify)

    res = client.get(
        "/api/v1/runners/poll-jobs",
        headers={"X-Runner-Token": f"ahauth_{uuid4().hex}"},
    )

    assert res.status_code == 401
    assert call_counter["count"] == 0


def test_poll_jobs_only_verifies_single_lookup_candidate(client, monkeypatch):
    from persistence import get_engine
    from agent_auth.routers import runner as runner_router

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-single-verify")
        _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        _create_runner(session, owner_id, pool_type=RunnerPoolType.SHARED)
        _create_runner(session, owner_id, pool_type=RunnerPoolType.PLATFORM)

        target_token = f"ahauth_{uuid4().hex}"
        target_hash = bcrypt.hashpw(target_token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        target_runner = Runner(
            name=f"runner-target-{uuid4().hex[:8]}",
            owner_user_id=owner_id,
            token_hash=target_hash,
            token_lookup=hashlib.sha256(target_token.encode("utf-8")).hexdigest(),
            status=RunnerStatus.ONLINE,
            last_heartbeat_at=datetime.utcnow() - timedelta(seconds=5),
            pool_type=RunnerPoolType.PRIVATE,
            is_global=True,
            allowed_repo_ids=[],
        )
        session.add(target_runner)
        session.commit()

    call_counter = {"count": 0}
    original_verify = runner_router._verify_token

    def _counted_verify(token: str, token_hash: str) -> bool:
        call_counter["count"] += 1
        return original_verify(token, token_hash)

    monkeypatch.setattr(runner_router, "_verify_token", _counted_verify)

    res = client.get(
        "/api/v1/runners/poll-jobs",
        headers={"X-Runner-Token": target_token},
    )

    assert res.status_code == 200
    assert call_counter["count"] == 1


def test_poll_jobs_legacy_runner_without_lookup_self_heals(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-legacy-self-heal")
        legacy_token = f"ahauth_{uuid4().hex}"
        legacy_hash = bcrypt.hashpw(legacy_token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        legacy_runner = Runner(
            name=f"runner-legacy-{uuid4().hex[:8]}",
            owner_user_id=owner_id,
            token_hash=legacy_hash,
            token_lookup=None,
            status=RunnerStatus.ONLINE,
            last_heartbeat_at=datetime.utcnow() - timedelta(seconds=5),
            pool_type=RunnerPoolType.PRIVATE,
            is_global=True,
            allowed_repo_ids=[],
        )
        session.add(legacy_runner)
        session.commit()
        session.refresh(legacy_runner)
        legacy_runner_id = legacy_runner.id

    res = client.get(
        "/api/v1/runners/poll-jobs",
        headers={"X-Runner-Token": legacy_token},
    )
    assert res.status_code == 200

    with Session(get_engine()) as session:
        refreshed = session.get(Runner, legacy_runner_id)
        assert refreshed is not None
        assert refreshed.token_lookup == hashlib.sha256(legacy_token.encode("utf-8")).hexdigest()


def test_poll_jobs_rejects_status_banned_runner(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-status-banned")
        banned_token = f"ahauth_{uuid4().hex}"
        banned_hash = bcrypt.hashpw(banned_token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        banned_runner = Runner(
            name=f"runner-status-banned-{uuid4().hex[:8]}",
            owner_user_id=owner_id,
            token_hash=banned_hash,
            token_lookup=hashlib.sha256(banned_token.encode("utf-8")).hexdigest(),
            status=RunnerStatus.BANNED,
            is_banned=False,
            banned_reason="Audit violation",
            last_heartbeat_at=datetime.utcnow() - timedelta(seconds=5),
            pool_type=RunnerPoolType.PRIVATE,
            is_global=True,
            allowed_repo_ids=[],
        )
        session.add(banned_runner)
        session.commit()

    res = client.get(
        "/api/v1/runners/poll-jobs",
        headers={"X-Runner-Token": banned_token},
    )

    assert res.status_code == 403
    assert "Runner banned" in res.json()["detail"]


def test_update_runner_repos_supports_pool_type(client):
    from persistence import get_engine
    from agent_auth.services.user_auth import get_current_user

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-update-pool")
        runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)

    app.dependency_overrides[get_current_user] = lambda: type("MockUser", (), {"id": owner_id})()
    try:
        res = client.put(
            f"/api/v1/runners/{runner_id}/repos",
            json={"allowed_repo_ids": [], "is_global": True, "pool_type": "shared"},
        )
        assert res.status_code == 200
        assert res.json()["pool_type"] == "shared"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_runner_share_grant_crud_requires_owner(client):
    from persistence import get_engine
    from agent_auth.services.user_auth import get_current_user

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-share-crud")
        grantee_id = _create_user(session, "grantee-share-crud")
        other_id = _create_user(session, "other-share-crud")
        runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)

    app.dependency_overrides[get_current_user] = lambda: type("MockUser", (), {"id": other_id})()
    try:
        bad = client.post(
            f"/api/v1/runners/{runner_id}/shares",
            json={"grantee_user_id": str(grantee_id), "can_execute": True},
        )
        assert bad.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    app.dependency_overrides[get_current_user] = lambda: type("MockUser", (), {"id": owner_id})()
    try:
        good = client.post(
            f"/api/v1/runners/{runner_id}/shares",
            json={"grantee_user_id": str(grantee_id), "can_execute": True},
        )
        assert good.status_code == 200

        listed = client.get(
            f"/api/v1/runners/{runner_id}/shares",
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        deleted = client.delete(
            f"/api/v1/runners/{runner_id}/shares/{grantee_id}",
        )
        assert deleted.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_runner_jobs_endpoints_require_active_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-jobs-auth")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(session, requester_user_id=owner_id)

    status_res = client.get(f"/api/v1/runners/jobs/{job_id}")
    assert status_res.status_code == 401

    service_res = client.get(f"/api/v1/runners/jobs/{job_id}/service-status")
    assert service_res.status_code == 401

    endpoint_res = client.get(f"/api/v1/runners/jobs/{job_id}/endpoint")
    assert endpoint_res.status_code == 401

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": owner_id, "role": "contributor"})()
    try:
        authed_status = client.get(f"/api/v1/runners/jobs/{job_id}")
        assert authed_status.status_code == 200

        authed_service = client.get(f"/api/v1/runners/jobs/{job_id}/service-status")
        assert authed_service.status_code == 200
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_runner_job_endpoints_ignore_spoofed_x_user_id_header(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-x-user-id-spoof")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(session, requester_user_id=owner_id)

    spoofed = client.get(
        f"/api/v1/runners/jobs/{job_id}",
        headers={"X-User-Id": str(owner_id)},
    )
    assert spoofed.status_code == 401

    spoofed_service = client.get(
        f"/api/v1/runners/jobs/{job_id}/service-status",
        headers={"X-User-Id": str(owner_id)},
    )
    assert spoofed_service.status_code == 401

    spoofed_endpoint = client.get(
        f"/api/v1/runners/jobs/{job_id}/endpoint",
        headers={"X-User-Id": str(owner_id)},
    )
    assert spoofed_endpoint.status_code == 401

    spoofed_list = client.get(
        "/api/v1/runners/jobs",
        params={"offset": 0},
        headers={"X-User-Id": str(owner_id)},
    )
    assert spoofed_list.status_code == 401


def test_runner_jobs_list_endpoint_requires_active_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-jobs-list-auth")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        _job_id = _create_job(session, requester_user_id=owner_id)

    list_res = client.get("/api/v1/runners/jobs", params={"offset": 0})
    assert list_res.status_code == 401

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": owner_id, "role": "contributor"})()
    try:
        authed_list = client.get("/api/v1/runners/jobs", params={"offset": 0})
        assert authed_list.status_code == 200
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_runner_endpoint_route_requires_active_identity_before_validation(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-endpoint-auth")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(session, requester_user_id=owner_id)

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": owner_id, "role": "contributor"})()
    try:
        authed_endpoint = client.get(f"/api/v1/runners/jobs/{job_id}/endpoint")
        assert authed_endpoint.status_code == 400
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_job_detail_and_service_status_forbid_unrelated_user(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-authz-forbid")
        other_id = _create_user(session, "other-authz-forbid")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(session, requester_user_id=owner_id)

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": other_id, "role": "contributor"})()
    try:
        detail_res = client.get(f"/api/v1/runners/jobs/{job_id}")
        assert detail_res.status_code == 403

        service_res = client.get(f"/api/v1/runners/jobs/{job_id}/service-status")
        assert service_res.status_code == 403

        endpoint_res = client.get(f"/api/v1/runners/jobs/{job_id}/endpoint")
        assert endpoint_res.status_code == 403
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_job_detail_allows_requester_agent_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-agent-requester")
        requester_agent = Agent(
            name=f"agent-{uuid4().hex[:8]}",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix=f"prefix-{uuid4().hex[:6]}",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim",
            claim_expires_at=datetime.utcnow() + timedelta(days=1),
            status="claimed",
            role="contributor",
        )
        session.add(requester_agent)
        session.commit()
        session.refresh(requester_agent)
        requester_agent_id = requester_agent.id

        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(
            session,
            requester_user_id=owner_id,
            requester_agent_id=requester_agent_id,
        )

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": str(requester_agent_id), "status": "claimed"})()
    try:
        detail_res = client.get(f"/api/v1/runners/jobs/{job_id}")
        assert detail_res.status_code == 200
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_job_detail_allows_repo_member_agent_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-repo-member")
        member_agent = Agent(
            name=f"agent-{uuid4().hex[:8]}",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix=f"prefix-{uuid4().hex[:6]}",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim",
            claim_expires_at=datetime.utcnow() + timedelta(days=1),
            status="claimed",
            role="contributor",
        )
        session.add(member_agent)
        session.commit()
        session.refresh(member_agent)
        member_agent_id = member_agent.id

        repo = Repo(
            full_name=f"owner/repo-{uuid4().hex[:8]}",
            name="repo",
            owner="owner",
            created_by_user_id=owner_id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

        membership = RepoMember(
            repo_id=repo.id,
            agent_id=member_agent_id,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        session.commit()

        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job_id = _create_job(
            session,
            requester_user_id=owner_id,
            repo_id=repo.id,
        )

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": str(member_agent_id), "status": "claimed"})()
    try:
        detail_res = client.get(f"/api/v1/runners/jobs/{job_id}")
        assert detail_res.status_code == 200
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_service_status_hides_access_token_even_when_ready(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-hide-token")
        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job = ComputeJob(
            bounty_id=f"bounty-{uuid4().hex[:8]}",
            execution_mode=ExecutionMode.SELF_HOSTED,
            test_command="pytest",
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
            requester_user_id=owner_id,
            service_endpoint="https://example.test/service",
            access_token="super-secret-token",
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": owner_id, "role": "contributor"})()
    try:
        res = client.get(f"/api/v1/runners/jobs/{job_id}/service-status")
        assert res.status_code == 200
        data = res.json()
        assert data["is_ready_for_testing"] is True
        assert data["service_endpoint"] == "https://example.test/service"
        assert data["access_token"] is None
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_job_endpoints_allow_repo_member_user_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-repo-member-user")
        member_user_id = _create_user(session, "member-repo-member-user")
        member_agent = Agent(
            name=f"agent-{uuid4().hex[:8]}",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix=f"prefix-{uuid4().hex[:6]}",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim",
            claim_expires_at=datetime.utcnow() + timedelta(days=1),
            status="claimed",
            role="contributor",
        )
        session.add(member_agent)
        session.commit()
        session.refresh(member_agent)

        binding = UserAgentBinding(
            user_id=member_user_id,
            agent_id=member_agent.id,
        )
        session.add(binding)

        repo = Repo(
            full_name=f"owner/repo-user-member-{uuid4().hex[:8]}",
            name="repo",
            owner="owner",
            created_by_user_id=owner_id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

        membership = RepoMember(
            repo_id=repo.id,
            agent_id=member_agent.id,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)

        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job = ComputeJob(
            bounty_id=f"bounty-{uuid4().hex[:8]}",
            execution_mode=ExecutionMode.SELF_HOSTED,
            test_command="pytest",
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
            requester_user_id=owner_id,
            repo_id=repo.id,
            service_endpoint="https://example.test/member-service",
            access_token="member-secret-token",
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": member_user_id, "role": "user"})()
    try:
        detail_res = client.get(f"/api/v1/runners/jobs/{job_id}")
        assert detail_res.status_code == 200

        service_res = client.get(f"/api/v1/runners/jobs/{job_id}/service-status")
        assert service_res.status_code == 200
        assert service_res.json()["access_token"] is None

        endpoint_res = client.get(f"/api/v1/runners/jobs/{job_id}/endpoint")
        assert endpoint_res.status_code == 403
    finally:
        app.dependency_overrides.pop(require_active_identity, None)


def test_job_endpoint_allows_repo_tester_user_identity(client):
    from persistence import get_engine

    with Session(get_engine()) as session:
        owner_id = _create_user(session, "owner-repo-member-tester-user")
        tester_user_id = _create_user(session, "tester-repo-member-user")
        tester_agent = Agent(
            name=f"agent-{uuid4().hex[:8]}",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix=f"prefix-{uuid4().hex[:6]}",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim",
            claim_expires_at=datetime.utcnow() + timedelta(days=1),
            status="claimed",
            role="tester",
        )
        session.add(tester_agent)
        session.commit()
        session.refresh(tester_agent)

        binding = UserAgentBinding(
            user_id=tester_user_id,
            agent_id=tester_agent.id,
        )
        session.add(binding)

        repo = Repo(
            full_name=f"owner/repo-tester-user-{uuid4().hex[:8]}",
            name="repo",
            owner="owner",
            created_by_user_id=owner_id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

        membership = RepoMember(
            repo_id=repo.id,
            agent_id=tester_agent.id,
            role=RepoRole.BLACKBOX_TESTER,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)

        _runner_id, _ = _create_runner(session, owner_id, pool_type=RunnerPoolType.PRIVATE)
        job = ComputeJob(
            bounty_id=f"bounty-{uuid4().hex[:8]}",
            execution_mode=ExecutionMode.SELF_HOSTED,
            test_command="pytest",
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
            requester_user_id=owner_id,
            repo_id=repo.id,
            service_endpoint="https://example.test/member-service",
            access_token="member-secret-token",
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    app.dependency_overrides[require_active_identity] = lambda: type("MockIdentity", (), {"id": tester_user_id, "role": "user"})()
    try:
        endpoint_res = client.get(f"/api/v1/runners/jobs/{job_id}/endpoint")
        assert endpoint_res.status_code == 200
        endpoint_body = endpoint_res.json()
        assert endpoint_body["service_endpoint"] == "https://example.test/member-service"
        assert endpoint_body["access_token"] == "member-secret-token"
    finally:
        app.dependency_overrides.pop(require_active_identity, None)
