import os
from datetime import datetime
from typing import List, Optional
from uuid import uuid4
from sqlmodel import Field, SQLModel, create_engine, Session, select, JSON, Column, StaticPool
from sqlalchemy import event, Text
from enum import Enum

# --- Constants ---
DB_PATH = os.path.abspath("./agenthub_data/agenthub.db")
DEFAULT_SQLITE_URL = f"sqlite:///{DB_PATH}"

def get_database_url() -> str:
    """Get database URL from env or fallback to SQLite."""
    return os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)

# --- Enums ---

class LockMode(str, Enum):
    """仓库锁定模式"""
    NONE = "none"              # 无锁定
    BRANCH = "branch"          # 分支保护
    PATH = "path"              # 路径锁定
    FREEZE = "freeze"          # 仓库冻结
    COMBINED = "combined"      # 组合模式

class PermissionLevel(str, Enum):
    """权限级别"""
    OWNER = "owner"            # 最高权限
    ADMIN = "admin"            # 管理员
    WRITE = "write"            # 写权限
    READ = "read"              # 只读
    NONE = "none"              # 无权限


class PRStatus(str, Enum):
    """PR 状态"""
    OPEN = "open"              # 待审批
    APPROVED = "approved"      # 已审批，待合并
    MERGED = "merged"          # 已合并
    DEPLOYED = "deployed"      # 已部署
    CLOSED = "closed"          # 已关闭


class UpdateStatus(str, Enum):
    """平台更新状态"""
    PENDING = "pending"        # 待同步
    SYNCING = "syncing"        # 同步中
    DEPLOYED = "deployed"      # 已部署
    FAILED = "failed"          # 失败
    ROLLED_BACK = "rolled_back"  # 已回滚

# --- Models ---

class RepoConfig(SQLModel, table=True):
    """仓库配置与权限模型"""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str = Field(unique=True, index=True, description="仓库名称")
    owner_id: str = Field(index=True, description="拥有者 Agent ID")

    # 锁定配置
    lock_mode: str = Field(default=LockMode.NONE.value, description="锁定模式")
    locked_branches: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="受保护分支")
    locked_paths: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="锁定路径")

    # 权限配置
    default_permission: str = Field(default=PermissionLevel.WRITE.value, description="默认权限")
    collaborators: dict = Field(default_factory=dict, sa_column=Column(JSON), description="协作者权限 {agent_id: permission}")

    # 仓库设置
    is_public: bool = Field(default=True, description="是否公开")
    allow_force_push: bool = Field(default=False, description="允许强制推送")
    require_trace_commit: bool = Field(default=True, description="必须 TraceCommit 协议")

    # 分支保护规则
    branch_protection: dict = Field(default_factory=dict, sa_column=Column(JSON), description="分支保护规则")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"onupdate": datetime.utcnow})


