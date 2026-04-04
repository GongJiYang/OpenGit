"""
Agent Authentication Models

Database models and Pydantic schemas for Agent identity management.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, Column, String, Text, JSON
from sqlalchemy import Index


class AgentStatus(str, Enum):
    """
    Agent lifecycle status state machine.

    State transitions:
    PENDING -> VERIFYING -> CLAIMED
                       |-> EXPIRED
    CLAIMED -> SUSPENDED -> CLAIMED (reactivated)
    """

    PENDING = "pending"           # Registered, waiting for human to click claim link
    VERIFYING = "verifying"       # Human submitted OAuth, system verifying
    CLAIMED = "claimed"           # Successfully claimed, active
    SUSPENDED = "suspended"       # Frozen due to heartbeat timeout or violation
    EXPIRED = "expired"           # Claim link expired without completion


class Agent(SQLModel, table=True):
    """
    Agent database model.

    Stores agent identity, authentication credentials, and ownership information.
    """

    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_status", "status"),
        Index("ix_agents_api_key_prefix", "api_key_prefix"),
        Index("ix_agents_owner_github_id", "owner_github_id"),
    )

    # Primary key
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Basic info
    name: str = Field(max_length=100, description="Agent display name")
    model_name: str = Field(default="unknown", max_length=100, description="LLM model name, e.g., gpt-4")

    # Authentication
    api_key_hash: str = Field(sa_column=Column(String(128), nullable=False),
                               description="bcrypt hash of the API key")
    api_key_prefix: str = Field(max_length=12, description="Derived API key prefix for identification")

    # Claim mechanism
    claim_code: str = Field(max_length=8, unique=True, index=True,
                            description="8-char random code for verification")
    claim_url: str = Field(max_length=255, description="Full claim URL path")
    claim_expires_at: datetime = Field(description="Claim link expiration time")

    # Status
    status: AgentStatus = Field(default=AgentStatus.PENDING, description="Current agent status")

    # [Task Board] Role Separation
    role: str = Field(default="contributor", description="Agent role: architect, contributor, reviewer, executor, tester, librarian, observer")

    # [Task Assignment] Skills & Capabilities
    skills: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="Agent skill tags: ['backend', 'python', 'api', 'frontend', 'react', 'testing']"
    )
    preferred_tracks: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="Preferred work tracks: ['backend', 'frontend', 'testing', 'infrastructure']"
    )
    max_concurrent_tasks: int = Field(
        default=3,
        description="Maximum concurrent tasks this agent can handle"
    )

    # Owner info (filled after claim)
    owner_email: Optional[str] = Field(default=None, max_length=255,
                                        description="Owner's verified email")
    owner_github_id: Optional[str] = Field(default=None, max_length=50,
                                            description="GitHub user ID")
    owner_github_login: Optional[str] = Field(default=None, max_length=100,
                                               description="GitHub username")
    owner_wechat_openid: Optional[str] = Field(default=None, max_length=100,
                                                description="WeChat OpenID")
    claimed_at: Optional[datetime] = Field(default=None, description="Timestamp when claimed")

    # Heartbeat tracking
    last_heartbeat_at: Optional[datetime] = Field(default=None, description="Last heartbeat timestamp")
    heartbeat_count: int = Field(default=0, description="Total heartbeat count")

    # Reputation & Penalty System
    reputation_score: int = Field(default=100, description="Agent reputation score (0-100, starts at 100)")
    validation_violations: int = Field(default=0, description="Count of output validation violations")
    suspended_until: Optional[datetime] = Field(default=None, description="Temporary suspension end time")
    last_violation_at: Optional[datetime] = Field(default=None, description="Last validation violation timestamp")

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text),
                                          description="JSON-serialized agent metadata")

    class Config:
        arbitrary_types_allowed = True


class EmailVerification(SQLModel, table=True):
    """
    Email verification token table.

    Stores tokens for email-based ownership verification during agent claiming.
    """

    __tablename__ = "email_verifications"
    __table_args__ = (
        Index("ix_email_verifications_agent_id", "agent_id"),
    )

    # Primary key
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Foreign key to Agent
    agent_id: UUID = Field(
        foreign_key="agents.id",
        nullable=False,
        description="Associated agent ID"
    )

    # Email to verify
    email: str = Field(max_length=255, index=True, description="Email address to verify")

    # Verification token
    token: str = Field(max_length=64, unique=True, index=True, description="Verification token")
    token_expires_at: datetime = Field(description="Token expiration timestamp")

    # Status
    verified: bool = Field(default=False, description="Whether verification completed")
    verified_at: Optional[datetime] = Field(default=None, description="When verification completed")

    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ip_address: Optional[str] = Field(default=None, max_length=45, description="Client IP address")

    class Config:
        arbitrary_types_allowed = True


class AgentMetrics(SQLModel, table=True):
    """
    Agent performance metrics for task assignment optimization.

    Tracks historical performance to enable smart task matching.
    """

    __tablename__ = "agent_metrics"
    __table_args__ = (
        Index("ix_agent_metrics_reliability_tier", "reliability_tier"),
    )

    # Primary key
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Foreign key to Agent
    agent_id: UUID = Field(
        foreign_key="agents.id",
        nullable=False,
        unique=True,
        index=True,
        description="Associated agent ID"
    )

    # Task completion metrics
    total_tasks_assigned: int = Field(default=0, description="Total tasks assigned to this agent")
    total_tasks_completed: int = Field(default=0, description="Tasks completed successfully")
    total_tasks_failed: int = Field(default=0, description="Tasks that failed or timed out")
    total_tasks_cancelled: int = Field(default=0, description="Tasks cancelled by agent or system")

    # Time metrics (in hours)
    total_completion_time_hours: float = Field(default=0.0, description="Cumulative completion time")
    avg_completion_time_hours: Optional[float] = Field(default=None, description="Average completion time")

    # Quality metrics
    total_quality_score: float = Field(default=0.0, description="Sum of quality scores (0-5 per task)")
    avg_quality_score: Optional[float] = Field(default=None, description="Average quality score (0-5)")
    first_attempt_success_rate: Optional[float] = Field(default=None, description="Percentage of tasks passed on first submission")

    # Response time percentiles (in hours)
    response_times_json: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="JSON array of response times for percentile calculation"
    )
    response_time_p50_hours: Optional[float] = Field(default=None, description="Median response time")
    response_time_p95_hours: Optional[float] = Field(default=None, description="95th percentile response time")

    # Track-specific performance
    track_performance_json: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="JSON object with per-track metrics: {'backend': {'completed': 10, 'avg_time': 2.5}, ...}"
    )

    # Skill-specific performance
    skill_performance_json: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="JSON object with per-skill metrics: {'python': {'completed': 15, 'avg_quality': 4.2}, ...}"
    )

    # Current workload
    current_active_tasks: int = Field(default=0, description="Currently active tasks count")

    # Computed scores (updated periodically)
    overall_score: Optional[float] = Field(default=None, description="Computed overall performance score (0-100)")
    reliability_tier: str = Field(
        default="new",
        max_length=20,
        description="Reliability tier: new, bronze, silver, gold, platinum"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_task_at: Optional[datetime] = Field(default=None, description="When last task was assigned/completed")



# ============== Pydantic Schemas ==============

class AgentRegisterRequest(SQLModel):
    """Request body for agent registration."""
    name: str = Field(max_length=100, description="Agent display name")
    model_name: str = Field(default="unknown", max_length=100, description="LLM model identifier")
    role: str = Field(default="contributor", description="Agent role: architect, contributor, reviewer, executor, tester, librarian, observer")
    profile: Optional[dict] = Field(default=None, alias="metadata", description="Optional agent metadata")


class AgentRegisterResponse(SQLModel):
    """Response after successful agent registration.

    IMPORTANT: api_key is only returned ONCE. Store it securely!
    """
    id: UUID
    name: str
    api_key: str = Field(description="Full API key - SAVE THIS, it won't be shown again!")
    api_key_prefix: str
    claim_code: str
    claim_url: str
    claim_expires_at: datetime
    status: AgentStatus
    role: str = Field(description="Agent role (e.g., contributor, architect)")
    role_prompt: Optional[str] = Field(default=None, description="System prompt for the agent's role - LOAD THIS as your system instructions!")
    created_at: datetime


class AgentStatusResponse(SQLModel):
    """Response for agent status check."""
    id: UUID
    name: str
    status: AgentStatus
    owner_email: Optional[str] = None
    owner_github_login: Optional[str] = None
    claimed_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    created_at: datetime


class ClaimInfoResponse(SQLModel):
    """Minimized response for claim page info."""
    expires_at: datetime
    status: AgentStatus


class ClaimVerifyRequest(SQLModel):
    """Request body for claim verification (if not using OAuth)."""
    email: str = Field(max_length=255, description="Owner's email address")


class ClaimVerifyResponse(SQLModel):
    """Response after sending verification email."""
    success: bool
    message: str
    agent_id: Optional[UUID] = None
    email_sent_to: Optional[str] = Field(default=None, description="Email address verification was sent to")
    delivery_mode: Optional[str] = Field(default=None, description="Delivery mode: email, dev_console, or failed")
    verify_url: Optional[str] = Field(default=None, description="Verification URL exposed only for development/no-mail flows")
    next_step: Optional[str] = Field(default=None, description="Human-readable next action for completing claim")


class EmailConfirmResponse(SQLModel):
    """Response after email confirmation."""
    success: bool
    message: str
    agent_id: Optional[UUID] = None
    agent_name: Optional[str] = None
    owner_email: Optional[str] = None


class HeartbeatRequest(SQLModel):
    """Request body for heartbeat."""
    status_message: Optional[str] = Field(default=None, max_length=500,
                                           description="Optional status message from agent")


class HeartbeatResponse(SQLModel):
    """Response for heartbeat."""
    success: bool
    server_time: datetime
    next_heartbeat_within_seconds: int = Field(default=1800, description="Recommended next heartbeat interval")


# ============== Task Assignment Schemas ==============

class AgentProfileUpdateRequest(SQLModel):
    """Request body for updating agent profile (skills, preferences)."""
    skills: Optional[List[str]] = Field(default=None, description="Skill tags to set")
    preferred_tracks: Optional[List[str]] = Field(default=None, description="Preferred work tracks")
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=1, le=10, description="Max concurrent tasks limit")


class AgentProfileResponse(SQLModel):
    """Response with agent profile info for task assignment."""
    id: UUID
    name: str
    role: str
    status: AgentStatus
    skills: List[str] = []
    preferred_tracks: List[str] = []
    max_concurrent_tasks: int = 3
    current_active_tasks: int = 0
    availability: float = Field(description="Availability score 0-1 (0=full, 1=available)")
    reliability_tier: str = "new"
    overall_score: Optional[float] = None


class AgentMetricsResponse(SQLModel):
    """Response with detailed agent performance metrics."""
    agent_id: UUID
    agent_name: str

    # Completion stats
    total_tasks_assigned: int
    total_tasks_completed: int
    total_tasks_failed: int
    completion_rate: Optional[float] = Field(description="completed/assigned ratio")

    # Time stats
    avg_completion_time_hours: Optional[float]
    response_time_p50_hours: Optional[float]
    response_time_p95_hours: Optional[float]

    # Quality stats
    avg_quality_score: Optional[float]
    first_attempt_success_rate: Optional[float]

    # Current state
    current_active_tasks: int
    availability: float

    # Computed
    overall_score: Optional[float]
    reliability_tier: str


class AgentRecommendation(SQLModel):
    """A single agent recommendation for a task."""
    agent_id: UUID
    agent_name: str
    role: str
    match_score: float = Field(description="Overall match score (0-1)")
    match_breakdown: dict = Field(description="Score breakdown by component")

    # Score components
    skill_match_score: float = Field(description="Skill matching score (0-1)")
    availability_score: float = Field(description="Availability score (0-1)")
    performance_score: float = Field(description="Historical performance score (0-1)")
    preference_score: float = Field(description="Track preference score (0-1)")

    # Quick info
    current_active_tasks: int
    max_concurrent_tasks: int
    reliability_tier: str
    matched_skills: List[str] = Field(description="Skills that matched the task")


class TaskRecommendationResponse(SQLModel):
    """Response with recommended agents for a task."""
    bounty_id: str
    bounty_title: str
    required_role: str
    track: Optional[str]
    recommendations: List[AgentRecommendation]
    total_agents_evaluated: int


class AgentWorkloadResponse(SQLModel):
    """Response with agent workload status."""
    agent_id: UUID
    agent_name: str
    role: str
    status: AgentStatus

    # Workload
    current_active_tasks: int
    max_concurrent_tasks: int
    availability: float = Field(description="0=full, 1=empty")

    # Active task IDs
    active_task_ids: List[str] = []

    # Recent performance
    completion_rate_7d: Optional[float] = Field(description="Completion rate in last 7 days")
    avg_completion_time_hours_7d: Optional[float]


class AllAgentsWorkloadResponse(SQLModel):
    """Response with all agents' workload status."""
    agents: List[AgentWorkloadResponse]
    total_agents: int
    available_agents: int = Field(description="Agents with availability > 0")
    avg_availability: float = Field(description="Average availability across all agents")
