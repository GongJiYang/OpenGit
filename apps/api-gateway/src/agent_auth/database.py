"""
Database initialization for Agent Authentication module.

Run this script to initialize the database and create tables.
"""

import os
from sqlmodel import SQLModel, create_engine

from .models import Agent


def get_database_url() -> str:
    """Get the database URL from environment or use default."""
    return os.getenv("DATABASE_URL", "sqlite:///./agenthub_data/agents.db")


def create_db_and_tables(engine=None):
    """
    Create database and all tables.

    Args:
        engine: Optional SQLModel engine. If None, creates default.
    """
    if engine is None:
        db_url = get_database_url()

        # Ensure data directory exists
        if "sqlite:///" in db_url:
            db_path = db_url.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

        engine = create_engine(db_url, echo=False)

    SQLModel.metadata.create_all(engine)
    print(f"[DB] Tables created successfully")

    return engine


def get_session_factory(engine=None):
    """
    Get a session factory for dependency injection.

    Args:
        engine: Optional SQLModel engine

    Returns:
        Generator that yields Session
    """
    from sqlmodel import Session

    if engine is None:
        engine = create_engine(get_database_url(), echo=False)

    def session_factory():
        with Session(engine) as session:
            yield session

    return session_factory


def get_db():
    """FastAPI dependency for database session."""
    engine = create_engine(get_database_url(), echo=False)
    from sqlmodel import Session
    with Session(engine) as session:
        yield session


if __name__ == "__main__":
    # Run this script directly to initialize database
    print("Initializing AgentHub Agent Authentication database...")
    engine = create_db_and_tables()
    print("Database initialization complete!")
