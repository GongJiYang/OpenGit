import os
import sys

import pytest

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

import persistence  # noqa: E402
from agent_auth import database as auth_db  # noqa: E402


def test_agent_auth_database_url_rejects_mismatch(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/main.db")
    monkeypatch.setenv("AUTH_DATABASE_URL", "sqlite:////tmp/auth.db")

    with pytest.raises(RuntimeError, match="AUTH_DATABASE_URL and DATABASE_URL must match"):
        auth_db.get_database_url()


def test_agent_auth_database_url_accepts_single_url(monkeypatch):
    monkeypatch.delenv("AUTH_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/agenthub.db")

    assert auth_db.get_database_url() == "sqlite:////tmp/agenthub.db"


def test_persistence_create_all_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_SQLMODEL_CREATE_ALL", raising=False)
    with pytest.raises(RuntimeError, match="create_db_and_tables is disabled in runtime path"):
        persistence.create_db_and_tables()


def test_agent_auth_create_all_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_SQLMODEL_CREATE_ALL", raising=False)
    with pytest.raises(RuntimeError, match="create_db_and_tables is disabled in runtime path"):
        auth_db.create_db_and_tables()
