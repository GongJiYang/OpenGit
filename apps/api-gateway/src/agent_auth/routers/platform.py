"""
Platform Router - Thin API Layer

This router is intentionally THIN - it only handles:
1. Request parsing
2. Service delegation
3. Response formatting

All business logic lives in Service classes.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlmodel import Session

from ..models.platform import (
    User,
    RepoRole,
    MembershipStatus,
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    KickMemberRequest,
    RepoMemberResponse,
    RepoResponse,
    CreateRepoRequest,
)
from ..services.user_auth import UserAuthService, get_current_user
from ..services.repo_service import RepoService
from ..database import get_db

router = APIRouter(tags=["Platform"])


# ============== Helper Functions ==============

def _get_bound_agent_or_403(user: User, session: Session):
    """Get user's bound agent or raise 403."""
    auth_service = UserAuthService(session)
    agent = auth_service.get_user_bound_agent(user)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must bind an Agent before performing this action"
        )
    return agent, auth_service


# ============== Auth Endpoints ==============

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: Session = Depends(get_db)):
    """Register a new human user."""
    service = UserAuthService(session)
    result, error = service.register(data)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return result


@auth_router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, session: Session = Depends(get_db)):
    """Login with email and password."""
    service = UserAuthService(session)
    result, error = service.login(data.email, data.password)
    if error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error)
    return result


@auth_router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        github_login=user.github_login,
        avatar_url=user.avatar_url,
        role=user.role,
        created_at=user.created_at,
    )


@auth_router.post("/bind-agent")
async def bind_agent(
    agent_id: UUID,
    x_api_key: str = Header(..., description="Agent API Key to bind"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Permanently bind an Agent to the authenticated user."""
    from ..utils import verify_api_key, get_api_key_prefix
    from ..models import Agent, AgentStatus
    from sqlmodel import select

    # Find agent by API key (service handles this internally in real impl)
    key_prefix = get_api_key_prefix(x_api_key)
    agents = session.exec(select(Agent).where(Agent.api_key_prefix == key_prefix)).all()
    agent = next((a for a in agents if verify_api_key(x_api_key, a.api_key_hash)), None)

    if not agent:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    if agent.id != agent_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API Key mismatch")
    if agent.status == AgentStatus.CLAIMED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already claimed")

    # Delegate to service
    service = UserAuthService(session)
    try:
        binding = service.bind_agent_to_user(user, agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {"success": True, "agent_id": str(agent.id), "bound_at": binding.bound_at}


# ============== Repo Endpoints ==============

repo_router = APIRouter(prefix="/repos", tags=["Repositories"])


@repo_router.get("", response_model=List[RepoResponse])
async def list_repos(
    mine: bool = False,
    session: Session = Depends(get_db),
    user: Optional[User] = Depends(lambda: None),
):
    """List repositories. Use ?mine=true to see only your repos."""
    from sqlmodel import select
    from ..models.platform import Repo

    service = RepoService(session)

    # If user wants their repos, filter by created_by_user_id
    if mine and user:
        statement = select(Repo).where(
            Repo.is_active == True,
            Repo.created_by_user_id == user.id
        )
        repos = session.exec(statement).all()
    else:
        statement = select(Repo).where(Repo.is_active == True)
        repos = session.exec(statement).all()

    # Get agent context for membership check
    agent_id = None
    if user:
        try:
            agent, _ = _get_bound_agent_or_403(user, session)
            agent_id = agent.id
        except HTTPException:
            pass  # User has no bound agent, that's ok

    return [service.build_repo_response(repo, agent_id, user.id if user else None) for repo in repos]


@repo_router.post("", response_model=RepoResponse, status_code=status.HTTP_201_CREATED)
async def create_repo(
    data: CreateRepoRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """
    Register a new repository.

    Requirements:
    - User must be authenticated
    - User must have a bound Agent
    - User's Agent must be an Architect in at least one repo
    """
    from ..models.platform import Repo

    # Check if user has bound agent
    agent, auth_service = _get_bound_agent_or_403(user, session)

    # Check if user's agent is an Architect anywhere
    # (This is the permission to create new repos)
    service = RepoService(session)
    agent_repos = service.list_agent_repos(agent.id)

    is_architect = any(
        member.role == RepoRole.ARCHITECT
        for repo, member in agent_repos
    )

    if not is_architect:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Architects can create new repositories"
        )

    # Check if repo already exists
    existing = service.get_repo_by_full_name(data.full_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository {data.full_name} already exists"
        )

    # Create repo
    owner, name = data.full_name.split("/", 1)
    repo = Repo(
        full_name=data.full_name,
        name=name,
        owner=owner,
        description=data.description,
        is_private=data.is_private,
        created_by_user_id=user.id,
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)

    # Auto-join the creator as Architect
    service.join_repo(repo.id, agent.id, RepoRole.ARCHITECT, added_by_agent_id=agent.id)

    return service.build_repo_response(repo, agent.id, user)


@repo_router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(
    repo_id: UUID,
    session: Session = Depends(get_db),
    user: Optional[User] = Depends(lambda: None),
):
    """Get repository info."""
    from ..models.platform import Repo

    repo = session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    service = RepoService(session)
    agent_id = None
    user_id = user.id if user else None
    if user:
        try:
            agent, _ = _get_bound_agent_or_403(user, session)
            agent_id = agent.id
        except HTTPException:
            pass

    return service.build_repo_response(repo, agent_id, user_id)


@repo_router.post("/{repo_id}/join")
async def join_repo(
    repo_id: UUID,
    role: RepoRole = RepoRole.CONTRIBUTOR,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Join a repository with your bound Agent."""
    from ..models.platform import Repo

    agent, _ = _get_bound_agent_or_403(user, session)

    repo = session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    service = RepoService(session)
    try:
        membership = service.join_repo(repo_id, agent.id, role)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {
        "success": True,
        "repo": repo.full_name,
        "role": role.value,
        "joined_at": membership.added_at,
    }


@repo_router.post("/{repo_id}/leave")
async def leave_repo(
    repo_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Leave a repository."""
    agent, _ = _get_bound_agent_or_403(user, session)

    service = RepoService(session)
    success = service.leave_repo(repo_id, agent.id)

    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a member")

    return {"success": True}


@repo_router.post("/{repo_id}/kick/{target_agent_id}")
async def kick_member(
    repo_id: UUID,
    target_agent_id: UUID,
    data: KickMemberRequest = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    """Kick a member (Architect only)."""
    agent, _ = _get_bound_agent_or_403(user, session)

    service = RepoService(session)
    reason = data.reason if data else None
    success, message = service.kick_member(repo_id, target_agent_id, agent.id, reason)

    if not success:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)

    return {"success": True, "message": message}


@repo_router.get("/{repo_id}/members", response_model=List[RepoMemberResponse])
async def list_repo_members(
    repo_id: UUID,
    status: MembershipStatus = MembershipStatus.ACTIVE,
    session: Session = Depends(get_db),
):
    """List members of a repository."""
    from ..models.platform import Repo
    from ..models import Agent

    repo = session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    service = RepoService(session)
    members = service.list_repo_members(repo_id, status)

    result = []
    for m in members:
        agent = session.get(Agent, m.agent_id)
        if agent:
            result.append(RepoMemberResponse(
                agent_id=agent.id,
                agent_name=agent.name,
                role=m.role,
                status=m.status,
                joined_at=m.added_at,
            ))

    return result


# Combine routers
platform_router = APIRouter()
platform_router.include_router(auth_router)
platform_router.include_router(repo_router)
