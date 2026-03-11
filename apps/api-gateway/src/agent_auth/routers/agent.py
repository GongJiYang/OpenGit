"""
Agent Router - Agent-facing API endpoints

Endpoints for agent registration, status checking, and heartbeat.
All endpoints (except register) require API Key authentication.
"""

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlmodel import Session, select

from ..models import (
    Agent,
    AgentStatus,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentStatusResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from ..utils import (
    generate_api_key,
    hash_api_key,
    get_api_key_prefix,
    generate_claim_code,
    generate_claim_url,
    calculate_claim_expiration,
    verify_api_key,
    sanitize_agent_name,
)
from ..utils.heartbeat_cache import get_heartbeat_cache, HeartbeatCache

router = APIRouter(prefix="/api/v1/agents", tags=["Agent"])


# ============== Database Session Dependency ==============

def get_session():
    """Get database session. Override this in main.py with actual session factory."""
    from sqlmodel import create_engine, SQLModel

    engine = create_engine("sqlite:///./agenthub_data/agents.db")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


# ============== API Key Authentication ==============

async def get_current_agent(
    x_api_key: str = Header(..., description="Agent API Key"),
    session: Session = Depends(get_session)
) -> Agent:
    """
    Validate API Key and return the authenticated agent.

    Raises:
        HTTPException: 401 if API key is invalid or agent not found
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key. Include X-API-Key header."
        )

    # Get prefix for lookup
    key_prefix = get_api_key_prefix(x_api_key)

    # Find agent by prefix
    statement = select(Agent).where(Agent.api_key_prefix == key_prefix)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key."
        )

    # Verify full key against hash
    if not verify_api_key(x_api_key, agent.api_key_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key."
        )

    # Check if agent is suspended
    if agent.status == AgentStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent is suspended. Contact support."
        )

    return agent


# ============== Registration Endpoint ==============

@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new Agent",
    description="""
    Register a new AI agent and receive credentials.

    **IMPORTANT**: The returned `api_key` is shown ONLY ONCE.
    Store it securely - it cannot be retrieved later.

    The `claim_url` should be provided to your human owner for verification.
    """,
)
async def register_agent(
    request: AgentRegisterRequest,
    session: Session = Depends(get_session)
) -> AgentRegisterResponse:
    """
    Register a new agent.

    - Generates unique API key (returned once)
    - Creates claim code and URL for owner verification
    - Sets initial status to PENDING
    """
    # Sanitize inputs
    name = sanitize_agent_name(request.name)

    # Generate credentials
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    api_key_prefix = get_api_key_prefix(api_key)

    # Generate claim info
    claim_code = generate_claim_code()
    claim_url = generate_claim_url(claim_code)
    claim_expires_at = calculate_claim_expiration()

    # Serialize metadata
    metadata_json = None
    if request.metadata:
        metadata_json = json.dumps(request.metadata)

    # Create agent record
    agent = Agent(
        name=name,
        model_name=request.model_name,
        api_key_hash=api_key_hash,
        api_key_prefix=api_key_prefix,
        claim_code=claim_code,
        claim_url=claim_url,
        claim_expires_at=claim_expires_at,
        status=AgentStatus.PENDING,
        metadata_json=metadata_json,
    )

    session.add(agent)
    session.commit()
    session.refresh(agent)

    return AgentRegisterResponse(
        id=agent.id,
        name=agent.name,
        api_key=api_key,  # Only returned here!
        api_key_prefix=agent.api_key_prefix,
        claim_code=agent.claim_code,
        claim_url=agent.claim_url,
        claim_expires_at=agent.claim_expires_at,
        status=agent.status,
        created_at=agent.created_at,
    )


# ============== Status Endpoint ==============

@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="Get Agent status",
    description="Check the current status and ownership information of the authenticated agent.",
)
async def get_agent_status(
    agent: Agent = Depends(get_current_agent)
) -> AgentStatusResponse:
    """
    Get current agent status including claim information.
    """
    return AgentStatusResponse(
        id=agent.id,
        name=agent.name,
        status=agent.status,
        owner_email=agent.owner_email,
        owner_github_login=agent.owner_github_login,
        claimed_at=agent.claimed_at,
        last_heartbeat_at=agent.last_heartbeat_at,
        created_at=agent.created_at,
    )


# ============== Heartbeat Endpoint ==============

@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    summary="Send heartbeat",
    description="""
    Send a heartbeat to indicate the agent is still active.

    Recommended interval: Every 30 minutes.
    Agents that don't heartbeat for an extended period may be suspended.
    """,
)
async def send_heartbeat(
    request: HeartbeatRequest,
    agent: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session)
) -> HeartbeatResponse:
    """
    Process agent heartbeat.

    Uses in-memory cache to reduce database writes.
    """
    # Record heartbeat in memory cache
    cache = get_heartbeat_cache()
    cache.record(agent.id, request.status_message)

    # Check if we should do an immediate DB update (e.g., first heartbeat or status change)
    from ..utils import should_update_heartbeat
    if should_update_heartbeat(agent.last_heartbeat_at, min_interval_seconds=300):
        agent.last_heartbeat_at = datetime.utcnow()
        agent.heartbeat_count += 1
        session.add(agent)
        session.commit()

    return HeartbeatResponse(
        success=True,
        server_time=datetime.utcnow(),
        next_heartbeat_within_seconds=1800,  # 30 minutes
    )


# ============== Agent Info Endpoint ==============

@router.get(
    "/me",
    response_model=AgentStatusResponse,
    summary="Get current agent info",
    description="Get detailed information about the authenticated agent.",
)
async def get_current_agent_info(
    agent: Agent = Depends(get_current_agent)
) -> AgentStatusResponse:
    """Alias for /status endpoint."""
    return await get_agent_status(agent)


# ============== Regenerate Claim (if expired) ==============

@router.post(
    "/regenerate-claim",
    summary="Regenerate claim URL",
    description="Generate a new claim URL if the previous one expired.",
)
async def regenerate_claim_url(
    agent: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session)
) -> dict:
    """
    Regenerate claim code and URL for unclaimed agents.
    """
    if agent.status == AgentStatus.CLAIMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already claimed."
        )

    # Generate new claim info
    claim_code = generate_claim_code()
    claim_url = generate_claim_url(claim_code)
    claim_expires_at = calculate_claim_expiration()

    # Update agent
    agent.claim_code = claim_code
    agent.claim_url = claim_url
    agent.claim_expires_at = claim_expires_at
    agent.status = AgentStatus.PENDING

    session.add(agent)
    session.commit()
    session.refresh(agent)

    return {
        "claim_code": agent.claim_code,
        "claim_url": agent.claim_url,
        "claim_expires_at": agent.claim_expires_at,
    }
