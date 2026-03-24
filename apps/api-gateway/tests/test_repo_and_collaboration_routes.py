import os
import sys
from uuid import uuid4

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

# Ensure src import path for direct module imports
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from core.settings import clear_settings_cache  # noqa: E402
from agent_auth.models import Agent, AgentStatus, EmailVerification, AgentMetrics  # noqa: E402
from agent_auth.models.platform import User, Repo, RepoMember  # noqa: E402
from agent_auth.models.runner import ComputeJob, ComputeJobStatus  # noqa: E402
from agent_auth.services.repo_service import RepoService  # noqa: E402
from agent_auth.utils import hash_api_key, get_api_key_prefix, API_KEY_PREFIX, API_KEY_LENGTH  # noqa: E402


@pytest.fixture()
def _set_required_security_env(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret")
    monkeypatch.setenv("WECHAT_TOKEN", "test-wechat-token")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-github-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-github-client-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    clear_settings_cache()


@pytest.fixture()
def claimed_agent_with_api_key(db_engine):
    raw_api_key = API_KEY_PREFIX + ("a" * API_KEY_LENGTH)
    with Session(db_engine) as session:
        agent = Agent(
            name="repo-collab-agent",
            model_name="test-model",
            api_key_hash=hash_api_key(raw_api_key),
            api_key_prefix=get_api_key_prefix(raw_api_key),
            claim_code="TESTME",
            claim_url="/api/v1/agents/claim/TESTME",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        yield agent, raw_api_key


@pytest.fixture()
def repo_with_job(db_engine):
    with Session(db_engine) as session:
        full_name = f"owner/repo-route-test-{uuid4().hex[:8]}"
        service = RepoService(session)
        repo = service.get_or_create_repo(full_name=full_name)

        job = ComputeJob(
            bounty_id="bounty-for-repo-route-test",
            repo_id=repo.id,
            status=ComputeJobStatus.PENDING,
            test_command="pytest",
        )
        session.add(job)
        session.commit()

        yield repo, job


def test_get_repo_supports_uuid_and_full_name(client, _set_required_security_env, repo_with_job):
    repo, _ = repo_with_job

    by_uuid = client.get(f"/api/v1/repos/{repo.id}")
    assert by_uuid.status_code == 200
    assert by_uuid.json()["id"] == str(repo.id)

    by_full_name = client.get(f"/api/v1/repos/{repo.full_name}")
    assert by_full_name.status_code == 200
    assert by_full_name.json()["full_name"] == repo.full_name


def test_get_repo_jobs_supports_uuid_and_full_name(client, _set_required_security_env, repo_with_job):
    repo, job = repo_with_job

    by_uuid = client.get(f"/api/v1/repos/{repo.id}/jobs")
    assert by_uuid.status_code == 200
    payload_by_id = by_uuid.json()
    assert isinstance(payload_by_id, list)
    assert any(entry["id"] == str(job.id) for entry in payload_by_id)

    by_full_name = client.get(f"/api/v1/repos/{repo.full_name}/jobs")
    assert by_full_name.status_code == 200
    payload_by_name = by_full_name.json()
    assert isinstance(payload_by_name, list)
    assert any(entry["id"] == str(job.id) for entry in payload_by_name)


def test_reviewer_me_requires_valid_api_key(_set_required_security_env, client):
    no_key = client.get("/api/v1/collaboration/reviews/reviewer/me")
    assert no_key.status_code == 401

    bad_key = client.get(
        "/api/v1/collaboration/reviews/reviewer/me",
        headers={"X-API-Key": "ahapi_invalid_key"},
    )
    assert bad_key.status_code == 401


def test_reviewer_me_resolves_agent_from_api_key(_set_required_security_env, client, claimed_agent_with_api_key):
    agent, raw_api_key = claimed_agent_with_api_key

    response = client.get(
        "/api/v1/collaboration/reviews/reviewer/me",
        headers={"X-API-Key": raw_api_key},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewer_id"] == str(agent.id)
    assert isinstance(payload["reviews"], list)


def test_model_table_indexes_declared_on_target_models():
    expected_model_level_indexes = {
        Agent: {"ix_agents_status", "ix_agents_api_key_prefix", "ix_agents_owner_github_id"},
        EmailVerification: {"ix_email_verifications_agent_id"},
        AgentMetrics: {"ix_agent_metrics_reliability_tier"},
        User: set(),
        Repo: set(),
        RepoMember: set(),
    }

    expected_table_indexes = {
        Agent: {
            "ix_agents_status",
            "ix_agents_api_key_prefix",
            "ix_agents_owner_github_id",
            "ix_agents_claim_code",
        },
        EmailVerification: {
            "ix_email_verifications_token",
            "ix_email_verifications_email",
            "ix_email_verifications_agent_id",
        },
        AgentMetrics: {"ix_agent_metrics_agent_id", "ix_agent_metrics_reliability_tier"},
        User: {"ix_users_email", "ix_users_github_id", "ix_users_wechat_openid"},
        Repo: {"ix_repos_full_name", "ix_repos_github_repo_id", "ix_repos_created_by_user_id"},
        RepoMember: {"ix_repo_members_repo_id", "ix_repo_members_agent_id"},
    }

    for model, expected_indexes in expected_model_level_indexes.items():
        table_args = getattr(model, "__table_args__", ())
        indexes = {arg.name for arg in table_args if hasattr(arg, "name")}
        assert expected_indexes.issubset(indexes)

    for model, expected_indexes in expected_table_indexes.items():
        actual_indexes = {index.name for index in model.__table__.indexes}
        assert expected_indexes.issubset(actual_indexes)
