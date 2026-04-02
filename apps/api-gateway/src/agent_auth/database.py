"""
Database initialization for Agent Authentication module.

Run this script to initialize the database and create tables.
"""

import os
from sqlmodel import SQLModel, create_engine, Session, StaticPool
from sqlalchemy import event


# Singleton Engine
_engine = None
CREATE_ALL_ENV_FLAG = "ALLOW_SQLMODEL_CREATE_ALL"
DEFAULT_SQLITE_URL = "sqlite:///./agenthub_data/agenthub.db"


def get_database_url() -> str:
    """Get the database URL with single-database enforcement."""
    database_url = os.getenv("DATABASE_URL")
    auth_database_url = os.getenv("AUTH_DATABASE_URL")

    if database_url and auth_database_url and database_url != auth_database_url:
        raise RuntimeError(
            "AUTH_DATABASE_URL and DATABASE_URL must match (single-database mode)"
        )

    return database_url or auth_database_url or DEFAULT_SQLITE_URL


def get_engine():
    """Returns the singleton database engine with optimizations."""
    global _engine
    if _engine is None:
        db_url = get_database_url()
        if db_url.startswith("sqlite:///"):
            # [Blind-Spot 4] SQLite WAL mode and StaticPool for FastAPI concurrency
            _engine = create_engine(
                db_url,
                echo=False,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool
            )

            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        else:
            _engine = create_engine(db_url, echo=False)
    return _engine


def create_db_and_tables(engine=None):
    """
    Create database and all tables.
    """
    if os.getenv(CREATE_ALL_ENV_FLAG) != "1":
        raise RuntimeError(
            "create_db_and_tables is disabled in runtime path. "
            f"Use Alembic migrations instead; set {CREATE_ALL_ENV_FLAG}=1 only for tests/bootstrap."
        )

    if engine is None:
        engine = get_engine()
        # Ensure data directory exists
        db_url = get_database_url()
        if "sqlite:///" in db_url:
            db_path = db_url.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

    SQLModel.metadata.create_all(engine)
    print("[DB] Tables created successfully")
    return engine


def get_db():
    """FastAPI dependency for database session."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


if __name__ == "__main__":
    print("Runtime direct create_all is disabled. Use Alembic migrations instead.")
    print(f"If you really need local bootstrap/testing, rerun with {CREATE_ALL_ENV_FLAG}=1.")
