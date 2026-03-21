import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from persistence import Bounty, BountyStatus, CommitRecord
from agent_auth.models import Agent
from agent_auth.utils import get_api_key_prefix
from core.security import STORE_ROOT



def _init_repo_with_one_commit(bare_path: str) -> None:
    tmp = tempfile.mkdtemp(prefix="e2e_seed_")
    try:
        subprocess.run(["git", "init", tmp], check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "e2e@test.local"], cwd=tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "e2e"], cwd=tmp, check=True, capture_output=True, text=True)
        Path(tmp, "README.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp, check=True, capture_output=True, text=True)

        os.makedirs(bare_path, exist_ok=True)
        subprocess.run(["git", "init", "--bare", bare_path], check=True, capture_output=True, text=True)
        subprocess.run(["git", "remote", "add", "origin", bare_path], cwd=tmp, check=True, capture_output=True, text=True)
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=tmp,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(["git", "push", "origin", current_branch], cwd=tmp, check=True, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_e2e_claim_submit_blackbox_revert_flow(client: TestClient, db_engine, auth_headers):
    repo_name = "repo-e2e"
    bare_path = os.path.abspath(os.path.join(STORE_ROOT, repo_name))
    if os.path.exists(bare_path):
        shutil.rmtree(bare_path, ignore_errors=True)

    try:
        _init_repo_with_one_commit(bare_path)

        with Session(db_engine) as s:
            # Build a real claimed agent and align request agent_id with authenticated principal
            api_key = auth_headers["X-API-Key"]
            api_key_prefix = get_api_key_prefix(api_key)
            agent = s.exec(select(Agent).where(Agent.api_key_prefix == api_key_prefix)).first()
            assert agent is not None
            agent_id = str(agent.id)

            bounty = Bounty(
                title="E2E",
                description="",
                reward=1,
                status=BountyStatus.IN_PROGRESS.value,
                repo_name=repo_name,
                required_role="contributor",
                assignee=agent_id,
                test_command="pytest",
                verification_mode="human",
            )
            s.add(bounty)
            s.commit()
            s.refresh(bounty)
            bounty_id = bounty.id

        req = {
            "agent_id": agent_id,
            "model_name": "test-model",
            "files": {"README.md": "Hello from e2e\n"},
            "intent_description": "impl",
            "intent_category": "feat",
            "diff_summary": "add README",
            "reasoning_trace": ["submit via authenticated agent"],
            "bounty_id": bounty_id,
        }
        commit_res = client.post(f"/api/v1/repos/{repo_name}/commit", json=req, headers=auth_headers)
        assert commit_res.status_code == 200, commit_res.text

        with Session(db_engine) as s:
            rec = s.exec(
                select(CommitRecord)
                .where(CommitRecord.repo_name == repo_name)
                .order_by(CommitRecord.id.desc())
            ).first()
            assert rec is not None
            commit_id = rec.id

        # Without identity, blackbox endpoint must be protected
        no_auth_res = client.post(
            f"/api/v1/commits/{commit_id}/blackbox-test",
            json={
                "endpoint": "/api",
                "results": [{"method": "GET", "api_path": "/", "passed": False}],
                "overall_verdict": "FAIL",
            },
        )
        assert no_auth_res.status_code == 401
    finally:
        shutil.rmtree(bare_path, ignore_errors=True)
