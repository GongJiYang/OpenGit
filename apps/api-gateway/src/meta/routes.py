"""
Meta-Repository API Routes

API endpoints for managing the self-hosting meta-repository.
"""

import subprocess
import fnmatch
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from persistence import (
    MetaRepoConfig,
    MetaRepoFork,
    PlatformPR,
    PlatformUpdate,
    PlatformAuditLog,
    PRStatus,
    get_session,
)
from agent_auth.services.workitem_service import WorkItemService
from agent_auth.models import Agent
from agent_auth.database import get_db as get_auth_session

meta_router = APIRouter(prefix="/api/v1/meta", tags=["Meta-Repository"])

# === Request Models ===

class MetaRepoInitRequest(BaseModel):
    """Request to initialize the meta-repository."""
    deploy_root: str = Field(description="Absolute path to platform root directory")
    protected_paths: Optional[List[str]] = None
    require_approval_count: int = Field(default=2)
    require_human_approval: bool = Field(default=True)


class CreateForkRequest(BaseModel):
    """Request to create a fork of the meta-repo."""
    fork_name: Optional[str] = Field(default=None, description="Custom fork name")


class CreatePRRequest(BaseModel):
    """Request to create a Pull Request."""
    title: str = Field(max_length=255)
    description: Optional[str] = None
    source_branch: str
    source_repo: str  # Fork repo name


class ApprovePRRequest(BaseModel):
    """Request to approve a PR."""
    comment: Optional[str] = None


# === Dependencies ===

def get_meta_config(session: Session = Depends(get_session)) -> MetaRepoConfig:
    """Get meta-repo configuration, raise 404 if not initialized."""
    config = session.exec(select(MetaRepoConfig)).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Meta-repository not initialized. Call POST /api/v1/meta/init first."
        )
    return config


def require_admin_agent(
    x_api_key: str = Depends(lambda: None),
    auth_session: Session = Depends(get_auth_session)
) -> Agent:
    """Require an agent with admin role."""
    if not x_api_key:
        # Re-extract from header properly
        pass
    # This is a placeholder - should integrate with existing require_agent
    # and check for admin role
    return None  # Will be implemented with proper auth


# === Status Endpoints ===

@meta_router.get("/status")
async def get_meta_status(
    session: Session = Depends(get_session)
):
    """Get meta-repository status and current deployment info."""
    config = session.exec(select(MetaRepoConfig)).first()

    if not config:
        return {
            "initialized": False,
            "message": "Meta-repository not initialized"
        }

    # Get latest update
    latest_update = session.exec(
        select(PlatformUpdate)
        .order_by(PlatformUpdate.created_at.desc())
        .limit(1)
    ).first()

    # Get pending PRs count via adapter（等价实现）
    wis = WorkItemService(session)
    pending_prs = len([p for p in wis.meta.list_prs(status_filter=PRStatus.OPEN.value, limit=1000)])

    return {
        "initialized": True,
        "repo_name": config.repo_name,
        "deploy_root": config.deploy_root,
        "current_commit": config.current_commit,
        "last_deploy_at": config.last_deploy_at,
        "hot_reload_enabled": config.hot_reload_enabled,
        "latest_update": {
            "id": latest_update.id,
            "status": latest_update.status,
            "source_pr_number": latest_update.source_pr_number,
        } if latest_update else None,
        "pending_prs": pending_prs,
    }


# === Initialization ===

