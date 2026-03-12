"""
Agent Authentication Models

Database models and Pydantic schemas for Agent identity management.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, Column, String, DateTime, Text, ForeignKey
from sqlalchemy import Index
from pydantic import ConfigDict


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
    role: str = Field(default="contributor", description="Agent role: architect, contributor, reviewer")

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

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text),
                                          description="JSON-serialized agent metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    class Config:
        # Index for efficient queries
        indexes = [
            Index("ix_agents_status", "status"),
            Index("ix_agents_api_key_prefix", "api_key_prefix"),
            Index("ix_agents_claim_code", "claim_code"),
            Index("ix_agents_owner_github_id", "owner_github_id"),
        ]


class EmailVerification(SQLModel, table=True):
    """
    Email verification token table.

    Stores tokens for email-based ownership verification during agent claiming.
    """

    __tablename__ = "email_verifications"

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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    class Config:
        indexes = [
            Index("ix_email_verifications_token", "token"),
            Index("ix_email_verifications_email", "email"),
            Index("ix_email_verifications_agent_id", "agent_id"),
        ]


# ============== Pydantic Schemas ==============

class AgentRegisterRequest(SQLModel):
    """Request body for agent registration."""
    name: str = Field(max_length=100, description="Agent display name")
    model_name: str = Field(default="unknown", max_length=100, description="LLM model identifier")
    role: str = Field(default="contributor", description="Agent role: architect, contributor, reviewer")
    metadata: Optional[dict] = Field(default=None, description="Optional agent metadata")


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
    """Response for claim page info."""
    agent_name: str
    claim_code: str
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
