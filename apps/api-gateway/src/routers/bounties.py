import html
import logging
import os
from typing import Any, List, Optional, Tuple, Set
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Header, status
from sqlmodel import Session, select

from agent_auth.deps import get_auth_session
from agent_auth.services.repo_service import RepoService
from agenthub_execution_vmm.guard import ExecutionGuard
from core.middleware import limiter
from core.security import STORE_ROOT, get_secure_repo_path
from core.settings import get_settings
from dependencies.auth import require_agent, require_active_identity, require_active_identity_optional
from git_tree_service import GitTreeService
from persistence import Bounty, get_session
from agenthub_protocol.roles import UserRole
from agenthub_protocol.roles import RepoRole
from schemas.bounties import (
    BountyDecisionRequest,
    BountyDecisionResponse,
    CancelRequest,
    CreateBountyRequest,
    DecomposedBountyRequest,
    DecomposedBountyResponse,
    PreparationClaimRequest,
    RestoreRequest,
    GovernanceTransitionRequest,
    SubTaskDTO,
    TaskNode,
)

router = APIRouter()
logger = logging.getLogger(__name__)


_ADMIN_ARCHITECT_REQUIRED_DETAIL = "Forbidden: admin or repo architect required"


def _is_platform_admin(identity: Any) -> bool:
    role = getattr(identity, "role", None)
    if isinstance(role, UserRole):
        return role == UserRole.ADMIN
    if hasattr(role, "value"):
        return str(role.value).lower() == UserRole.ADMIN.value
    return str(role).lower() == UserRole.ADMIN.value


def _require_repo_architect_or_admin(auth_session: Session, repo_name: str, identity: Any) -> None:
    from agent_auth.services.authz import require_repo_member

    if _is_platform_admin(identity):
        return

    if require_repo_member(auth_session, repo_name, str(identity.id), role=RepoRole.ARCHITECT):
        return

    raise HTTPException(status_code=403, detail=_ADMIN_ARCHITECT_REQUIRED_DETAIL)


def _identity_actor_ctx(identity: Any) -> tuple[str, Optional[str]]:
    role = getattr(identity, "role", None)
    role_value = str(role.value).lower() if hasattr(role, "value") else str(role).lower()
    actor_type = "user" if role_value in {UserRole.USER.value, UserRole.ADMIN.value} else "agent"
    actor_id = getattr(identity, "id", None) or getattr(identity, "agent_id", None)
    return actor_type, str(actor_id) if actor_id is not None else None


def _require_same_agent_and_repo_role(
    auth_session: Session,
    repo_name: str,
    identity: Any,
    requested_agent_id: str,
    *,
    allowed_roles: tuple[RepoRole, ...],
) -> str:
    from agent_auth.services.authz import require_repo_member

    identity_id = str(getattr(identity, "id", ""))
    if identity_id != requested_agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    if not require_repo_member(auth_session, repo_name, identity_id):
        raise HTTPException(status_code=403, detail="Forbidden: Not a member of this repository")

    for role in allowed_roles:
        if require_repo_member(auth_session, repo_name, identity_id, role=role):
            return role.value

    allowed_label = " or ".join(role.value for role in allowed_roles)
    raise HTTPException(status_code=403, detail=f"Forbidden: repo role {allowed_label} required")


def _enforce_non_admin_agent_identity(identity: Any, requested_agent_id: str) -> None:
    if _is_platform_admin(identity):
        return

    if str(getattr(identity, "id", "")) != requested_agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")


def _require_governance_identity(
    auth_session: Session,
    repo_name: str,
    identity: Any,
    *,
    requested_agent_id: Optional[str] = None,
) -> None:
    _require_repo_architect_or_admin(auth_session, repo_name, identity)
    if requested_agent_id is not None:
        _enforce_non_admin_agent_identity(identity, requested_agent_id)


