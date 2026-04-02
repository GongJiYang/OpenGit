import hashlib
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

import bcrypt
from sqlmodel import Session, select

from core.settings import clear_settings_cache
from core.security import GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL
from agent_auth.models import Agent, AgentStatus
from agent_auth.models.runner import AuditLog, ComputeJob, ComputeJobStatus, ExecutionMode, Runner, RunnerStatus, RunnerToken
from agent_auth.models.platform import Repo, RepoMember, MembershipStatus, RepoRole
from agent_auth.services.verification import VerificationService


def _seed_job_and_audit(
    *,
    test_command: str = "pytest",
    code_commit: Optional[str] = "abc123",
    env_fingerprint: Optional[str] = "A=1|B=2",
) -> str:
    from persistence import get_engine

    with Session(get_engine()) as session:
        job = ComputeJob(
            bounty_id="bounty-test",
            execution_mode=ExecutionMode.SHARED_LOCAL,
            test_command=test_command,
            code_commit=code_commit,
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
            env_vars={"A": "1", "B": "2"} if env_fingerprint else {},
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        audit = AuditLog(
            job_id=job.id,
            runner_id=uuid4(),
            status="pending",
            reason="periodic_audit",
            original_stdout="pytest run ok",
            original_exit_code=0,
            original_test_command=test_command,
            original_code_commit=code_commit,
            original_env_fingerprint=env_fingerprint,
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        return str(audit.id)


def test_runner_register_returns_403_when_governance_enforce(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "enforce")
    clear_settings_cache()

    res = client.post(
        "/api/v1/runners/register",
        json={"token": "bad-token", "name": "runner-under-enforce"},
    )

    assert res.status_code == 403
    assert res.json().get("detail") == GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL


def test_generate_runner_token_persists_hash_and_lookup_without_plaintext(client):
    from persistence import get_engine
    from agent_auth.services.user_auth import get_current_user

    user_id = uuid4()
    app_user = type("MockUser", (), {"id": user_id})()

    from main import app

    app.dependency_overrides[get_current_user] = lambda: app_user
    try:
        res = client.post("/api/v1/runners/generate-token")
        assert res.status_code == 200
        token = res.json()["token"]

        with Session(get_engine()) as session:
            stored = session.exec(
                select(RunnerToken).where(
                    RunnerToken.token_lookup == hashlib.sha256(token.encode("utf-8")).hexdigest()
                )
            ).first()
            assert stored is not None
            assert bcrypt.checkpw(token.encode("utf-8"), stored.token_hash.encode("utf-8"))
            assert getattr(stored, "token_lookup", None) is not None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_internal_audit_pending_requires_internal_token(client):
    res = client.get("/api/v1/runners/internal/audit/pending")
    assert res.status_code == 422


def test_runner_register_uses_hashed_registration_token_lookup(client):
    from persistence import get_engine

    raw_token = f"ahrun_{uuid4().hex}"
    token_hash = bcrypt.hashpw(raw_token.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    owner_id = uuid4()

    with Session(get_engine()) as session:
        row = RunnerToken(
            user_id=owner_id,
            token_lookup=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            token_hash=token_hash,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(row)
        session.commit()

    res = client.post(
        "/api/v1/runners/register",
        json={"token": raw_token, "name": "runner-register-hash"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body.get("auth_token", "").startswith("ahauth_")


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
        json={
            "audit_id": audit_id,
            "audited_stdout": "ok",
            "audited_exit_code": 0,
            "audited_test_command": "pytest",
            "audited_code_commit": "abc123",
            "audited_env_fingerprint": "A=1|B=2",
        },
        headers={"X-Internal-Token": "wrong"},
    )
    assert bad.status_code == 403

    good = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={
            "audit_id": audit_id,
            "audited_stdout": "ok",
            "audited_exit_code": 0,
            "audited_test_command": "pytest",
            "audited_code_commit": "abc123",
            "audited_env_fingerprint": "A=1|B=2",
        },
        headers={"X-Internal-Token": "internal-secret"},
    )
    assert good.status_code == 200
    body = good.json()
    assert body["success"] is True
    assert body["audit_id"] == audit_id


def test_internal_audit_submit_fails_on_test_command_fingerprint_mismatch(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    audit_id = _seed_job_and_audit(test_command="pytest -q")

    res = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={
            "audit_id": audit_id,
            "audited_stdout": "pytest run ok",
            "audited_exit_code": 0,
            "audited_test_command": "pytest -v",
            "audited_code_commit": "abc123",
            "audited_env_fingerprint": "A=1|B=2",
        },
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert res.status_code == 200
    assert res.json()["result"] == "failed"
    assert "test_command differs" in res.json()["explanation"]


def test_internal_audit_submit_fails_on_code_commit_fingerprint_mismatch(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    audit_id = _seed_job_and_audit(code_commit="commit-a")

    res = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={
            "audit_id": audit_id,
            "audited_stdout": "pytest run ok",
            "audited_exit_code": 0,
            "audited_test_command": "pytest",
            "audited_code_commit": "commit-b",
            "audited_env_fingerprint": "A=1|B=2",
        },
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert res.status_code == 200
    assert res.json()["result"] == "failed"
    assert "code_commit differs" in res.json()["explanation"]


def test_internal_audit_submit_fails_on_env_fingerprint_mismatch(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    audit_id = _seed_job_and_audit(env_fingerprint="A=1|B=2")

    res = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={
            "audit_id": audit_id,
            "audited_stdout": "pytest run ok",
            "audited_exit_code": 0,
            "audited_test_command": "pytest",
            "audited_code_commit": "abc123",
            "audited_env_fingerprint": "A=1|B=3",
        },
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert res.status_code == 200
    assert res.json()["result"] == "failed"
    assert "env_fingerprint differs" in res.json()["explanation"]


def test_verification_service_requires_strong_similarity_for_pass():
    result, explanation = VerificationService.execute_audit(
        original_stdout="pytest collected 10 tests\n10 passed in 1.23s\n",
        original_exit_code=0,
        audited_stdout="all good done\n",
        audited_exit_code=0,
        original_test_command="pytest",
        audited_test_command="pytest",
        original_code_commit="abc123",
        audited_code_commit="abc123",
        original_env_fingerprint="A=1|B=2",
        audited_env_fingerprint="A=1|B=2",
    )

    assert result.value == "suspicious"
    assert "similarity is low" in explanation


def test_verification_service_forces_audit_on_historical_audit_failures():
    from persistence import get_engine

    with Session(get_engine()) as session:
        runner = Runner(
            name="runner-risky",
            owner_user_id=uuid4(),
            token_hash="hash",
            audit_failures=2,
            reputation_score=95,
            total_jobs_completed=3,
        )
        session.add(runner)
        session.commit()
        session.refresh(runner)

        job = ComputeJob(
            bounty_id="bounty-1",
            test_command="pytest",
            execution_mode=ExecutionMode.SELF_HOSTED,
            requester_agent_id=None,
            env_vars={},
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        original_random = VerificationService.BASE_RANDOM_AUDIT_PROB
        VerificationService.BASE_RANDOM_AUDIT_PROB = 0.0
        try:
            triggered, reason = VerificationService.should_trigger_audit(
                runner=runner,
                session=session,
                job=job,
            )
        finally:
            VerificationService.BASE_RANDOM_AUDIT_PROB = original_random

        assert triggered is True
        assert reason.startswith("risk_forced:")
        assert "historical_audit_failures" in reason


def test_verification_service_forces_audit_on_sensitive_path_and_privileged_role(monkeypatch):
    from persistence import get_engine

    with Session(get_engine()) as session:
        agent = Agent(
            name="audit-risk-agent",
            model_name="test-model",
            api_key_hash="hash",
            api_key_prefix="pref-risk",
            claim_code=f"TC{uuid4().hex[:6].upper()}",
            claim_url="/claim/risk",
            claim_expires_at=datetime.utcnow(),
            status=AgentStatus.CLAIMED,
            role="architect",
        )
        session.add(agent)

        repo = Repo(
            full_name=f"risk/repo-{uuid4().hex[:8]}",
            name=f"repo-{uuid4().hex[:8]}",
            owner="risk-owner",
        )
        session.add(repo)

        runner = Runner(
            name="runner-sensitive",
            owner_user_id=uuid4(),
            token_hash="hash",
            audit_failures=0,
            reputation_score=90,
            total_jobs_completed=2,
        )
        session.add(runner)
        session.commit()
        session.refresh(agent)
        session.refresh(repo)
        session.refresh(runner)

        membership = RepoMember(
            repo_id=repo.id,
            agent_id=agent.id,
            role=RepoRole.ARCHITECT,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)

        job = ComputeJob(
            bounty_id="bounty-2",
            test_command="pytest",
            execution_mode=ExecutionMode.SELF_HOSTED,
            requester_agent_id=agent.id,
            env_vars={
                "repo_name": repo.name,
                "touched_paths": "infra/docker-compose.yml,apps/api-gateway/src/main.py",
            },
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        monkeypatch.setattr(VerificationService, "BASE_RANDOM_AUDIT_PROB", 0.0)
        triggered, reason = VerificationService.should_trigger_audit(
            runner=runner,
            session=session,
            job=job,
        )

        assert triggered is True
        assert reason.startswith("risk_forced:")
        assert "sensitive_path" in reason
        assert "high_privilege_repo_role" in reason


def test_internal_audit_submit_requires_new_fingerprint_fields(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    clear_settings_cache()
    audit_id = _seed_job_and_audit()

    res = client.post(
        "/api/v1/runners/internal/audit/submit",
        json={"audit_id": audit_id, "audited_stdout": "ok", "audited_exit_code": 0},
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert res.status_code == 422
    detail = res.json().get("detail") or []
    missing_fields = {item.get("loc", [None])[-1] for item in detail if isinstance(item, dict)}
    assert "audited_test_command" in missing_fields


def test_apply_audit_result_sets_is_banned_with_status_banned():
    from persistence import get_engine
    from agent_auth.models.runner import AuditResult

    with Session(get_engine()) as session:
        runner = Runner(
            name="runner-ban-sync",
            owner_user_id=uuid4(),
            token_hash="hash",
            reputation_score=10,
            total_jobs_completed=0,
        )
        session.add(runner)
        session.commit()
        session.refresh(runner)

        job = ComputeJob(
            bounty_id="bounty-ban-sync",
            execution_mode=ExecutionMode.SELF_HOSTED,
            test_command="pytest",
            timeout_seconds=300,
            status=ComputeJobStatus.RUNNING,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        audit = AuditLog(
            job_id=job.id,
            runner_id=runner.id,
            status="pending",
            reason="periodic_audit",
            original_stdout="ok",
            original_exit_code=0,
            original_test_command="pytest",
            original_code_commit="abc123",
            original_env_fingerprint="A=1|B=2",
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)

        VerificationService.apply_audit_result(
            session=session,
            audit=audit,
            result=AuditResult.FAILED,
            explanation="mismatch",
            audited_stdout="bad",
            audited_exit_code=1,
            audited_test_command="pytest",
            audited_code_commit="abc123",
            audited_env_fingerprint="A=1|B=2",
        )

        updated = session.get(Runner, runner.id)
        assert updated is not None
        assert updated.status == RunnerStatus.BANNED
        assert updated.is_banned is True
        assert updated.banned_reason is not None
