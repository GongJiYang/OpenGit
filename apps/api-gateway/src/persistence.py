import os
from datetime import datetime
from typing import List, Optional
from uuid import uuid4
from sqlmodel import Field, SQLModel, create_engine, Session, select, JSON, Column, StaticPool
from sqlalchemy import event

# --- Constants ---
DB_PATH = os.path.abspath("./agenthub_data/agenthub.db")
SQLITE_URL = f"sqlite:///{DB_PATH}"

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

# [Blind-Spot 4] SQLite WAL mode and StaticPool for FastAPI concurrency
engine = create_engine(
    SQLITE_URL, 
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

def create_db_and_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