def _resolve_or_create_repo_for_bounty(
    auth_session: Session,
    repo_name: str,
    requested_repo_id: Optional[str],
) -> tuple[str, Optional[str]]:
    """Resolve repo by name and keep repo_name/repo_id consistent on bounty records."""
    service = RepoService(auth_session)

    repo = service.get_repo_by_full_name(repo_name)
    if repo is None:
        repo = service.get_or_create_repo(full_name=repo_name)

    canonical_repo_id = repo.id

    if requested_repo_id:
        try:
            requested_repo_uuid = UUID(requested_repo_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid repo_id")

        if requested_repo_uuid != canonical_repo_id:
            raise HTTPException(
                status_code=400,
                detail="repo_id does not match repo_name",
            )

    # Bounty persistence schema stores repo_id as string in this service layer
    return repo.full_name, str(canonical_repo_id)


@router.get("/api/v1/bounties")
@router.get("/bounties")
@limiter.limit("60/minute")
def list_bounties(
    request: Request,
    status: Optional[str] = None,
    repo_name: Optional[str] = None,
    required_role: Optional[RepoRole] = None,
    session: Session = Depends(get_session),
):
    """List bounties. Defaults to open if no status specified.

    Query params:
    - status: open|pending|ready_for_preparation|in_progress|submitted|completed|cancelled
    - repo_name: filter by repository full name
    - required_role: filter by role
    """
    stmt = select(Bounty)
    if status:
        stmt = stmt.where(Bounty.status == status)
    else:
        stmt = stmt.where(Bounty.status == "open")
    if repo_name:
        stmt = stmt.where(Bounty.repo_name == repo_name)
    if required_role:
        stmt = stmt.where(Bounty.required_role == required_role.value if hasattr(required_role, "value") else required_role)
    return session.exec(stmt).all()


@router.post("/api/v1/bounties")
@router.post("/bounties")
@limiter.limit("20/minute")
def create_bounty(
    request: Request,
    bounty: CreateBountyRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    """Post a new job (strict DTO)."""
    # Input validation
    if not bounty.title or not bounty.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    if not bounty.repo_name or not bounty.repo_name.strip():
        raise HTTPException(status_code=400, detail="Repo name is required")

    # Reward validation (must be positive)
    if bounty.reward is not None and bounty.reward <= 0:
        raise HTTPException(status_code=400, detail="Reward must be a positive number")

    # Sanitization
    def sanitize_text(text: str, max_length: int = 1000) -> str:
        """Remove potentially dangerous characters from text."""
        if not text:
            return ""
        text = html.escape(text)
        dangerous_patterns = [";", "--", "/*", "*/", "xp_", "DROP", "DELETE", "INSERT", "UPDATE", "UNION"]
        for pattern in dangerous_patterns:
            if pattern.lower() in text.lower():
                raise HTTPException(status_code=400, detail="Invalid input: contains forbidden pattern")
        return text[:max_length]

    title = sanitize_text(bounty.title, 200)
    description = sanitize_text(bounty.description or "", 2000)
    repo_name = sanitize_text(bounty.repo_name, 100)

    # Validate required_role (enum coerces from string)
    if not isinstance(bounty.required_role, RepoRole):
        try:
            bounty.required_role = RepoRole(str(bounty.required_role).lower())
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid role: {bounty.required_role}")

    resolved_repo_name, resolved_repo_id = _resolve_or_create_repo_for_bounty(
        auth_session=auth_session,
        repo_name=repo_name,
        requested_repo_id=bounty.repo_id,
    )

    _require_governance_identity(auth_session, resolved_repo_name, identity)

    # verification_mode validation
    verification_mode = (bounty.verification_mode or get_settings().default_verification_mode).lower()
    if verification_mode not in ["auto", "human", "external"]:
        raise HTTPException(status_code=400, detail="Invalid verification_mode")

    # test_command policy (full command validation)
    normalized_test_command = (bounty.test_command or "pytest").strip()
    try:
        tokens = ExecutionGuard.verify_command(normalized_test_command)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Construct server-side Bounty with safe defaults
    new_bounty = Bounty(
        title=title,
        description=description,
        reward=bounty.reward,
        status="open",
        repo_name=resolved_repo_name,
        repo_id=resolved_repo_id,
        required_role=bounty.required_role.value if isinstance(bounty.required_role, RepoRole) else bounty.required_role,
        assignee=None,
        parent_id=None,
        dependencies=[],
        estimated_hours=bounty.estimated_hours,
        track=bounty.track,
        is_temporary_claim=False,
        claim_expires_at=None,
        claimed_by_user_id=None,
        max_steps=15,
        current_steps=0,
        context_files=[],
        target_files=[],
        acceptance_criteria=None,
        test_command=" ".join(tokens),
        verification_mode=verification_mode,
    )

    session.add(new_bounty)
    session.commit()
    session.refresh(new_bounty)
    return new_bounty


@router.post("/api/v1/bounties/{parent_id}/decompose")
@router.post("/bounties/{parent_id}/decompose")
def decompose_task(
    parent_id: str,
    sub_tasks: List[SubTaskDTO],
    agent_id: str,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    """[Task Board] Allow repo architects/admin to split a task into atomic sub-tasks (strict DTO)."""
    parent = session.get(Bounty, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent task not found")

    _require_governance_identity(
        auth_session,
        parent.repo_name,
        identity,
        requested_agent_id=agent_id,
    )

    created_tasks = []
    for dto in sub_tasks:
        # Normalize required_role to enum
        if not isinstance(dto.required_role, RepoRole):
            try:
                dto.required_role = RepoRole(str(dto.required_role).lower())
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid role: {dto.required_role}")
        normalized_test_command = (dto.test_command or "pytest").strip()
        try:
            tokens = ExecutionGuard.verify_command(normalized_test_command)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        # Server-side construction with safe defaults
        st = Bounty(
            title=dto.title,
            description=dto.description,
            reward=dto.reward,
            status="open",
            repo_name=parent.repo_name,
            repo_id=parent.repo_id,
            required_role=dto.required_role.value if isinstance(dto.required_role, RepoRole) else dto.required_role,
            assignee=None,
            parent_id=parent_id,
            dependencies=[],
            estimated_hours=dto.estimated_hours,
            track=dto.track,
            is_temporary_claim=False,
            claim_expires_at=None,
            claimed_by_user_id=None,
            max_steps=15,
            current_steps=0,
            context_files=[],
            target_files=[],
            acceptance_criteria=None,
            test_command=" ".join(tokens),
            verification_mode=(dto.verification_mode or "auto"),
        )
        session.add(st)
        created_tasks.append(st)

    session.commit()
    for task in created_tasks:
        session.refresh(task)
    return {"parent_id": parent_id, "children": created_tasks}


# === Hierarchical Bounty System (DAG) ===


def _flatten_task_tree(
    node: TaskNode,
    parent_id: Optional[str],
    repo_name: str,
    repo_id: Optional[str],
    client_to_server_id: dict,
    all_bounties: List[Bounty],
) -> Tuple[Bounty, Optional[str]]:
    """Recursively flatten a task tree into individual Bounty records."""
    from persistence import BountyStatus

    bounty = Bounty(
        title=node.title,
        description=node.description,
        reward=node.reward,
        repo_name=repo_name,
        repo_id=repo_id,
        required_role=(node.required_role.value if hasattr(node.required_role, "value") else node.required_role),
        parent_id=parent_id,
        estimated_hours=node.estimated_hours,
        track=node.track,
        dependencies=[],
        test_command=node.test_command,
        verification_mode=node.verification_mode,
        status=BountyStatus.PENDING.value if node.dependencies else BountyStatus.OPEN.value,
    )
    all_bounties.append(bounty)
    # Return bounty and client_id (if provided) so caller can build mapping
    return bounty, (node.client_id or None)


@router.post("/api/v1/bounties/decomposed", response_model=DecomposedBountyResponse)
@limiter.limit("10/minute")
def create_decomposed_bounties(
    request: Request,
    req: DecomposedBountyRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    """
    Create a hierarchical bounty tree from a nested JSON structure.

    Enables Architect agents to create complex task DAGs with:
    - Parallel tracks (via 'track' field)
    - Dependencies between tasks (via 'dependencies' field)
    - Automatic status management (pending -> open when dependencies complete)
    """
    from persistence import BountyStatus

    # Validate repo exists in filesystem
    repo_path = get_secure_repo_path(req.repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail=f"Repo '{req.repo_name}' not found")

    # Resolve/create repo registry record and enforce repo_id consistency
    resolved_repo_name, resolved_repo_id = _resolve_or_create_repo_for_bounty(
        auth_session=auth_session,
        repo_name=req.repo_name,
        requested_repo_id=req.repo_id,
    )

    _require_governance_identity(auth_session, resolved_repo_name, identity)

    all_bounties: List[Bounty] = []
    client_to_server_id: dict = {}
    seen_client_ids: set = set()

    def process_node(node: TaskNode, parent_id: Optional[str] = None):
        """Recursively process a task node, building client_id -> server_id mapping."""
        # Enforce client_id uniqueness if provided
        if node.client_id:
            cid = node.client_id.strip()
            if not cid:
                raise HTTPException(status_code=400, detail="client_id cannot be empty when provided")
            if cid in seen_client_ids:
                raise HTTPException(status_code=400, detail=f"Duplicate client_id detected: '{cid}'")
            seen_client_ids.add(cid)

        verification_mode = (node.verification_mode or get_settings().default_verification_mode).lower()
        if verification_mode not in ["auto", "human", "external"]:
            raise HTTPException(status_code=400, detail="Invalid verification_mode")

        normalized_test_command = (node.test_command or "pytest").strip()
        try:
            tokens = ExecutionGuard.verify_command(normalized_test_command)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        node.test_command = " ".join(tokens)
        node.verification_mode = verification_mode

        bounty, cid = _flatten_task_tree(
            node=node,
            parent_id=parent_id,
            repo_name=resolved_repo_name,
            repo_id=resolved_repo_id,
            client_to_server_id=client_to_server_id,
            all_bounties=all_bounties,
        )
        session.add(bounty)
        session.flush()
        if cid:
            client_to_server_id[cid] = bounty.id

        for child in node.children:
            process_node(child, bounty.id)

    process_node(req.root_task)

    # Resolve dependencies using client_id -> bounty_id mapping
    def find_node_deps_by_client_id(node: TaskNode, target_client_id: Optional[str]) -> Optional[List[str]]:
        if (node.client_id or None) == target_client_id:
            return node.dependencies
        for child in node.children:
            result = find_node_deps_by_client_id(child, target_client_id)
            if result is not None:
                return result
        return None

    for bounty in all_bounties:
        # Determine this bounty's client_id by reverse lookup
        this_client_id = None
        for cid, sid in client_to_server_id.items():
            if sid == bounty.id:
                this_client_id = cid
                break
        original_deps = find_node_deps_by_client_id(req.root_task, this_client_id)
        if original_deps:
            node_deps = []
            for dep_cid in original_deps:
                if dep_cid in client_to_server_id:
                    node_deps.append(client_to_server_id[dep_cid])
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Dependency client_id '{dep_cid}' not found for task '{bounty.title}'",
                    )
            bounty.dependencies = node_deps
            bounty.status = BountyStatus.PENDING.value
        else:
            bounty.status = BountyStatus.OPEN.value

    session.commit()

    for bounty in all_bounties:
        session.refresh(bounty)

    task_tree_sync = {
        "attempted": True,
        "status": "pending",
        "error": None,
    }

    # Sync task tree to repository
    try:
        tree_service = GitTreeService(session, STORE_ROOT)
        tree_service.sync_repo_task_tree(resolved_repo_name, identity.id)
        task_tree_sync["status"] = "synced"
    except Exception as e:
        err = str(e)[:500]
        task_tree_sync["status"] = "failed"
        task_tree_sync["error"] = err
        logger.warning(
            "[bounties][decomposed] task tree sync failed repo=%s actor=%s error=%s",
            resolved_repo_name,
            str(identity.id),
            err,
        )

    bounty_dicts = [
        {
            "id": b.id,
            "title": b.title,
            "status": b.status,
            "dependencies": b.dependencies,
            "track": b.track,
            "estimated_hours": b.estimated_hours,
            "parent_id": b.parent_id,
        }
        for b in all_bounties
    ]

    return DecomposedBountyResponse(
        total_created=len(all_bounties),
        bounties=bounty_dicts,
        dependency_map=client_to_server_id,
        task_tree_sync=task_tree_sync,
    )


def resolve_bounty_dependencies(bounty_id: str, session: Session) -> int:
    """
    Check and update bounty status when dependencies complete.

    Handles two cases:
    1. pending -> open (when all dependencies complete)
    2. ready_for_preparation -> open/in_progress (when all dependencies complete)

    Returns the number of bounties that transitioned.
    """
    from persistence import BountyStatus

    updated_count = 0

    # Case 1: pending bounties
    pending_bounties = session.exec(
        select(Bounty).where(
            Bounty.status == BountyStatus.PENDING.value
        )
    ).all()

    for bounty in pending_bounties:
        # Skip if this bounty does not depend on the completed bounty_id
        if bounty.dependencies and bounty_id not in bounty.dependencies:
            continue

        # Check all dependencies are completed
        all_deps_completed = True
        for dep_id in bounty.dependencies:
            dep_bounty = session.get(Bounty, dep_id)
            if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                all_deps_completed = False
                break

        if all_deps_completed:
            from agent_auth.services.bounty_fsm import transition
            updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
            if not err:
                updated_count += 1

            # Sync task tree to repository if status changed
            try:
                tree_service = GitTreeService(session, STORE_ROOT)
                tree_service.sync_repo_task_tree(bounty.repo_name)
            except Exception as e:
                logger.warning(
                    "[bounties][dependency-resolution] task tree sync failed repo=%s bounty_id=%s error=%s",
                    bounty.repo_name,
                    bounty.id,
                    str(e)[:500],
                )

    # Case 2: ready_for_preparation bounties (with or without assignee)
    preparable_bounties = session.exec(
        select(Bounty).where(
            Bounty.status == BountyStatus.READY_FOR_PREPARATION.value
        )
    ).all()

    for bounty in preparable_bounties:
        # Skip if this bounty does not depend on the completed bounty_id
        if bounty.dependencies and bounty_id not in bounty.dependencies:
            continue

        all_deps_completed = True
        for dep_id in bounty.dependencies:
            dep_bounty = session.get(Bounty, dep_id)
            if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                all_deps_completed = False
                break

        if all_deps_completed:
            from agent_auth.services.bounty_fsm import transition
            if bounty.assignee:
                updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
            else:
                updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
            if not err:
                updated_count += 1

    if updated_count > 0:
        session.commit()

    return updated_count


@router.post("/api/v1/bounties/{bounty_id}/claim")
@router.post("/bounties/{bounty_id}/claim")
def claim_bounty_route(
    bounty_id: str,
    agent_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Any = Depends(require_agent),
):
    """
    Agent claims a job.

    Two modes:
    1. Authenticated (user logged in): Permanent claim with full validation
    2. Unauthenticated (no user): Temporary claim, expires in 24 hours, contributor role only
    """
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    # Use BountyService for unified validation
    from agent_auth.services.bounty_service import BountyService
    from agent_auth.services.user_auth import UserAuthService
    service = BountyService(bounty_session=session, auth_session=auth_session)

    # Check if user is authenticated via Authorization header
    user = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        auth_service = UserAuthService(auth_session)
        payload = auth_service.verify_token(token)
        if payload:
            user_id = payload.get("sub")
            user = auth_service.get_user_by_id(user_id)

    if user:
        # Authenticated claim - permanent with full validation
        bounty, error = service.claim_bounty(bounty_id, agent_id)
        if error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error
            )
        # Mark as claimed by user
        bounty.claimed_by_user_id = str(user.id)
        bounty.is_temporary_claim = False
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
    else:
        # Unauthenticated claim - temporary with restrictions
        bounty, error = service.create_temporary_claim(bounty_id, agent_id)
        if error:
            # Check if it's an architect restriction
            if "Architect role requires login" in error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=error
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error
            )

    return bounty


@router.post("/api/v1/bounties/{bounty_id}/convert-claim")
@router.post("/bounties/{bounty_id}/convert-claim")
def convert_temporary_claim_route(
    bounty_id: str,
    agent_id: str,
    authorization: str = Header(..., alias="Authorization"),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Any = Depends(require_agent),
):
    """
    Convert a temporary claim to permanent (user logged in).

    Requires valid JWT token. Validates agent eligibility and removes temporary flag.
    """
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    # Verify user authentication
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required. Use: Bearer <token>"
        )

    token = authorization[7:]
    from agent_auth.services.user_auth import UserAuthService
    auth_service = UserAuthService(auth_session)
    payload = auth_service.verify_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    user_id = payload.get("sub")
    user = auth_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    # Convert temporary claim
    from agent_auth.services.bounty_service import BountyService
    service = BountyService(bounty_session=session, auth_session=auth_session)
    bounty, error = service.convert_temporary_claim_to_permanent(
        bounty_id, str(user.id), agent_id
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    return bounty


# === Preparation Mode Endpoints ===

@router.post("/api/v1/bounties/{bounty_id}/mark-preparable")
@limiter.limit("20/minute")
def mark_bounty_preparable(
    request: Request,
    bounty_id: str,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity)
):
    """
    Mark a pending bounty as ready for preparation.

    Requires platform admin or repo architect.

    Status transition: pending -> ready_for_preparation
    """
    from persistence import BountyStatus

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    _require_governance_identity(auth_session, bounty.repo_name, identity)

    if bounty.status != BountyStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty must be in 'pending' status. Current status: {bounty.status}"
        )

    from agent_auth.services.bounty_fsm import transition

    actor_type, actor_id = _identity_actor_ctx(identity)

    updated, err = transition(
        session,
        bounty.id,
        BountyStatus.READY_FOR_PREPARATION.value,
        ctx={
            "actor_type": actor_type,
            "actor_id": actor_id,
        },
    )
    if err:
        raise HTTPException(status_code=409, detail=err)

    return {
        "id": updated.id,
        "title": updated.title,
        "status": updated.status,
        "message": "Bounty marked as ready for preparation. Contributors can now prepare."
    }


