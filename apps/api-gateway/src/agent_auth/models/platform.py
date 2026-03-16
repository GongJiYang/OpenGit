"""
Platform Models - User, Repo, Membership, and Permission System

Core architecture for:
- Human user authentication (OAuth/Email)
- User-Agent permanent binding
- Repository membership management
- Role-based permissions (Architect can kick, etc.)
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel
from sqlalchemy import Index, UniqueConstraint


# ============== Enums ==============

class UserRole(str, Enum):
    """Human user roles in the platform."""
    USER = "user"
    ADMIN = "admin"


class RepoRole(str, Enum):
    """Agent roles within a specific repository."""
    ARCHITECT = "architect"      # Full control: kick members, manage bounties
    CONTRIBUTOR = "contributor"  # Can claim and submit bounties
    EXECUTOR = "executor"
    BLACKBOX_TESTER = "tester"  # 定义黑盒测试者
    OBSERVER = "observer"        # Read-only access


class MembershipStatus(str, Enum):
    """Repository membership status."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"


class AuthProvider(str, Enum):
    """Authentication providers for human users."""
    GITHUB = "github"
    EMAIL = "email"
    WECHAT = "wechat"


# ============== User Model ==============

class User(SQLModel, table=True):
    """
    Human user model.

    Supports OAuth (GitHub, WeChat) and Email authentication.
    One user can permanently bind to ONE Agent.
    """

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Auth info
    email: Optional[str] = Field(default=None, max_length=255, index=True,
                                  description="Primary email address")
    email_verified: bool = Field(default=False, description="Email verification status")

    # OAuth profiles
    github_id: Optional[str] = Field(default=None, max_length=50, index=True)
    github_login: Optional[str] = Field(default=None, max_length=100)
    github_avatar: Optional[str] = Field(default=None, max_length=500)

    wechat_openid: Optional[str] = Field(default=None, max_length=100, index=True)
    wechat_nickname: Optional[str] = Field(default=None, max_length=100)

    # Profile
    display_name: Optional[str] = Field(default=None, max_length=100)
    avatar_url: Optional[str] = Field(default=None, max_length=500)

    # Platform role
    role: UserRole = Field(default=UserRole.USER)

    # Password hash (for email auth)
    password_hash: Optional[str] = Field(default=None, max_length=255)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = Field(default=None)

    class Config:
        indexes = [
            Index("ix_users_email", "email"),
            Index("ix_users_github_id", "github_id"),
            Index("ix_users_wechat_openid", "wechat_openid"),
        ]


# ============== User-Agent Binding ==============

class UserAgentBinding(SQLModel, table=True):
    """
    Permanent binding between User and their root Agent.

    Rules:
    - One user can only bind to ONE agent
    - One agent can only be bound by ONE user
    - Binding is permanent (cannot be changed once established)
    """

    __tablename__ = "user_agent_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_binding_user"),
        UniqueConstraint("agent_id", name="uq_binding_agent"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    agent_id: UUID = Field(foreign_key="agents.id", nullable=False, index=True)

    # Binding metadata
    bound_at: datetime = Field(default_factory=datetime.utcnow)
    ip_address: Optional[str] = Field(default=None, max_length=45)

    # Permanent binding - cannot be undone
    is_permanent: bool = Field(default=True)


# ============== Repository Model ==============

class Repo(SQLModel, table=True):
    """
    Repository registry.

    Each repo can have multiple agents with different roles.
    Bounties belong to repos.
    """

    __tablename__ = "repos"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Repository identity
    github_repo_id: Optional[int] = Field(default=None, index=True,
                                           description="GitHub repository ID")
    full_name: str = Field(max_length=255, unique=True, index=True,
                           description="owner/repo format")
    name: str = Field(max_length=100, description="Repository name")
    owner: str = Field(max_length=100, description="Repository owner")

    # Creator (User who registered this repo)
    created_by_user_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True,
                                                description="User who created this repo")

    # Metadata
    description: Optional[str] = Field(default=None, max_length=500)
    is_private: bool = Field(default=False)
    is_active: bool = Field(default=True)

    # Webhook
    webhook_secret: Optional[str] = Field(default=None, max_length=100)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        indexes = [
            Index("ix_repos_full_name", "full_name"),
            Index("ix_repos_github_repo_id", "github_repo_id"),
        ]


# ============== Repository Membership ==============

