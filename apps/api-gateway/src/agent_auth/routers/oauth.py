"""
GitHub OAuth Router

Handles GitHub OAuth flow for agent claiming.
"""

import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..models import Agent, AgentStatus
from ..utils import generate_oauth_state_token, is_claim_expired
from ..database import get_db

router = APIRouter(prefix="/api/v1/oauth", tags=["OAuth"])

# ============== Configuration ==============

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/oauth/github/callback")

# In-memory state store (use Redis in production)
_oauth_states: dict[str, dict] = {}
OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))


# ============== Database Session ==============

def get_session():
    """Get database session."""
    yield from get_db()


# ============== GitHub OAuth Endpoints ==============

@router.get(
    "/github",
    summary="Start GitHub OAuth flow",
    description="Redirect to GitHub authorization page for agent claiming.",
)
async def github_auth_start(
    claim_code: str = Query(..., description="Agent's claim code"),
    session: Session = Depends(get_session)
) -> RedirectResponse:
    """
    Initiate GitHub OAuth flow for agent claiming.

    1. Validate claim code
    2. Generate state token
    3. Redirect to GitHub authorization
    """
    # Find agent by claim code
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    # Check if already claimed
    if agent.status == AgentStatus.CLAIMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already claimed."
        )

    # Check expiration
    if is_claim_expired(agent.claim_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Claim link has expired. Please request a new one."
        )

    # Generate state token
    state_token = generate_oauth_state_token()

    # Store state with claim info
    _oauth_states[state_token] = {
        "agent_id": str(agent.id),
        "claim_code": claim_code,
        "created_at": datetime.utcnow().isoformat(),
    }

    # Build GitHub authorization URL
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state_token,
    }
    github_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    return RedirectResponse(url=github_url)


@router.get(
    "/github/callback",
    summary="GitHub OAuth callback",
    description="Handle GitHub OAuth callback and complete agent claiming.",
)
async def github_auth_callback(
    code: str = Query(..., description="GitHub authorization code"),
    state: str = Query(..., description="OAuth state token"),
    session: Session = Depends(get_session)
) -> dict:
    """
    Process GitHub OAuth callback.

    1. Validate state token
    2. Exchange code for access token
    3. Fetch user info from GitHub
    4. Complete agent claiming
    """
    # Validate state
    if state not in _oauth_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state."
        )

    state_data = _oauth_states.pop(state)
    try:
        created_at = datetime.fromisoformat(state_data.get("created_at", ""))
        if datetime.utcnow() - created_at > timedelta(seconds=OAUTH_STATE_TTL_SECONDS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth state expired."
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state."
        )

    # Get agent
    from uuid import UUID
    agent_id = UUID(state_data["agent_id"])
    statement = select(Agent).where(Agent.id == agent_id)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found."
        )

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_response.json()

        if "error" in token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub OAuth error: {token_data.get('error_description', token_data['error'])}"
            )

        access_token = token_data["access_token"]

        # Fetch user info
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        user_data = user_response.json()

        # Fetch user emails (may be private)
        emails_response = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        emails_data = emails_response.json()

    # Extract primary verified email
    primary_email = None
    if isinstance(emails_data, list):
        for email_info in emails_data:
            if email_info.get("primary") and email_info.get("verified"):
                primary_email = email_info["email"]
                break
        if not primary_email and emails_data:
            primary_email = emails_data[0].get("email")

    if not primary_email:
        primary_email = user_data.get("email")

    if not primary_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not retrieve verified email from GitHub."
        )

    # Update agent with owner info
    agent.status = AgentStatus.CLAIMED
    agent.owner_email = primary_email.lower()
    agent.owner_github_id = str(user_data.get("id", ""))
    agent.owner_github_login = user_data.get("login", "")
    agent.claimed_at = datetime.utcnow()

    session.add(agent)
    session.commit()
    session.refresh(agent)

    return {
        "success": True,
        "message": f"Agent '{agent.name}' has been successfully claimed!",
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "owner_github": agent.owner_github_login,
        "owner_email": agent.owner_email,
    }
