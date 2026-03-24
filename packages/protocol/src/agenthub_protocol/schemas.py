from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TRACE_COMMIT_PROTOCOL_VERSION = "1.0"
SUPPORTED_TRACE_COMMIT_PROTOCOL_VERSIONS = {TRACE_COMMIT_PROTOCOL_VERSION}

TRACE_COMMIT_MAX_AGENT_ID_LENGTH = 128
TRACE_COMMIT_MAX_MODEL_NAME_LENGTH = 128
TRACE_COMMIT_MAX_OWNER_ID_LENGTH = 128
TRACE_COMMIT_MAX_INTENT_DESCRIPTION_LENGTH = 2000
TRACE_COMMIT_MAX_INTENT_CATEGORY_LENGTH = 64
TRACE_COMMIT_MAX_MODEL_VERSION_LENGTH = 128
TRACE_COMMIT_MAX_INTENT_VECTOR_DIMS = 4096
TRACE_COMMIT_MAX_DIFF_SUMMARY_LENGTH = 512
TRACE_COMMIT_MAX_REASONING_STEPS = 50
TRACE_COMMIT_MAX_REASONING_STEP_LENGTH = 2000
TRACE_COMMIT_MAX_REJECTED_ALTERNATIVES = 20
TRACE_COMMIT_MAX_REJECTED_ALTERNATIVE_LENGTH = 1000
TRACE_COMMIT_MAX_CONTEXT_FILE_PATHS = 500
TRACE_COMMIT_MAX_FILE_PATH_LENGTH = 1024
TRACE_COMMIT_MAX_LIBRARY_VERSIONS = 200

FilePath = Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_FILE_PATH_LENGTH)]
ReasoningStep = Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_REASONING_STEP_LENGTH)]
RejectedAlternative = Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_REJECTED_ALTERNATIVE_LENGTH)]

# --- Core Primitives ---

class AgentIdentity(BaseModel):
    """Identity of the Agent performing the action."""

    agent_id: Annotated[
        str,
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_AGENT_ID_LENGTH, description="Unique UUID or hash of the agent instance"),
    ]
    model_name: Annotated[
        str,
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_MODEL_NAME_LENGTH, description="e.g. gpt-4-0613, claude-3-opus"),
    ]
    owner_id: Optional[Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_OWNER_ID_LENGTH, description="Human or Org owner ID")]] = None
    reputation_score: float = 0.0


class ContextSnapshot(BaseModel):
    """Snapshot of what the Agent 'knew' when making the change."""

    file_paths: Annotated[
        List[FilePath],
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_CONTEXT_FILE_PATHS),
    ]
    doc_references: Annotated[List[Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_FILE_PATH_LENGTH)]], Field(max_length=200)] = Field(default_factory=list)
    env_vars_accessed: Annotated[List[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=200)] = Field(default_factory=list)
    library_versions: Dict[str, str] = Field(default_factory=dict, max_length=TRACE_COMMIT_MAX_LIBRARY_VERSIONS)


class IntentVector(BaseModel):
    """Semantic vector representation of the change intent."""

    description: Annotated[
        str,
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_INTENT_DESCRIPTION_LENGTH, description="Natural language intent description for generation"),
    ]
    category: Optional[Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_INTENT_CATEGORY_LENGTH, description="Intent category, e.g. feature/fix/refactor")]] = None
    vector: Annotated[
        List[float],
        Field(
            min_length=1,
            max_length=TRACE_COMMIT_MAX_INTENT_VECTOR_DIMS,
            description="Embedding vector (e.g. 1536 dim)",
        ),
    ]
    model_version: Annotated[str, Field(min_length=1, max_length=TRACE_COMMIT_MAX_MODEL_VERSION_LENGTH)] = "openai/text-embedding-3-small"

# --- Main Protocols ---

class TraceCommit(BaseModel):
    """
    The 'Why' behind the code.
    Replaces the traditional Git Commit Message with a structured thought trace.
    """

    protocol_version: str = Field(default=TRACE_COMMIT_PROTOCOL_VERSION, description="TraceCommit protocol version")
    commit_sha: Optional[str] = None
    parent_sha: Optional[str] = None

    # Integrity anchors
    tree_hash: Optional[str] = None
    diff_hash: Optional[str] = None
    reasoning_hash: Optional[str] = None
    binding_hash: Optional[str] = None
    test_log_hash: Optional[str] = None
    artifact_hash: Optional[str] = None

    # The 'What'
    diff_summary: Annotated[
        str,
        Field(min_length=10, max_length=TRACE_COMMIT_MAX_DIFF_SUMMARY_LENGTH, description="Concise summary of code changes"),
    ]

    # The 'Why' (Chain of Thought)
    reasoning_trace: Annotated[
        List[ReasoningStep],
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_REASONING_STEPS, description="Step-by-step logic: Analysis -> Selection -> Implementation"),
    ]
    rejected_alternatives: Annotated[
        List[RejectedAlternative],
        Field(min_length=1, max_length=TRACE_COMMIT_MAX_REJECTED_ALTERNATIVES, description="Approaches considered but discarded"),
    ]

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
