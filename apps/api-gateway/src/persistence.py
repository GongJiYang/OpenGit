import os
from datetime import datetime
from typing import List, Optional
from uuid import uuid4
from sqlmodel import Field, SQLModel, create_engine, Session, select, JSON, Column, StaticPool
from sqlalchemy import event

# --- Constants ---
DB_PATH = os.path.abspath("./agenthub_data/agenthub.db")
DEFAULT_SQLITE_URL = f"sqlite:///{DB_PATH}"

def get_database_url() -> str:
    """Get database URL from env or fallback to SQLite."""
    return os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)

# --- Models ---

class Bounty(SQLModel, table=True):
    """Bounty (Job Market) Model."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str = Field(index=True)
    description: str
    reward: int
    status: str = Field(default="open", index=True) # open, claimed, completed
    repo_name: str = Field(index=True)
    required_role: str # architect, contributor, executor
    assignee: Optional[str] = Field(default=None, index=True)
    parent_id: Optional[str] = Field(default=None, index=True, description="Parent bounty ID for decomposition")
    
    # Cost & Risk Control
    max_steps: int = Field(default=15, description="Max allowed submission attempts")
    current_steps: int = Field(default=0)
    
    # Store lists as JSON in SQLite
    context_files: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    target_files: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    acceptance_criteria: Optional[str] = None
    test_command: str = Field(default="pytest", description="Command to run for verification")
    verification_mode: str = Field(default="auto", description="auto | human | external")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"onupdate": datetime.utcnow})

class CommitRecord(SQLModel, table=True):
    """Historical record of TraceCommits submitted via API."""
    id: Optional[int] = Field(default=None, primary_key=True)
    repo_name: str = Field(index=True)
    commit_sha: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    bounty_id: Optional[str] = Field(default=None, index=True)
    branch_name: Optional[str] = Field(default=None, index=True)
    
    # Review Status (Human-in-the-loop)
    status: str = Field(default="pending", index=True) # pending, approved, rejected
    
    model_name: str
    intent_category: str
    intent_description: str
    diff_summary: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Verification Results
    verification_exit_code: Optional[int] = None
    verification_stdout: Optional[str] = None
    
    # Full TraceCommit JSON for deep inspection
    trace_json: dict = Field(sa_column=Column(JSON))

# --- Database Setup & Optimization ---

_engine = None

def get_engine():
    """Singleton engine with SQLite optimizations when applicable."""
    global _engine
    if _engine is not None:
        return _engine
    db_url = get_database_url()
    if db_url.startswith("sqlite:///"):
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

def create_db_and_tables():
    db_url = get_database_url()
    if db_url.startswith("sqlite:///"):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    SQLModel.metadata.create_all(get_engine())

def get_session():
    with Session(get_engine()) as session:
        yield session
