import json
import os
import secrets
import sys
from datetime import date, datetime, timedelta
from typing import Dict

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session

# Ensure src and repo-root packages are importable
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Force isolated test database under project root
TEST_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../agenthub_data/test_agenthub.db"))
os.makedirs(os.path.dirname(TEST_DB_PATH), exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["ALLOW_SQLMODEL_CREATE_ALL"] = "1"

from core.settings import clear_settings_cache  # noqa: E402
from persistence import create_db_and_tables, get_engine  # noqa: E402
from main import app  # noqa: E402
from core.middleware import limiter  # noqa: E402
from agent_auth.models import Agent, AgentStatus  # noqa: E402
from agent_auth.utils import API_KEY_PREFIX, API_KEY_LENGTH, get_api_key_prefix  # noqa: E402
import routers.commits as commits_router  # noqa: E402
# import skills.api_router as skills_api_router  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    app.dependency_overrides.clear()
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        limiter._storage.reset()

    # Reset in-memory circuit-breaker state between tests to avoid cross-test coupling.
    # skills_api_router._CB_RECENT.clear()
    # skills_api_router._CB_OPEN_UNTIL.clear()

    # Reset daily budget tracker state to avoid cross-test coupling via shared JSON file.
    commits_router.budget_tracker._ensure_file()
    with open(commits_router.budget_tracker.path, "w", encoding="utf-8") as f:
        json.dump({"date": str(date.today()), "spent": 0.0}, f)

    yield

    app.dependency_overrides.clear()
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        limiter._storage.reset()

    # skills_api_router._CB_RECENT.clear()
    # skills_api_router._CB_OPEN_UNTIL.clear()


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    # Create DB/Tables once per test session
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    create_db_and_tables()
    yield


@pytest.fixture(autouse=True)
def _isolate_db_tables(db_engine):
    # Keep schema, clear table rows between tests for deterministic isolation.
    with db_engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        inspector = inspect(conn)
        table_names = inspector.get_table_names()
        for table_name in table_names:
            conn.exec_driver_sql(f'DELETE FROM "{table_name}"')
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def db_engine():
    return get_engine()


def _new_api_key() -> str:
    random_part = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(API_KEY_LENGTH))
    return f"{API_KEY_PREFIX}{random_part}"


def _create_claimed_agent_headers() -> Dict[str, str]:
    api_key = _new_api_key()
    hashed = bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

    with Session(get_engine()) as session:
        agent = Agent(
            name="test-agent",
            model_name="test-model",
            api_key_hash=hashed,
            api_key_prefix=get_api_key_prefix(api_key),
            claim_code=f"TC{secrets.token_hex(3).upper()[:6]}",
            claim_url="/api/v1/agents/claim/test-code",
            claim_expires_at=datetime.utcnow() + timedelta(days=3650),
            status=AgentStatus.CLAIMED,
            role="contributor",
        )
        session.add(agent)
        session.commit()

    return {"X-API-Key": api_key}


@pytest.fixture()
def auth_headers() -> Dict[str, str]:
    return _create_claimed_agent_headers()


@pytest.fixture()
def client(monkeypatch):
    clear_settings_cache()

    # Default governance/security env for tests
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    monkeypatch.setenv("SKILLS_CIRCUIT_BREAKER", "1")
    monkeypatch.setenv("SKILLS_CB_WINDOW", "4")
    monkeypatch.setenv("SKILLS_CB_FAIL_RATE", "0.5")
    monkeypatch.setenv("SKILLS_CB_OPEN_SECS", "5")
    monkeypatch.setenv("SKILLS_REQUEST_TIMEOUT", "5")

    # Security envs required by fail-fast startup validation
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret")
    monkeypatch.setenv("WECHAT_TOKEN", "test-wechat-token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    monkeypatch.setenv("TRACE_COMMIT_SIGNING_SECRET", "test-trace-signing-secret")

    # PII mask default empty
    monkeypatch.delenv("SKILLS_PII_MASK_FIELDS", raising=False)
    clear_settings_cache()
    with TestClient(app) as c:
        yield c
