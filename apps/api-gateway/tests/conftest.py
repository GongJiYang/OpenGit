import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure src is importable
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Force isolated test database under project root
TEST_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../agenthub_data/test_agenthub.db"))
os.makedirs(os.path.dirname(TEST_DB_PATH), exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

from persistence import create_db_and_tables, get_engine  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    # Create DB/Tables once per test session
    create_db_and_tables()
    yield


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def db_engine():
    return get_engine()


@pytest.fixture()
def client(monkeypatch):
    # Default governance/security env for tests
    monkeypatch.setenv("SKILLS_ALLOWLIST", "list_templates")
    monkeypatch.setenv("SKILLS_CIRCUIT_BREAKER", "1")
    monkeypatch.setenv("SKILLS_CB_WINDOW", "4")
    monkeypatch.setenv("SKILLS_CB_FAIL_RATE", "0.5")
    monkeypatch.setenv("SKILLS_CB_OPEN_SECS", "5")
    monkeypatch.setenv("SKILLS_REQUEST_TIMEOUT", "5")
    # PII mask default empty
    monkeypatch.delenv("SKILLS_PII_MASK_FIELDS", raising=False)
    with TestClient(app) as c:
        yield c
