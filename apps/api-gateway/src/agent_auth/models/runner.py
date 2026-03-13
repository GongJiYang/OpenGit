"""
Runner Models - Self-Hosted Compute Network

Implements the distributed CI/CD compute network where community members
can contribute their servers as test runners.

Architecture:
- User generates a RunnerToken in UI
- User runs `agenthub-runner start --token=xxx` on their server
- Runner polls platform for jobs via reverse long-polling
- Platform verifies results with Zero-Trust model (logs + random audits)

Trust Model:
- Mandatory full log upload (stdout, stderr, exit code)
- Random audit: every 10th job is re-run on official E2B sandbox
- Mismatch = permanent ban + reputation penalty
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, Column, String, DateTime, Text, Integer, Boolean, JSON
from sqlalchemy import Index


# ============== Enums ==============

class RunnerStatus(str, Enum):
    """Runner node status."""
    ONLINE = "online"        # Heartbeat received, ready for jobs
    OFFLINE = "offline"      # No heartbeat for 60+ seconds
    BUSY = "busy"            # Currently executing a job
    DISABLED = "disabled"    # Manually disabled by owner
    BANNED = "banned"        # Permanently banned for cheating


class ComputeJobStatus(str, Enum):
    """Compute job status."""
    PENDING = "pending"      # Waiting for available runner
    ASSIGNED = "assigned"    # Assigned to a runner, not started yet
    RUNNING = "running"      # Currently executing
    COMPLETED = "completed"  # Successfully completed
    FAILED = "failed"        # Execution failed
    TIMEOUT = "timeout"      # Runner didn't respond in time
    AUDIT_FAILED = "audit_failed"  # Random audit detected cheating


class ExecutionMode(str, Enum):
    """Repository execution mode for CI/CD."""
    E2B_SANDBOX = "e2b_sandbox"      # Official E2B sandbox (paid, most secure)
    SHARED_LOCAL = "shared_local"    # Platform shared servers (free, queued)
    YOLO_MODE = "yolo_mode"          # Skip testing, direct human review
    SELF_HOSTED = "self_hosted"      # Community/self-hosted runners


class AuditResult(str, Enum):
    """Audit result for Zero-Trust verification."""
    PASSED = "passed"          # Runner output matches audit
    FAILED = "failed"          # Runner lied about results (cheating)
    SUSPICIOUS = "suspicious"  # Some mismatch but not definitive


# ============== Runner Node Model ==============

class Runner(SQLModel, table=True):
    """
    Self-hosted runner node.

    A runner is a server that connects to the platform to execute
    compute jobs (mainly CI/CD tests for Bounty submissions).

    Connection Pattern:
    - Runner polls platform every 5 seconds (reverse long-polling)
    - Platform assigns jobs through poll response
    - Runner executes in local Docker container
    - Runner posts results back to platform
    """

    __tablename__ = "runners"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Identity
    name: str = Field(max_length=100, description="Runner display name, e.g., 'Ubuntu-Node-X'")
    owner_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True,
                                 description="User who owns this runner")

    # Authentication
    token_hash: str = Field(max_length=128, description="bcrypt hash of runner token")

    # Status
    status: RunnerStatus = Field(default=RunnerStatus.OFFLINE, index=True)

    # Heartbeat
    last_heartbeat_at: Optional[datetime] = Field(default=None, description="Last heartbeat timestamp")
    heartbeat_ip: Optional[str] = Field(default=None, max_length=45, description="IP address from heartbeat")

    # Specs (reported by runner)
    cpu_cores: Optional[int] = Field(default=None, description="Number of CPU cores")
    memory_gb: Optional[int] = Field(default=None, description="Memory in GB")
    os_type: Optional[str] = Field(default=None, max_length=50, description="OS type: linux, darwin, windows")
    os_version: Optional[str] = Field(default=None, max_length=100, description="OS version")
    docker_version: Optional[str] = Field(default=None, max_length=50, description="Docker version")
    labels: List[str] = Field(default_factory=list, sa_column=Column(JSON),
                               description="Custom labels for job matching, e.g., ['gpu', 'macos']")

    # Metrics
    total_jobs_completed: int = Field(default=0, description="Total jobs completed successfully")
    total_jobs_failed: int = Field(default=0, description="Total jobs failed")
    total_compute_seconds: int = Field(default=0, description="Total compute time in seconds")
    total_earnings: int = Field(default=0, description="Total earnings in platform credits (cents)")

    # Reputation & Trust
    reputation_score: int = Field(default=100, description="Reputation score (0-100)")
    audit_failures: int = Field(default=0, description="Number of failed random audits")
    is_banned: bool = Field(default=False, description="Permanently banned for cheating")
    banned_reason: Optional[str] = Field(default=None, max_length=500, description="Reason for ban")
    banned_at: Optional[datetime] = Field(default=None, description="When banned")

    # Current job
    current_job_id: Optional[UUID] = Field(default=None, description="Currently executing job ID")

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        indexes = [
            Index("ix_runners_status", "status"),
            Index("ix_runners_owner_user_id", "owner_user_id"),
        ]


# ============== Runner Token (Registration) ==============

class RunnerToken(SQLModel, table=True):
    """
    One-time token for registering a new runner.

    Flow:
    1. User clicks "Add Server" in UI
    2. Platform generates RunnerToken (shows token ONCE)
    3. User runs: agenthub-runner start --token=xxx
    4. Runner calls register endpoint with token
    5. Token is marked as used, Runner record created
    """

    __tablename__ = "runner_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)

    # Token (shown only once!)
    token: str = Field(max_length=64, unique=True, index=True,
                        description="Plaintext token (shown once to user)")
    token_hash: str = Field(max_length=128, description="bcrypt hash for verification")

    # Status
    is_used: bool = Field(default=False, index=True)
    used_at: Optional[datetime] = Field(default=None)
    used_by_runner_id: Optional[UUID] = Field(default=None,
                                                description="Runner created with this token")

    # Expiry
    expires_at: datetime = Field(description="Token expiration time")

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_ip: Optional[str] = Field(default=None, max_length=45)

    class Config:
        indexes = [
            Index("ix_runner_tokens_token", "token"),
            Index("ix_runner_tokens_user_id", "user_id"),
        ]


# ============== Compute Job ==============

class ComputeJob(SQLModel, table=True):
    """
    A compute job to be executed by a runner.

    Jobs are created when an Agent submits code for a Bounty.
    The job contains everything needed to run tests in isolation.
    """

    __tablename__ = "compute_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Associated entities
    bounty_id: str = Field(index=True, description="Bounty this job is for")
    repo_id: Optional[UUID] = Field(default=None, index=True, description="Repository ID")
    submission_id: Optional[str] = Field(default=None, description="Submission/Commit record ID")

    # Job assignment
    runner_id: Optional[UUID] = Field(default=None, index=True,
                                       description="Assigned runner (null if pending)")
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SHARED_LOCAL,
                                           description="Where to execute this job")

    # Job definition
    test_command: str = Field(default="pytest", max_length=500,
                               description="Command to run tests, e.g., 'pytest -v'")
    code_url: Optional[str] = Field(default=None, max_length=500,
                                     description="URL to download code (signed, expiring)")
    code_branch: Optional[str] = Field(default=None, max_length=100)
    code_commit: Optional[str] = Field(default=None, max_length=40)
    env_vars: Dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON),
                                      description="Environment variables for test")
    timeout_seconds: int = Field(default=300, description="Job timeout in seconds")

    # Status
    status: ComputeJobStatus = Field(default=ComputeJobStatus.PENDING, index=True)

    # Timing
    assigned_at: Optional[datetime] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    # Results (Zero-Trust: MUST upload full logs)
    exit_code: Optional[int] = Field(default=None, description="Process exit code")
    stdout_log: Optional[str] = Field(default=None, sa_column=Column(Text),
                                       description="Full stdout log (REQUIRED)")
    stderr_log: Optional[str] = Field(default=None, sa_column=Column(Text),
                                       description="Full stderr log")
    test_results: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON),
                                          description="Parsed test results")
    passed: Optional[bool] = Field(default=None, description="Did tests pass?")

    # Audit (Random verification)
    is_audited: bool = Field(default=False, description="Was this job randomly audited?")
    audit_job_id: Optional[UUID] = Field(default=None,
                                          description="Reference job run on E2B for audit")
    audit_result: Optional[str] = Field(default=None, max_length=50,
                                         description="audit_passed / audit_failed / audit_pending")
    audit_mismatch_details: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        indexes = [
            Index("ix_compute_jobs_status", "status"),
            Index("ix_compute_jobs_bounty_id", "bounty_id"),
            Index("ix_compute_jobs_runner_id", "runner_id"),
        ]


# ============== Audit Log ==============

class AuditLog(SQLModel, table=True):
    """
    Audit log for Zero-Trust verification.

    When a job is flagged for audit, it's re-run on trusted infrastructure
    (E2B sandbox) and the results are compared to the runner's submission.
    """

    __tablename__ = "audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Job and runner being audited
    job_id: UUID = Field(foreign_key="compute_jobs.id", index=True)
    runner_id: UUID = Field(foreign_key="runners.id", index=True)

    # Original submission
    original_stdout: Optional[str] = Field(default=None, sa_column=Column(Text))
    original_exit_code: Optional[int] = Field(default=None)
    original_passed: Optional[bool] = Field(default=None)

    # Audited result (from trusted E2B)
    audited_stdout: Optional[str] = Field(default=None, sa_column=Column(Text))
    audited_exit_code: Optional[int] = Field(default=None)

    # Audit result
    status: str = Field(default="pending", description="pending, running, completed")
    result: Optional[AuditResult] = Field(default=None, description="Audit result")
    explanation: Optional[str] = Field(default=None, max_length=1000)

    # Metadata
    reason: str = Field(default="random", max_length=100, description="Why audit was triggered")
    audited_at: Optional[datetime] = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        indexes = [
            Index("ix_audit_logs_job_id", "job_id"),
            Index("ix_audit_logs_runner_id", "runner_id"),
        ]


# ============== Repo Execution Config ==============

class RepoExecutionConfig(SQLModel, table=True):
    """
    Repository's CI/CD execution configuration.

    Defines which execution mode to use for tests.
    """

    __tablename__ = "repo_execution_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    repo_id: UUID = Field(foreign_key="repos.id", unique=True, nullable=False, index=True)

    # Execution mode
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SHARED_LOCAL)

    # Self-hosted settings
    preferred_runner_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON),
                                             description="Preferred runners for this repo")
    sponsor_user_id: Optional[UUID] = Field(default=None,
                                             description="User sponsoring compute (gets 20% of bounty)")

    # E2B settings
    e2b_budget_limit: int = Field(default=1000, description="Max E2B budget in cents")

    # YOLO mode settings
    yolo_require_human_review: bool = Field(default=True,
                                              description="Require human review in YOLO mode")

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Pydantic Schemas ==============

class GenerateTokenResponse(SQLModel):
    """Response when generating a new runner token."""
    token: str = Field(description="ONE-TIME token - SAVE THIS!")
    expires_at: datetime
    command: str = Field(description="Command to run on server")


class RunnerRegisterRequest(SQLModel):
    """Request to register a new runner."""
    token: str
    name: str = Field(max_length=100)
    cpu_cores: Optional[int] = None
    memory_gb: Optional[int] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    docker_version: Optional[str] = None
    labels: List[str] = []


class RunnerHeartbeatRequest(SQLModel):
    """Runner heartbeat request."""
    runner_token: str
    current_job_id: Optional[UUID] = None
    status: RunnerStatus = RunnerStatus.ONLINE


class PollJobsRequest(SQLModel):
    """Runner polls for available jobs."""
    runner_token: str
    max_jobs: int = Field(default=1, description="Max jobs to receive")


class JobAssignment(SQLModel):
    """Job assignment sent to runner."""
    job_id: UUID
    code_url: str
    code_branch: Optional[str] = None
    test_command: str
    env_vars: Dict[str, str] = {}
    timeout_seconds: int = 300


class SubmitResultRequest(SQLModel):
    """Runner submits job results."""
    runner_token: str
    job_id: UUID
    exit_code: int
    stdout_log: str = Field(description="REQUIRED: Full stdout log")
    stderr_log: Optional[str] = None
    test_results: Dict[str, Any] = {}
    passed: bool


class RunnerResponse(SQLModel):
    """Runner info for API responses."""
    id: UUID
    name: str
    status: RunnerStatus
    cpu_cores: Optional[int] = None
    memory_gb: Optional[int] = None
    os_type: Optional[str] = None
    total_jobs_completed: int
    reputation_score: int
    last_heartbeat_at: Optional[datetime] = None


class ComputeJobResponse(SQLModel):
    """Compute job info for API responses."""
    id: UUID
    bounty_id: str
    status: ComputeJobStatus
    execution_mode: ExecutionMode
    passed: Optional[bool] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
