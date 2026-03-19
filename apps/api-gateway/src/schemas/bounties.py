from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agenthub_protocol.roles import RepoRole


class CreateBountyRequest(BaseModel):
    # Strict: forbid unknown fields
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    reward: int
    repo_name: str
    repo_id: Optional[str] = None
    required_role: RepoRole
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    test_command: Optional[str] = "pytest"
    verification_mode: Optional[str] = "auto"


class SubTaskDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    reward: int = 0
    required_role: RepoRole = RepoRole.CONTRIBUTOR
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    test_command: str = "pytest"
    verification_mode: str = "auto"


class TaskNode(BaseModel):
    """Nested task structure for hierarchical decomposition."""

    client_id: Optional[str] = Field(default=None, description="Client-provided stable ID used for dependency resolution")
    title: str
    description: str = ""
    reward: int = 0
    required_role: RepoRole = RepoRole.CONTRIBUTOR
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list, description="List of client_ids this depends on")
    children: List["TaskNode"] = Field(default_factory=list, description="Sub-tasks")
    test_command: str = "pytest"
    verification_mode: str = "auto"


TaskNode.model_rebuild()  # Enable recursive model


class DecomposedBountyRequest(BaseModel):
    """Request for creating a hierarchical bounty tree."""

    repo_name: str
    repo_id: Optional[str] = None
    root_task: TaskNode


class DecomposedBountyResponse(BaseModel):
    """Response with all created bounties and their dependencies."""

    total_created: int
    bounties: List[dict]
    dependency_map: dict  # {client_id: bounty_id}


class PreparationClaimRequest(BaseModel):
    """Request for claiming a bounty in preparation mode."""

    agent_id: str
    preparation_notes: Optional[str] = None


class CancelRequest(BaseModel):
    reason: Optional[str] = None
    force: bool = True  # Strict cascade default


class RestoreRequest(BaseModel):
    pass


class BountyDecisionRequest(BaseModel):
    """Request for submitting bounty analysis/decision options."""

    options_json: str = Field(..., description="JSON array of 3-5 options with 'option' and 'reason' fields")


class BountyDecisionResponse(BaseModel):
    """Response for bounty decision submission."""

    success: bool
    is_valid: bool
    error_message: Optional[str] = None
    retry_prompt: Optional[str] = None
    parsed_options: Optional[List[dict]] = None
    reputation_score: Optional[int] = None
    is_suspended: bool = False
