from typing import Optional

from pydantic import BaseModel


class AgentIdentity(BaseModel):
    agent_id: str
    model_name: str


class AgentPublicInfo(BaseModel):
    """Public information about an agent (no sensitive data)."""

    id: str
    name: str
    role: str
    model_name: str
    status: str
    reputation_score: int
    validation_violations: int
    heartbeat_count: int
    last_heartbeat_at: Optional[str] = None
    owner_github_login: Optional[str] = None
    created_at: str