@router.post("/api/v1/bounties/{bounty_id}/claim-preparation")
@limiter.limit("10/minute")
def claim_bounty_for_preparation(
    request: Request,
    bounty_id: str,
    req: PreparationClaimRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Any = Depends(require_agent)
):
    """
    Claim a bounty for preparation (early access).

    This endpoint allows Contributors to:
    - View and analyze the task
    - Study the codebase
    - Prepare implementation plan
    - BUT cannot submit code until all dependencies complete

    Requirements:
    - Bounty must be in 'ready_for_preparation' status
    - Agent must have matching role (contributor)
    - Dependencies must still be tracked

    When all dependencies complete, the bounty auto-transitions to 'open' and
    the preparing agent gets first priority to claim.
    """
    from persistence import BountyStatus

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    _require_same_agent_and_repo_role(
        auth_session,
        bounty.repo_name,
        agent,
        req.agent_id,
        allowed_roles=(RepoRole.CONTRIBUTOR,),
    )

    # Check status - must be ready_for_preparation
    if bounty.status != BountyStatus.READY_FOR_PREPARATION.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty is not ready for preparation. Current status: {bounty.status}"
        )

    # Atomic claim for preparation via FSM (keeps audit/concurrency semantics)
    from agent_auth.services.bounty_fsm import transition

    _, err = transition(
        session,
        bounty_id,
        BountyStatus.READY_FOR_PREPARATION.value,
        ctx={"actor_type": "agent", "actor_id": str(agent.id), "agent_id": str(agent.id)},
    )
    if err:
        if "already claimed for preparation" in err.lower() or "concurrent" in err.lower() or "race" in err.lower():
            raise HTTPException(status_code=409, detail="Bounty already claimed for preparation")
        raise HTTPException(status_code=400, detail=err)

    # Optional: append preparation notes after successful claim (structured)
    if req.preparation_notes:
        bounty = session.get(Bounty, bounty_id)
        notes_entry = {
            "agent_id": str(agent.id),
            "notes": req.preparation_notes,
            "timestamp": datetime.utcnow().isoformat(),
        }
        bounty.preparation_notes = (bounty.preparation_notes or []) + [notes_entry]
        session.add(bounty)

    session.commit()
    bounty = session.get(Bounty, bounty_id)

    return {
        "id": bounty.id,
        "title": bounty.title,
        "status": bounty.status,
        "assignee": bounty.assignee,
        "dependencies": bounty.dependencies,
        "message": "Bounty claimed for preparation. You can prepare but cannot submit until dependencies complete.",
        "warning": "Code submission will be blocked until all dependencies are completed."
    }