@meta_router.post("/init")
async def init_meta_repo(
    request: MetaRepoInitRequest,
    session: Session = Depends(get_session),
    # agent: Agent = Depends(require_admin_agent)  # TODO: Enable after auth integration
):
    """
    Initialize the agenthub-platform meta-repository.

    This creates a bare git repo from the current platform code and
    sets up the configuration for self-hosting.

    Only callable by agents with 'admin' role.
    """
    # Check if already initialized
    existing = session.exec(select(MetaRepoConfig)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Meta-repository already initialized"
        )

    # Validate deploy_root
    deploy_root = Path(request.deploy_root).resolve()
    if not deploy_root.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deploy root does not exist: {deploy_root}"
        )

    if not (deploy_root / ".git").exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deploy root must be a git repository"
        )

    # Create bare repo path
    repos_dir = Path("./agenthub_data/repos").resolve()
    repos_dir.mkdir(parents=True, exist_ok=True)
    bare_repo_path = repos_dir / "agenthub-platform.git"

    try:
        # Clone as bare repo
        if bare_repo_path.exists():
            # Update existing
            subprocess.run(
                ["git", "fetch", "--all"],
                cwd=str(bare_repo_path),
                check=True,
                capture_output=True
            )
        else:
            subprocess.run(
                ["git", "clone", "--bare", str(deploy_root), str(bare_repo_path)],
                check=True,
                capture_output=True
            )

        # Get current commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(deploy_root),
            capture_output=True,
            text=True
        )
        current_commit = result.stdout.strip() if result.returncode == 0 else None

        # Create config
        config = MetaRepoConfig(
            repo_name="agenthub-platform.git",
            deploy_root=str(deploy_root),
            current_commit=current_commit,
            protected_paths=request.protected_paths or [
                "infra/*",
                "services/git-core/*",
                ".github/workflows/*",
                "apps/api-gateway/src/meta/*"
            ],
            require_approval_count=request.require_approval_count,
            require_human_approval=request.require_human_approval,
        )
        session.add(config)

        # Create audit log
        audit = PlatformAuditLog(
            event_type="meta_repo_initialized",
            actor_type="human",  # TODO: Get from auth
            actor_id="admin",
            target_type="config",
            target_id=config.id,
            details={
                "deploy_root": str(deploy_root),
                "current_commit": current_commit,
            }
        )
        session.add(audit)
        session.commit()

        return {
            "success": True,
            "repo_name": config.repo_name,
            "deploy_root": config.deploy_root,
            "current_commit": current_commit,
            "message": "Meta-repository initialized successfully"
        }

    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Git operation failed: {e.stderr.decode() if e.stderr else str(e)}"
        )


# === Fork Management ===

@meta_router.post("/forks")
async def create_fork(
    request: CreateForkRequest,
    meta_config: MetaRepoConfig = Depends(get_meta_config),
    session: Session = Depends(get_session),
    # agent: Agent = Depends(require_agent)  # TODO: Enable after auth integration
):
    """
    Create a fork of the meta-repository for PR workflow.
    """
    # TODO: Get agent info from auth
    owner_type = "agent"
    owner_id = "test-agent"  # Placeholder

    # Generate fork name
    if request.fork_name:
        fork_name = request.fork_name
        if not fork_name.endswith(".git"):
            fork_name += ".git"
    else:
        fork_name = f"agenthub-platform-{owner_id[:8]}.git"

    # Check if fork already exists
    existing = session.exec(
        select(MetaRepoFork).where(MetaRepoFork.fork_name == fork_name)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Fork already exists: {fork_name}"
        )

    repos_dir = Path("./agenthub_data/repos").resolve()
    bare_repo_path = repos_dir / meta_config.repo_name
    fork_path = repos_dir / fork_name

    try:
        # Clone meta-repo to create fork
        subprocess.run(
            ["git", "clone", "--bare", str(bare_repo_path), str(fork_path)],
            check=True,
            capture_output=True
        )

        # Get current commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(bare_repo_path),
            capture_output=True,
            text=True
        )
        source_commit = result.stdout.strip()

        # Create fork record
        fork = MetaRepoFork(
            fork_name=fork_name,
            owner_type=owner_type,
            owner_id=owner_id,
            source_commit=source_commit,
            status="active",
        )
        session.add(fork)

        # Audit log
        audit = PlatformAuditLog(
            event_type="fork_created",
            actor_type=owner_type,
            actor_id=owner_id,
            target_type="fork",
            target_id=fork_name,
            details={"source_commit": source_commit}
        )
        session.add(audit)
        session.commit()

        return {
            "success": True,
            "fork_name": fork_name,
            "clone_url": f"/api/v1/repos/{fork_name}",
            "source_commit": source_commit,
        }

    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create fork: {e.stderr.decode() if e.stderr else str(e)}"
        )


@meta_router.get("/forks")
async def list_forks(
    session: Session = Depends(get_session)
):
    """List all forks of the meta-repository."""
    forks = session.exec(
        select(MetaRepoFork).where(MetaRepoFork.status == "active")
    ).all()

    return {
        "forks": [
            {
                "fork_name": f.fork_name,
                "owner_type": f.owner_type,
                "owner_id": f.owner_id,
                "source_commit": f.source_commit,
                "created_at": f.created_at.isoformat(),
            }
            for f in forks
        ]
    }


# === PR Management ===

