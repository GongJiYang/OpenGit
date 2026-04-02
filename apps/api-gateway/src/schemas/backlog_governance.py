from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class BacklogStartRequest(BaseModel):
    repo_name: str = Field(..., min_length=1)
    args: Dict[str, Any] = Field(default_factory=dict)
    mode: Optional[str] = "sync"
    description: Optional[str] = None


class BacklogEnvelope(BaseModel):
    ok: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    job: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    next_suggested_action: Optional[str] = None
    description: Optional[str] = None