@router.post("/api/v1/bounties/{bounty_id}/activate-from-preparation")
@limiter.limit("10/minute")
def activate_from_preparation(
    request: Request,
    bounty_id: str,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Optional[Any] = Depends(require_active_identity_optional),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """
    Internal endpoint to activate a prepared bounty when dependencies complete.

    AuthN/AuthZ:
    - Preferred: X-Internal-Token must match env INTERNAL_API_TOKEN
    - Otherwise: Only ADMIN user or ARCHITECT agent can invoke

    Called automatically by resolve_bounty_dependencies when all dependencies
    are marked as completed. Transitions status from ready_for_preparation to open.

    The agent who claimed for preparation gets first priority.
    """
    from persistence import BountyStatus

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    # Authorization gate
    expected_token = get_settings().internal_api_token
    if expected_token and x_internal_token and x_internal_token == expected_token:
        pass
    else:
        if identity is None:
            raise HTTPException(status_code=401, detail="Valid X-API-Key or Bearer Token required")
        _require_governance_identity(auth_session, bounty.repo_name, identity)

    if bounty.status != BountyStatus.READY_FOR_PREPARATION.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty is not in preparation mode. Current status: {bounty.status}"
        )

    # Check if all dependencies are completed
    all_deps_completed = True
    for dep_id in bounty.dependencies:
        dep_bounty = session.get(Bounty, dep_id)
        if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
            all_deps_completed = False
            break

    if not all_deps_completed:
        raise HTTPException(
            status_code=400,
            detail="Not all dependencies are completed yet"
        )

    # Activate via FSM
    from agent_auth.services.bounty_fsm import transition
    if bounty.assignee:
        updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
    else:
        updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
    if err:
        raise HTTPException(status_code=400, detail=err)

    return {
        "id": updated.id,
        "title": updated.title,
        "status": updated.status,
        "assignee": updated.assignee,
        "message": "Bounty activated. Preparer can now submit code."
    }


