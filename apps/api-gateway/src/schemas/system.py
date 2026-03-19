from typing import Optional

from pydantic import BaseModel


class SystemStats(BaseModel):
    active_agents: int
    total_repos: int
    total_vectors: int
    system_load: str


class MemoryStatusResponse(BaseModel):
    enabled: bool
    disabled_reason: Optional[str] = None
    provider: str
    collection_name: str
    qdrant_mode: str
    history_db_path: str
    qdrant_path: Optional[str] = None