@meta_router.post("/prs")
async def create_pr(
    request: CreatePRRequest,
    meta_config: MetaRepoConfig = Depends(get_meta_config),
    session: Session = Depends(get_session),
):
    """
    Create a Pull Request for the meta-repository.
    """
    # TODO: Get author info from auth (kept for future integration)
    # Validate source repo exists and is a fork
    source_fork = session.exec(
        select(MetaRepoFork).where(
            MetaRepoFork.fork_name == request.source_repo,
            MetaRepoFork.status == "active"
        )
    ).first()

    if not source_fork:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source fork not found: {request.source_repo}"
        )

    # 通过 WorkItemService 统一创建 PR（保持响应结构不变）
    wis = WorkItemService(session)
    try:
        pr = wis.meta.create_pr(
            meta_config=meta_config,
            title=request.title,
            description=request.description,
            source_branch=request.source_branch,
            source_repo=request.source_repo,
        )
        # 重建 changed_files 与 touches_protected 以兼容响应
        repos_dir = Path("./agenthub_data/repos").resolve()
        source_repo_path = repos_dir / request.source_repo
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", f"refs/heads/{request.source_branch}", "refs/heads/main"],
                cwd=str(source_repo_path), capture_output=True, text=True
            )
            changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except subprocess.CalledProcessError:
            changed_files = []
        touches_protected = any(
            any(fnmatch.fnmatch(f, pattern) for pattern in (meta_config.protected_paths or []))
            for f in changed_files
        )
        return {
            "success": True,
            "pr_number": pr.pr_number,
            "title": pr.title,
            "status": pr.status,
            "touches_protected_paths": touches_protected,
            "required_approval_count": pr.required_approval_count,
            "changed_files": changed_files,
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@meta_router.get("/prs")
async def list_prs(
    status_filter: Optional[str] = None,
    limit: int = 20,
    session: Session = Depends(get_session)
):
    """List Pull Requests with optional filtering."""
    wis = WorkItemService(session)
    prs = wis.meta.list_prs(status_filter=status_filter, limit=limit)

    return {
        "prs": [
            {
                "pr_number": pr.pr_number,
                "title": pr.title,
                "author_type": pr.author_type,
                "author_id": pr.author_id,
                "status": pr.status,
                "approval_count": pr.approval_count,
                "required_approval_count": pr.required_approval_count,
                "touches_protected_paths": pr.touches_protected_paths,
                "created_at": pr.created_at.isoformat(),
            }
            for pr in prs
        ]
    }


@meta_router.get("/prs/{pr_number}")
async def get_pr(
    pr_number: int,
    session: Session = Depends(get_session)
):
    """Get PR details including approvals and changed files."""
    # 通过 WorkItemService 获取统一视图（保留原有字段）
    wis = WorkItemService(session)
    item = wis.get("meta_pr", pr_number)

    pr = session.exec(
        select(PlatformPR).where(PlatformPR.pr_number == pr_number)
    ).first()

    if not pr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PR #{pr_number} not found"
        )

    return {
        "workitem": {
            "kind": item.kind if item else "meta_pr",
            "ref": item.ref if item else pr_number,
            "title": (item.title if item else pr.title),
            "status": (item.status if item else pr.status),
        },
        "pr_number": pr.pr_number,
        "title": pr.title,
        "description": pr.description,
        "source_branch": pr.source_branch,
        "source_repo": pr.source_repo,
        "author_type": pr.author_type,
        "author_id": pr.author_id,
        "status": pr.status,
        "touches_protected_paths": pr.touches_protected_paths,
        "approvals": pr.approvals,
        "approval_count": pr.approval_count,
        "required_approval_count": pr.required_approval_count,
        "verification_passed": pr.verification_passed,
        "update_id": pr.update_id,
        "created_at": pr.created_at.isoformat(),
        "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
        "deployed_at": pr.deployed_at.isoformat() if pr.deployed_at else None,
    }