# --- Cancel / Restore Endpoints ---


def _collect_cascade_ids(session: Session, root_id: str) -> Set[str]:
    """Collect child and dependent bounty ids for strict cascade."""
    ids: Set[str] = set()
    to_visit = [root_id]
    while to_visit:
        current = to_visit.pop()
        if current in ids:
            continue
        ids.add(current)
        # Children
        children = session.exec(select(Bounty).where(Bounty.parent_id == current)).all()
        for child in children:
            to_visit.append(child.id)
        # Reverse dependents
        dependents = session.exec(select(Bounty).where(Bounty.dependencies.contains([current]))).all()
        for dependent in dependents:
            to_visit.append(dependent.id)
    return ids


@router.post("/api/v1/bounties/{bounty_id}/governance-transition")
@router.post("/bounties/{bounty_id}/governance-transition")
@limiter.limit("20/minute")
def governance_transition_bounty(
    request: Request,
    bounty_id: str,
    req: GovernanceTransitionRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    from persistence import BountyStatus

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    _require_governance_identity(auth_session, bounty.repo_name, identity)

    to_status = (req.to_status or "").strip().lower()
    actor_type, actor_id = _identity_actor_ctx(identity)

    if to_status == BountyStatus.CANCELLED.value:
        ids = _collect_cascade_ids(session, bounty_id) if req.force else {bounty_id}
        from agent_auth.services.bounty_fsm import transition

        errors = []
        for bid in ids:
            _, err = transition(
                session,
                bid,
                BountyStatus.CANCELLED.value,
                ctx={
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "reason": req.reason,
                },
            )
            if err:
                errors.append({"bounty_id": bid, "error": err})

        if errors:
            raise HTTPException(status_code=409, detail={"message": "Some cancellations failed", "errors": errors})
        return {"success": True, "id": bounty_id, "status": BountyStatus.CANCELLED.value, "cancelled": list(ids), "count": len(ids)}

    if to_status in (BountyStatus.OPEN.value, BountyStatus.PENDING.value):
        from agent_auth.services.bounty_fsm import transition

        if bounty.status == BountyStatus.READY_FOR_PREPARATION.value:
            all_deps_completed = True
            for dep_id in bounty.dependencies:
                dep_bounty = session.get(Bounty, dep_id)
                if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                    all_deps_completed = False
                    break
            if not all_deps_completed:
                raise HTTPException(status_code=400, detail="Not all dependencies are completed yet")

        updated, err = transition(
            session,
            bounty_id,
            to_status,
            ctx={
                "actor_type": actor_type,
                "actor_id": actor_id,
                "reason": req.reason,
            },
        )
        if err:
            raise HTTPException(status_code=409, detail=err)
        return {"success": True, "id": updated.id, "status": updated.status}

    if to_status == BountyStatus.READY_FOR_PREPARATION.value:
        if bounty.status != BountyStatus.PENDING.value:
            raise HTTPException(
                status_code=400,
                detail=f"Bounty must be in 'pending' status. Current status: {bounty.status}",
            )

        from agent_auth.services.bounty_fsm import transition

        updated, err = transition(
            session,
            bounty.id,
            BountyStatus.READY_FOR_PREPARATION.value,
            ctx={"actor_type": actor_type, "actor_id": actor_id, "reason": req.reason},
        )
        if err:
            raise HTTPException(status_code=409, detail=err)

        return {"success": True, "id": updated.id, "status": updated.status}

    if to_status in (BountyStatus.OPEN.value, BountyStatus.IN_PROGRESS.value) and bounty.status == BountyStatus.READY_FOR_PREPARATION.value:
        all_deps_completed = True
        for dep_id in bounty.dependencies:
            dep_bounty = session.get(Bounty, dep_id)
            if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                all_deps_completed = False
                break
        if not all_deps_completed:
            raise HTTPException(status_code=400, detail="Not all dependencies are completed yet")

        from agent_auth.services.bounty_fsm import transition

        updated, err = transition(
            session,
            bounty.id,
            BountyStatus.IN_PROGRESS.value if bounty.assignee else BountyStatus.OPEN.value,
            ctx={"actor_type": actor_type, "actor_id": actor_id, "reason": req.reason},
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return {"success": True, "id": updated.id, "status": updated.status, "assignee": updated.assignee}

    if to_status == BountyStatus.IN_PROGRESS.value and bounty.status == BountyStatus.SUBMITTED.value:
        from agent_auth.services.bounty_fsm import transition

        updated, err = transition(
            session,
            bounty.id,
            BountyStatus.IN_PROGRESS.value,
            ctx={"actor_type": actor_type, "actor_id": actor_id, "reason": req.reason},
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return {"success": True, "id": updated.id, "status": updated.status}

    if to_status == BountyStatus.COMPLETED.value and bounty.status == BountyStatus.SUBMITTED.value:
        from agent_auth.services.bounty_fsm import transition

        updated, err = transition(
            session,
            bounty.id,
            BountyStatus.COMPLETED.value,
            ctx={"actor_type": actor_type, "actor_id": actor_id, "reason": req.reason},
        )
        if err:
            raise HTTPException(status_code=400, detail=err)

        resolve_bounty_dependencies(updated.id, session)
        return {"success": True, "id": updated.id, "status": updated.status}

    raise HTTPException(status_code=400, detail=f"Unsupported governance transition target: {to_status}")


@router.post("/api/v1/bounties/{bounty_id}/cancel")
@limiter.limit("10/minute")
def cancel_bounty(
    request: Request,
    bounty_id: str,
    req: CancelRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    """Cancel a bounty with strict cascade to children and dependents.

    Auth: repo Architect or platform Admin. Cascade default enabled (force=True).
    """
    # AuthZ: repo membership + architect/admin check
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    _require_governance_identity(auth_session, bounty.repo_name, identity)

    # Cascade ids
    ids = _collect_cascade_ids(session, bounty_id) if req.force else {bounty_id}

    from agent_auth.services.bounty_fsm import transition
    from persistence import BountyStatus

    actor_type, actor_id = _identity_actor_ctx(identity)

    errors = []
    for bid in ids:
        _, err = transition(
            session,
            bid,
            BountyStatus.CANCELLED.value,
            ctx={
                "actor_type": actor_type,
                "actor_id": actor_id,
                "reason": req.reason,
            },
        )
        if err:
            errors.append({"bounty_id": bid, "error": err})

    if errors:
        raise HTTPException(status_code=409, detail={"message": "Some cancellations failed", "errors": errors})

    return {"cancelled": list(ids), "count": len(ids)}


@router.post("/api/v1/bounties/{bounty_id}/restore")
@limiter.limit("10/minute")
def restore_bounty(
    request: Request,
    bounty_id: str,
    req: RestoreRequest,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    identity: Any = Depends(require_active_identity),
):
    """Restore a cancelled bounty. If dependencies complete -> open else pending."""
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    _require_governance_identity(auth_session, bounty.repo_name, identity)

    from agent_auth.services.bounty_fsm import transition
    from persistence import BountyStatus

    actor_type, actor_id = _identity_actor_ctx(identity)

    # Try OPEN first, fallback to PENDING
    updated, err = transition(
        session,
        bounty_id,
        BountyStatus.OPEN.value,
        ctx={
            "actor_type": actor_type,
            "actor_id": actor_id,
        },
    )
    if err:
        updated, err = transition(
            session,
            bounty_id,
            BountyStatus.PENDING.value,
            ctx={
                "actor_type": actor_type,
                "actor_id": actor_id,
            },
        )
        if err:
            raise HTTPException(status_code=409, detail=err)

    return {"restored": updated.id, "status": updated.status}


# --- Bounty Decision Endpoint (Structured Output Validation) ---


@router.post("/api/v1/bounties/{bounty_id}/analyze", response_model=BountyDecisionResponse)
@router.post("/bounties/{bounty_id}/analyze", response_model=BountyDecisionResponse)
@limiter.limit("10/minute")
async def analyze_bounty(
    request: Request,
    bounty_id: str,
    req: BountyDecisionRequest,
    agent: Any = Depends(require_agent),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
):
    """
    Submit structured analysis/decision options for a bounty.

    This endpoint enforces structured output format:
    - Must be valid JSON array
    - Must have 3-5 options
    - Each option must have 'option' and 'reason' fields
    - No questions or deflections allowed

    If validation fails, a retry prompt is returned.
    Repeated violations result in reputation penalties and potential suspension.
    """
    from agent_auth.models import Agent
    from agent_auth.services.penalty_service import PenaltyService
    from agent_auth.validators.output_validator import get_validator

    try:
        agent_uuid = UUID(str(agent.id))
    except ValueError:
        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message="Invalid agent identity",
            is_suspended=True,
        )

    db_agent = auth_session.get(Agent, agent_uuid)
    if not db_agent:
        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message="Agent not found",
            is_suspended=True,
        )

    penalty_service = PenaltyService(auth_session)

    # Check if agent is allowed to act
    allowed, reason = penalty_service.is_agent_allowed(db_agent)
    if not allowed:
        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message=reason,
            reputation_score=db_agent.reputation_score,
            is_suspended=True,
        )

    validator = get_validator()
    result, retry_prompt = validator.validate_with_retry_prompt(req.options_json)

    if not result.is_valid:
        # Record violation and apply penalty
        is_suspended, _ = penalty_service.record_violation(
            db_agent,
            result.error_message or "Output validation failed",
            result.penalty_points,
        )

        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message=result.error_message,
            retry_prompt=retry_prompt,
            reputation_score=db_agent.reputation_score,
            is_suspended=is_suspended,
        )

    # Validation passed - record success for reputation recovery
    new_score = penalty_service.record_success(db_agent)

    return BountyDecisionResponse(
        success=True,
        is_valid=True,
        parsed_options=result.parsed_options,
        reputation_score=new_score,
        is_suspended=False,
    )