class RepoMember(SQLModel, table=True):
    """
    Repository membership with role-based permissions.

    An agent must be a member of a repo to claim bounties.
    Architects can kick members with lower roles.
    """

    __tablename__ = "repo_members"
    __table_args__ = (
        UniqueConstraint("repo_id", "agent_id", name="uq_repo_agent"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    repo_id: UUID = Field(foreign_key="repos.id", nullable=False, index=True)
    agent_id: UUID = Field(foreign_key="agents.id", nullable=False, index=True)

    # Role in this repo
    role: RepoRole = Field(default=RepoRole.CONTRIBUTOR)

    # Membership status
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE)

    # Audit
    added_by_agent_id: Optional[UUID] = Field(default=None,
                                               description="Agent who added this member")
    added_at: datetime = Field(default_factory=datetime.utcnow)

    # Kicked info
    kicked_by_agent_id: Optional[UUID] = Field(default=None)
    kicked_at: Optional[datetime] = Field(default=None)
    kick_reason: Optional[str] = Field(default=None, max_length=500)

    class Config:
        indexes = [
            Index("ix_repo_members_repo_id", "repo_id"),
            Index("ix_repo_members_agent_id", "agent_id"),
        ]


# ============== Role Permissions ==============

class RolePermission(SQLModel, table=True):
    """
    Defines what each role can do.

    Permissions are checked via PermissionGuard.
    """

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role", "permission", name="uq_role_permission"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    role: RepoRole = Field(nullable=False, index=True)
    permission: str = Field(max_length=100, nullable=False,
                            description="e.g., 'kick_members', 'claim_bounty'")

    # Resource constraints
    resource_type: Optional[str] = Field(default=None, max_length=50)
    max_count: Optional[int] = Field(default=None,
                                      description="Max operations per time window")

    created_at: datetime = Field(default_factory=datetime.utcnow)


DEFAULT_PERMISSIONS = {
    RepoRole.ARCHITECT: [
        "kick_members",
        "invite_members",
        "manage_bounties",
        "claim_bounty",
        "submit_code",
        "review_submissions",
        "view_analytics",
    ],
    RepoRole.CONTRIBUTOR: [
        "claim_bounty",
        "submit_code",
        "view_analytics",
    ],
    RepoRole.BLACKBOX_TESTER: [
        "run_api_tests",        # 运行接口探测
        "verify_endpoint",      # 验证临时端点
        "view_analytics",
    ],
    RepoRole.OBSERVER: [
        "view_analytics",
    ],
}

# Role hierarchy for kick permission
ROLE_HIERARCHY = {
    RepoRole.ARCHITECT: 100,
    RepoRole.BLACKBOX_TESTER: 50,
    RepoRole.CONTRIBUTOR: 30,
    RepoRole.OBSERVER: 10,
}


# ============== Pydantic Schemas ==============

class UserCreate(SQLModel):
    """User registration request."""
    email: str = Field(max_length=255)
    password: Optional[str] = Field(default=None, max_length=100)


class UserLogin(SQLModel):
    """User login request."""
    email: str = Field(max_length=255)
    password: str = Field(max_length=100)


class UserResponse(SQLModel):
    """User info response."""
    id: UUID
    email: Optional[str] = None
    display_name: Optional[str] = None
    github_login: Optional[str] = None
    avatar_url: Optional[str] = None
    role: UserRole
    created_at: datetime


class TokenResponse(SQLModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(default=3600, description="Seconds until expiration")
    user: UserResponse


class JoinRepoRequest(SQLModel):
    """Request to join a repository."""
    repo_full_name: str = Field(description="Repository in owner/repo format")


class KickMemberRequest(SQLModel):
    """Request to kick a member from repository."""
    reason: Optional[str] = Field(default=None, max_length=500)


class RepoMemberResponse(SQLModel):
    """Repository member info."""
    agent_id: UUID
    agent_name: str
    role: RepoRole
    status: MembershipStatus
    joined_at: datetime


class RepoResponse(SQLModel):
    """Repository info response."""
    id: UUID
    full_name: str
    name: str
    owner: str
    description: Optional[str] = None
    member_count: int = 0
    bounty_count: int = 0
    is_member: bool = False
    your_role: Optional[RepoRole] = None
    is_owner: bool = False
    created_at: datetime = None


class CreateRepoRequest(SQLModel):
    """Request to create a new repository."""
    full_name: str = Field(description="Repository in owner/repo format")
    description: Optional[str] = None
    is_private: bool = False
