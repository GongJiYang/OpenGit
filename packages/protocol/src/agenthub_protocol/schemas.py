from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime
import uuid

# --- Enums & Constants ---

class ChangeType(BaseModel):
    """The nature of the change."""
    value: Literal["fix", "feat", "refactor", "perf", "test", "chore", "evolution"]

# --- Core Primitives ---

class AgentIdentity(BaseModel):
    """Identity of the Agent performing the action."""
    agent_id: str = Field(..., description="Unique UUID or hash of the agent instance")
    model_name: str = Field(..., description="e.g. gpt-4-0613, claude-3-opus")
    owner_id: Optional[str] = Field(None, description="Human or Org owner ID")
    reputation_score: float = 0.0

class ContextSnapshot(BaseModel):
    """Snapshot of what the Agent 'knew' when making the change."""
    file_paths: List[str]
    doc_references: List[str] = []
    env_vars_accessed: List[str] = []
    library_versions: Dict[str, str] = {}

class IntentVector(BaseModel):
    """Semantic vector representation of the change intent."""
    description: str = Field(..., description="Natural language intent description for generation")
    vector: List[float] = Field(default_factory=list, description="Embedding vector (e.g. 1536 dim)")
    model_version: str = "openai/text-embedding-3-small"

# --- Main Protocols ---

class TraceCommit(BaseModel):
    """
    The 'Why' behind the code.
    Replaces the traditional Git Commit Message with a structured thought trace.
    """
    commit_sha: Optional[str] = None
    parent_sha: Optional[str] = None
    
    # The 'What'
    diff_summary: str = Field(..., description="Concise summary of code changes")
    
    # The 'Why' (Chain of Thought)
    reasoning_trace: List[str] = Field(..., description="Step-by-step logic: Analysis -> Selection -> Implementation")
    rejected_alternatives: List[str] = Field(..., description="Approaches considered but discarded")
    
    # The 'Context'
    context_snapshot: ContextSnapshot
    intent: IntentVector
    
    # Metadata
    author: AgentIdentity
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "diff_summary": "Fixed deadlock in async pool by adding timeout",
                "reasoning_trace": [
                    "Observed infinite hang in logs",
                    "Traced to `await pool.acquire()`",
                    "Verified no timeout parameter was set",
                    "Added `timeout=5.0` to prevent indefinite blocking"
                ],
                "rejected_alternatives": ["Increasing pool size (doesn't fix root cause)"]
            }
        }

class PullRequestSpec(BaseModel):
    """
    Formal Specification for an Agent Pull Request.
    """
    title: str
    type: Literal["fix", "feat", "refactor", "perf", "test", "evolution"]
    
    # Linking
    target_branch: str = "main"
    source_branch: str
    issue_ids: List[str] = []
    
    # Verification
    tests_added: bool = Field(..., description="Must be True for 'fix' and 'feat'")
    test_command: str = "pytest tests/integration/test_login.py"
    verification_hash: Optional[str] = Field(None, description="Hash of the local test run log")
    
    # Trace
    commits: List[TraceCommit]
    
    # Financial/Legal (Smart Contract Slots)
    bounty_claim_id: Optional[str] = None
    royalty_recipient: Optional[str] = None
    license_compatibility: str = "MIT"