@meta_router.post("/prs/{pr_number}/approve")
async def approve_pr(
    pr_number: int,
    request: ApprovePRRequest,
    meta_config: MetaRepoConfig = Depends(get_meta_config),
    session: Session = Depends(get_session),
):
    """
    Approve a Pull Request.

    For protected paths, requires different types of reviewers (at least 1 human + 1 agent).
    """
    pr = session.exec(
        select(PlatformPR).where(PlatformPR.pr_number == pr_number)
    ).first()

    if not pr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PR #{pr_number} not found"
        )

    if pr.status not in [PRStatus.OPEN.value, PRStatus.APPROVED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PR is in {pr.status} status, cannot approve"
        )

    # TODO: Get reviewer info from auth
    reviewer_type = "agent"
    reviewer_id = "test-reviewer"

    # 交由 WorkItemService 适配层执行（逻辑与旧实现等价）
    wis = WorkItemService(session)
    try:
        pr = wis.approve_meta_pr(
            pr_number,
            reviewer_type=reviewer_type,
            reviewer_id=reviewer_id,
            comment=request.comment,
            meta_config=meta_config,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {
        "success": True,
        "pr_number": pr.pr_number,
        "approval_count": pr.approval_count,
        "required_approval_count": pr.required_approval_count,
        "status": pr.status,
        "can_merge": pr.status == PRStatus.APPROVED.value,
    }


@meta_router.post("/prs/{pr_number}/merge")
async def merge_pr(
    pr_number: int,
    meta_config: MetaRepoConfig = Depends(get_meta_config),
    session: Session = Depends(get_session),
):
    """
    Merge an approved PR and trigger deployment.
    """
    pr = session.exec(
        select(PlatformPR).where(PlatformPR.pr_number == pr_number)
    ).first()

    if not pr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PR #{pr_number} not found"
        )

    if pr.status != PRStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PR is not approved for merge (status: {pr.status})"
        )

    # TODO: Get merger info from auth
    merger_type = "agent"
    merger_id = "test-merger"

    wis = WorkItemService(session)
    try:
        pr, update = wis.merge_meta_pr(pr_number, meta_config, merger_type=merger_type, merger_id=merger_id)
        return {
            "success": True,
            "pr_number": pr.pr_number,
            "merge_commit_sha": pr.merge_commit_sha,
            "update_id": update.id,
            "message": "PR merged successfully. Deployment pending.",
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# === Deployment Management ===

@meta_router.get("/updates")
async def list_updates(
    status_filter: Optional[str] = None,
    limit: int = 20,
    session: Session = Depends(get_session)
):
    """List platform update history."""
    wis = WorkItemService(session)
    updates = wis.meta.list_updates(status_filter=status_filter, limit=limit)

    return {
        "updates": [
            {
                "id": u.id,
                "source_pr_number": u.source_pr_number,
                "source_commit_sha": u.source_commit_sha[:8] if u.source_commit_sha else None,
                "status": u.status,
                "files_changed_count": len(u.files_changed or []),
                "triggered_by": u.triggered_by,
                "created_at": u.created_at.isoformat(),
                "completed_at": u.completed_at.isoformat() if u.completed_at else None,
            }
            for u in updates
        ]
    }


@meta_router.get("/updates/{update_id}")
async def get_update_status(
    update_id: int,
    session: Session = Depends(get_session)
):
    """Get detailed status of a platform update."""
    update = session.get(PlatformUpdate, update_id)

    if not update:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Update {update_id} not found"
        )

    return {
        "id": update.id,
        "source_pr_id": update.source_pr_id,
        "source_pr_number": update.source_pr_number,
        "source_commit_sha": update.source_commit_sha,
        "status": update.status,
        "files_changed": update.files_changed,
        "files_synced": update.files_synced,
        "files_failed": update.files_failed,
        "previous_commit_sha": update.previous_commit_sha,
        "rollback_available": update.rollback_available,
        "triggered_by": update.triggered_by,
        "deploy_log": update.deploy_log,
        "created_at": update.created_at.isoformat(),
        "started_at": update.started_at.isoformat() if update.started_at else None,
        "completed_at": update.completed_at.isoformat() if update.completed_at else None,
    }


@meta_router.post("/updates/{update_id}/rollback")
async def rollback_update(
    update_id: int,
    meta_config: MetaRepoConfig = Depends(get_meta_config),
    session: Session = Depends(get_session),
):
    """
    Rollback a failed or problematic update.

    Only callable by admin.
    """
    wis = WorkItemService(session)
    try:
        update = wis.meta.rollback_update(update_id, meta_config)
        return {
            "success": True,
            "update_id": update_id,
            "rolled_back_to": update.previous_commit_sha,
            "message": "Rollback completed",
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# === Audit Log ===

@meta_router.get("/audit-log")
async def get_audit_log(
    event_type: Optional[str] = None,
    actor_type: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session)
):
    """Get audit log entries."""
    query = select(PlatformAuditLog).order_by(PlatformAuditLog.timestamp.desc())

    if event_type:
        query = query.where(PlatformAuditLog.event_type == event_type)
    if actor_type:
        query = query.where(PlatformAuditLog.actor_type == actor_type)

    query = query.limit(limit)
    logs = session.exec(query).all()

    return {
        "logs": [
            {
                "id": log.id,
                "event_type": log.event_type,
                "actor_type": log.actor_type,
                "actor_id": log.actor_id,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "details": log.details,
                "timestamp": log.timestamp.isoformat(),
            }
            for log in logs
        ]
    }
