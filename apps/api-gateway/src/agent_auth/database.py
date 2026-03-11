"""
Database initialization for Agent Authentication module.

Run this script to initialize the database and create tables.
"""

import os
from sqlmodel import SQLModel, create_engine, Session, StaticPool
from sqlalchemy import event

from .models import Agent

# Singleton Engine
_engine = None

def get_database_url() -> str:
    """Get the database URL from environment or use default."""
    return os.getenv("DATABASE_URL", "sqlite:///./agenthub_data/agents.db")


def get_engine():
    """Returns the singleton database engine with optimizations."""
    global _engine
    if _engine is None:
        db_url = get_database_url()
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
            
    return _engine


def create_db_and_tables(engine=None):
    """
    Create database and all tables.
    """
    if engine is None:
        engine = get_engine()
        # Ensure data directory exists
        db_url = get_database_url()
        if "sqlite:///" in db_url:
            db_path = db_url.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

    SQLModel.metadata.create_all(engine)
    print(f"[DB] Tables created successfully")
    return engine


def get_db():
    """FastAPI dependency for database session."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


if __name__ == "__main__":
    # Run this script directly to initialize database
    print("Initializing AgentHub Agent Authentication database...")
    engine = create_db_and_tables()
    print("Database initialization complete!")
