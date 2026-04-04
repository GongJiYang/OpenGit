"""
GitHub OAuth Router

Handles GitHub OAuth flow for agent claiming.
"""

import os
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import and_, update
from sqlmodel import Session, select

from core.settings import get_settings
from ..models import Agent, AgentStatus
from ..utils import generate_oauth_state_token, is_claim_expired
from ..database import get_db

router = APIRouter(prefix="/api/v1/oauth", tags=["OAuth"])

# ============== Configuration ==============

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/oauth/github/callback")

# Stateless OAuth state token TTL
OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
OAUTH_STATE_TOKEN_TYPE = "oauth_claim_state"


# ============== Database Session ==============

def get_session():
    """Get database session."""
    yield from get_db()


def _normalize_return_to(return_to: str | None) -> str | None:
    if not return_to:
        return None

    normalized = return_to.strip()
    if not normalized.startswith("/") or normalized.startswith("//"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid return_to path.",
        )

    return normalized


def _build_frontend_return_url(return_to: str | None, agent_id: str, claim_code: str) -> str | None:
    normalized_return_to = _normalize_return_to(return_to)
    if not normalized_return_to:
        return None

    settings = get_settings()
    base = settings.frontend_url.rstrip("/")
    parts = urlsplit(normalized_return_to)
    query_params = parse_qsl(parts.query, keep_blank_values=True)
    query_params = [(key, value) for key, value in query_params if key not in {"agent_id", "claim_code"}]
    query_params.extend([
        ("agent_id", agent_id),
        ("claim_code", claim_code),
    ])
    query = urlencode(query_params)
    path = urlunsplit(("", "", parts.path, query, parts.fragment))
    return f"{base}{path}"


def _encode_oauth_state(agent_id: str, claim_code: str, return_to: str | None = None) -> str:
    settings = get_settings()
    secret = settings.effective_jwt_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth state signing secret is not configured",
        )

    now_ts = int(time.time())
    payload = {
        "typ": OAUTH_STATE_TOKEN_TYPE,
        "aid": agent_id,
        "cc": claim_code,
        "iat": now_ts,
        "exp": now_ts + OAUTH_STATE_TTL_SECONDS,
        "jti": generate_oauth_state_token(),
    }
    normalized_return_to = _normalize_return_to(return_to)
    if normalized_return_to:
        payload["rt"] = normalized_return_to
    return jwt.encode(payload, secret, algorithm="HS256")


def _decode_oauth_state(state_token: str) -> dict:
    settings = get_settings()
    secret = settings.effective_jwt_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth state signing secret is not configured",
        )

    try:
        payload = jwt.decode(state_token, secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state.",
        )

    if payload.get("typ") != OAUTH_STATE_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state.",
        )

    agent_id = payload.get("aid")
    claim_code = payload.get("cc")
    if not agent_id or not claim_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state.",
        )

    return_to = payload.get("rt")
    if return_to is not None:
        _normalize_return_to(return_to)

    return payload


# ============== GitHub OAuth Endpoints ==============

@router.get(
    "/github",
    summary="Start GitHub OAuth flow",
    description="Redirect to GitHub authorization page for agent claiming.",
)
async def github_auth_start(
    claim_code: str = Query(..., description="Agent's claim code"),
    return_to: str | None = Query(default=None, description="Optional frontend path to return to after successful claim"),
    session: Session = Depends(get_session)
) -> RedirectResponse:
    """
    Initiate GitHub OAuth flow for agent claiming.

    1. Validate claim code
    2. Generate state token
    3. Redirect to GitHub authorization
    """
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    if agent.status == AgentStatus.CLAIMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already claimed."
        )

    if is_claim_expired(agent.claim_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Claim link has expired. Please request a new one."
        )

    state_token = _encode_oauth_state(str(agent.id), claim_code, return_to=return_to)

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
    response_model=None,
)
async def github_auth_callback(
    code: str = Query(..., description="GitHub authorization code"),
    state: str = Query(..., description="OAuth state token"),
    session: Session = Depends(get_session)
) -> dict | RedirectResponse:
    """
    Process GitHub OAuth callback.

    1. Validate state token
    2. Exchange code for access token
    3. Fetch user info from GitHub
    4. Complete agent claiming
    """
    state_data = _decode_oauth_state(state)

    from uuid import UUID
    agent_id = UUID(state_data["aid"])
    statement = select(Agent).where(Agent.id == agent_id)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found."
        )

    if agent.claim_code != state_data["cc"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state."
        )

    if agent.status == AgentStatus.CLAIMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already claimed."
        )

    if is_claim_expired(agent.claim_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Claim link has expired. Please request a new one."
        )

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

        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        user_data = user_response.json()

        emails_response = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        emails_data = emails_response.json()

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

    now = datetime.utcnow()
    normalized_email = primary_email.lower()
    normalized_github_id = str(user_data.get("id", ""))
    normalized_github_login = user_data.get("login", "")

    agent_update = session.exec(
        update(Agent)
        .where(
            and_(
                Agent.id == agent.id,
                Agent.claim_code == state_data["cc"],
                Agent.status != AgentStatus.CLAIMED,
                Agent.claim_expires_at >= now,
            )
        )
        .values(
            status=AgentStatus.CLAIMED,
            owner_email=normalized_email,
            owner_github_id=normalized_github_id,
            owner_github_login=normalized_github_login,
            claimed_at=now,
        )
    )

    if agent_update.rowcount != 1:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already claimed."
        )

    session.commit()

    frontend_return_url = _build_frontend_return_url(
        return_to=state_data.get("rt"),
        agent_id=str(agent.id),
        claim_code=state_data["cc"],
    )
    if frontend_return_url:
        return RedirectResponse(url=frontend_return_url, status_code=status.HTTP_302_FOUND)

    return {
        "success": True,
        "message": f"Agent '{agent.name}' has been successfully claimed!",
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "owner_github": normalized_github_login,
        "owner_email": normalized_email,
    }
