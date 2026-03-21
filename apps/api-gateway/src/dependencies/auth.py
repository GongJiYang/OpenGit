from typing import Any, Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session

from agent_auth.deps import get_auth_session
from agent_auth.models import Agent
from agent_auth.services.user_auth import UserAuthService


def require_agent(
    x_api_key: str = Header(None, alias="X-API-Key"),
    auth_session: Session = Depends(get_auth_session)
) -> Any:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")
    from agent_auth.services.authz import authenticate_api_key

    principal = authenticate_api_key(auth_session, x_api_key)
    if not principal:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key or credentials")
    if principal.status == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is suspended")
    if principal.status != "claimed":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is not claimed")

    # Enrich principal with persisted agent role for role-based checks in routers
    agent_uuid: Optional[UUID] = None
    try:
        agent_uuid = UUID(principal.id)
    except (TypeError, ValueError):
        agent_uuid = None

    if agent_uuid is not None:
        agent = auth_session.get(Agent, agent_uuid)
        if agent:
            principal.role = agent.role

    return principal


def require_active_identity(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    auth_session: Session = Depends(get_auth_session)
) -> Any:
    """
    Dependency that allows EITHER an agent (via API Key) OR a human user (via JWT).
    Returns an Agent object or a User object.
    """
    # 1. Try Agent Auth
    if x_api_key:
        try:
            return require_agent(x_api_key=x_api_key, auth_session=auth_session)
        except HTTPException:
            pass  # Continue to try User auth

    # 2. Try User Auth (JWT)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        user_auth = UserAuthService(auth_session)
        payload = user_auth.verify_token(token)
        if payload:
            user_id = payload.get("sub")
            user = user_auth.get_user_by_id(user_id)
            if user:
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid X-API-Key or Bearer Token required"
    )