class AuditLog(SQLModel, table=True):
    """操作审计日志"""
    id: Optional[int] = Field(default=None, primary_key=True)
    repo_name: str = Field(index=True)
    agent_id: str = Field(index=True)
    action: str = Field(description="操作类型: push/lock/unlock/revert/delete")
    target: str = Field(description="操作目标: branch/path/commit")
    detail: dict = Field(sa_column=Column(JSON), description="操作详情")
    ip_address: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class Bounty(SQLModel, table=True):
    """Bounty (Job Market) Model."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str = Field(index=True)
    description: str
    reward: int
    status: str = Field(default="open", index=True) # open, in_progress, submitted, completed
    repo_name: str = Field(index=True)
    repo_id: Optional[str] = Field(default=None, index=True, description="Linked Repo ID for membership check")
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


# === Meta-Repository Models ===

class MetaRepoConfig(SQLModel, table=True):
    """
    Meta-repository configuration for self-hosting platform code.

    The meta-repo (agenthub-platform.git) contains the platform's own source code
    and enables PR-driven collective optimization.
    """
    __tablename__ = "meta_repo_config"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    repo_name: str = Field(default="agenthub-platform.git", unique=True, description="Bare repo name")

    # Deployment Configuration
    deploy_root: str = Field(description="Absolute path to running platform root")
    current_commit: Optional[str] = Field(default=None, description="Currently deployed commit SHA")
    last_deploy_at: Optional[datetime] = Field(default=None)

    # Hot-Reload Settings
    hot_reload_enabled: bool = Field(default=True, description="Enable automatic hot-reload after merge")
    auto_restart_services: List[str] = Field(
        default_factory=lambda: ["api-gateway", "observer-ui"],
        sa_column=Column(JSON),
        description="Services to auto-restart on changes"
    )

    # Security Settings
    require_approval_count: int = Field(default=2, description="Minimum approvals for protected paths")
    require_human_approval: bool = Field(default=True, description="Require at least one human approval")
    protected_paths: List[str] = Field(
        default_factory=lambda: ["infra/*", "services/git-core/*", ".github/workflows/*", "apps/api-gateway/src/meta/*"],
        sa_column=Column(JSON),
        description="Paths requiring elevated approval"
    )

    # Fork/PR Workflow
    allow_direct_push: bool = Field(default=False, description="Allow direct push to main (not recommended)")
    require_fork_workflow: bool = Field(default=True, description="Require fork + PR workflow")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"onupdate": datetime.utcnow})


class MetaRepoFork(SQLModel, table=True):
    """
    Fork of the meta-repository created by an agent or human.
    """
    __tablename__ = "meta_repo_forks"

    id: Optional[int] = Field(default=None, primary_key=True)
    fork_name: str = Field(unique=True, index=True, description="Fork repo name (e.g., agenthub-platform-{agent_id}.git)")
    owner_type: str = Field(index=True, description="'agent' or 'human'")
    owner_id: str = Field(index=True, description="Agent ID or user ID")

    # Fork metadata
    source_commit: str = Field(description="Commit SHA when forked")
    last_sync_commit: Optional[str] = Field(default=None, description="Last synced commit from upstream")

    status: str = Field(default="active", index=True)  # active, archived, deleted

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"onupdate": datetime.utcnow})


class PlatformPR(SQLModel, table=True):
    """
    Pull Request for the meta-repository.

    Supports both human and AI Agent authors with security annotations
    for protected paths.
    """
    __tablename__ = "platform_prs"

    id: Optional[int] = Field(default=None, primary_key=True)

    # PR Identification
    pr_number: int = Field(unique=True, index=True, description="Sequential PR number")
    title: str = Field(max_length=255)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Source/Target
    source_branch: str = Field(description="Branch name in source repo")
    source_repo: str = Field(description="Fork repo name or 'main' for direct")
    target_branch: str = Field(default="main")

    # Author
    author_type: str = Field(index=True, description="'agent' or 'human'")
    author_id: str = Field(index=True, description="Agent ID or user ID")
    author_github_login: Optional[str] = Field(default=None, max_length=100)

    # Review Status
    status: str = Field(default=PRStatus.OPEN.value, index=True)
    # open -> approved -> merged -> deployed | closed

    # Security Annotations
    touches_protected_paths: bool = Field(default=False)
    requires_elevated_review: bool = Field(default=False)

    # Approval Tracking
    approvals: List[dict] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description='[{"reviewer_id": "...", "reviewer_type": "agent|human", "approved_at": "..."}]'
    )
    approval_count: int = Field(default=0)
    required_approval_count: int = Field(default=1)

    # CI/CD
    verification_passed: Optional[bool] = Field(default=None)
    verification_log: Optional[str] = Field(default=None)

    # Deployment Link
    update_id: Optional[int] = Field(default=None, description="Linked PlatformUpdate after merge")

    # Merge Info
    merge_commit_sha: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    merged_at: Optional[datetime] = Field(default=None)
    deployed_at: Optional[datetime] = Field(default=None)


class PlatformUpdate(SQLModel, table=True):
    """
    Tracks platform self-update operations triggered by PR merges.

    Records the entire lifecycle: sync -> verify -> deploy -> (rollback if needed)
    """
    __tablename__ = "platform_updates"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Source
    source_pr_id: Optional[int] = Field(default=None, index=True, description="PR that triggered this update")
    source_pr_number: Optional[int] = Field(default=None, index=True)
    source_commit_sha: str = Field(index=True)
    source_branch: str

    # Deployment Status
    status: str = Field(default=UpdateStatus.PENDING.value, index=True)
    # pending -> syncing -> verifying -> deployed | failed | rolled_back

    # File Changes
    files_changed: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    files_synced: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    files_failed: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Verification
    pre_deploy_health: Optional[str] = Field(default=None, sa_column=Column(Text))
    post_deploy_health: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Rollback Info
    previous_commit_sha: Optional[str] = Field(default=None, index=True)
    rollback_available: bool = Field(default=False)

    # Metadata
    triggered_by: str = Field(description="'agent:{id}' or 'human:{id}'")
    deploy_log: Optional[str] = Field(default=None, sa_column=Column(Text))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)


class PlatformAuditLog(SQLModel, table=True):
    """
    Detailed audit log for meta-repo operations.

    Provides full traceability for security-sensitive operations.
    """
    __tablename__ = "platform_audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)

    event_type: str = Field(index=True)
    # pr_created, pr_approved, pr_merged, pr_rejected
    # sync_started, sync_completed, sync_failed
    # service_restarted, rollback_triggered, config_changed

    actor_type: str = Field(index=True, description="'agent' or 'human'")
    actor_id: str = Field(index=True)

    target_type: str = Field(index=True, description="'pr', 'update', 'service', 'config', 'file'")
    target_id: str = Field(index=True)

    details: dict = Field(sa_column=Column(JSON))
    ip_address: Optional[str] = Field(default=None, max_length=45)

    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


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
