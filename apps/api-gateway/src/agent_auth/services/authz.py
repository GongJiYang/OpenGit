from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from sqlmodel import Session, select

from ..models import Agent
from ..models.platform import Repo, RepoMember
from agenthub_protocol.roles import RepoRole, MembershipStatus, UserRole

@dataclass
class Principal:
    id: str
    kind: str  # "agent" | "user"
    status: Optional[str] = None  # CLAIMED/SUSPENDED etc.
    role: Optional[UserRole] = None  # for user


def verify_api_key(key: str) -> bool:
    from ..utils import is_valid_api_key_format  # keep internal hidden
    if not (key and is_valid_api_key_format(key)):
        return False
    return True


def authenticate_api_key(auth_session: Session, x_api_key: str) -> Optional[Principal]:
    from ..utils import get_api_key_prefix, get_legacy_api_key_prefix, verify_api_key as _verify
    key_prefix = get_api_key_prefix(x_api_key)
    agents = auth_session.exec(select(Agent).where(Agent.api_key_prefix == key_prefix)).all()
    agent = next((a for a in agents if _verify(x_api_key, a.api_key_hash)), None)
    if agent:
        return Principal(id=str(agent.id), kind="agent", status=agent.status)
    legacy_prefix = get_legacy_api_key_prefix(x_api_key)
    if legacy_prefix != key_prefix:
        legacy_agents = auth_session.exec(select(Agent).where(Agent.api_key_prefix == legacy_prefix)).all()
        agent = next((a for a in legacy_agents if _verify(x_api_key, a.api_key_hash)), None)
        if agent:
            return Principal(id=str(agent.id), kind="agent")
    return None


def require_repo_member(auth_session: Session, repo_name: str, agent_id: str, role: RepoRole | None = None) -> bool:
    repo = auth_session.exec(select(Repo).where(Repo.full_name == repo_name)).first()
    if not repo:
        return False
    membership = auth_session.exec(
        select(RepoMember).where(
            RepoMember.repo_id == repo.id,
            RepoMember.agent_id == agent_id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
    ).first()
    if not membership:
        return False
    if role is not None:
        return membership.role == role
    return True


def start_scheduler():
    from ..services import start_scheduler as _start
    _start()


def stop_scheduler():
    from ..services import stop_scheduler as _stop
    _stop()
