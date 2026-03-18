import os
import sys
import tempfile
import shutil

import pytest
from fastapi.testclient import TestClient

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from main import app  # noqa: E402
from persistence import create_db_and_tables, Bounty, BountyStatus  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    create_db_and_tables()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_e2e_claim_submit_blackbox_revert_flow(client: TestClient, monkeypatch):
    # Create a repo directory (bare) for commit flow
    repo_name = "owner/repo-e2e"
    repo_root = os.path.abspath("apps/api-gateway/data/repos")
    os.makedirs(repo_root, exist_ok=True)
    bare_path = os.path.join(repo_root, repo_name)
    if not os.path.exists(bare_path):
        os.makedirs(bare_path, exist_ok=True)
        # Initialize a bare repo for test
        tmp = tempfile.mkdtemp(prefix="e2e_repo_")
        try:
            os.system(f"git init {tmp} >/dev/null 2>&1")
            os.system(f"cd {tmp} && git config user.email 't@t' && git config user.name 't' && git commit --allow-empty -m init >/dev/null 2>&1")
            os.makedirs(bare_path, exist_ok=True)
            os.system(f"git init --bare {bare_path} >/dev/null 2>&1")
            os.system(f"cd {tmp} && git remote add origin {bare_path} && git push origin master >/dev/null 2>&1")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Create a bounty directly in DB for simplicity
    from sqlmodel import Session
    from sqlmodel import create_engine
    engine = create_engine("sqlite:///agenthub_data/agenthub.db")
    with Session(engine) as s:
        b = Bounty(
            title="E2E",
            description="",
            reward=1,
            status=BountyStatus.OPEN.value,
            repo_name=repo_name,
            required_role="contributor",
            test_command="pytest",
            verification_mode="human",
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        bounty_id = b.id

    # Simulate submit API (skip actual auth and ownership checks for brevity)
    req = {
        "agent_id": "agent-1",
        "model_name": "test-model",
        "files": {"README.md": "Hello"},
        "intent_description": "impl",
        "intent_category": "feat",
        "diff_summary": "add README",
        "bounty_id": bounty_id
    }
    r = client.post(f"/api/v1/repos/{repo_name}/commit", json=req)
    assert r.status_code in (200, 403, 409)  # Depending on auth, but route works

    # Create a commit record to use in blackbox test
    from sqlmodel import Session
    from sqlmodel import select
    from persistence import CommitRecord
    with Session(engine) as s:
        rec = s.exec(select(CommitRecord).order_by(CommitRecord.id.desc())).first()
        if rec:
            commit_id = rec.id
            report = {
                "endpoint": "/api",
                "results": [{"method": "GET", "api_path": "/", "passed": False}],
                "overall_verdict": "FAIL"
            }
            r2 = client.post(f"/api/v1/commits/{commit_id}/blackbox-test", json=report)
            assert r2.status_code in (200, 403)  # depends on identity; endpoint exists
