import sys
import os
from typing import Optional
from pydantic import BaseModel, Field

from skills.base import Skill

# Add api-gateway src to path to access MemoryService
# This assumes the skills are being run in an environment where the apps/ directory is accessible
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../apps/api-gateway/src"))

try:
    from agent_auth.services.memory_service import get_memory_service
except ImportError:
    # Fallback/Mock for environments where the service isn't available
    get_memory_service = None

class PersistentMemoryInput(BaseModel):
    """Parameters for persistent memory operations."""
    action: str = Field(..., description="Action to perform: 'add' or 'search'")
    content: str = Field(..., description="The content to remember or the query to search for")
    agent_id: str = Field(..., description="The unique ID of the agent")
    role: Optional[str] = Field(None, description="Optional role namespace for memory isolation")
    metadata: Optional[dict] = Field(None, description="Optional metadata for the memory (only for 'add')")
    scope: Optional[str] = Field(None, description="Memory scope: private, shared, or combined for search")
    shared: Optional[bool] = Field(None, description="Deprecated alias for shared scope")

class PersistentMemorySkill(Skill):
    """
    Skill for interacting with the agent's long-term persistent memory.
    Allows saving insights, failure patterns, and retrieving relevant historical experiences.
    """
    name = "persistent_memory"
    description = "Access long-term memory. Use 'add' to save insights/skills and 'search' to find relevant past experiences."
    input_schema = PersistentMemoryInput

    def execute(
        self,
        action: str,
        content: str,
        agent_id: str,
        role: Optional[str] = None,
        metadata: dict = None,
        scope: Optional[str] = None,
        shared: Optional[bool] = None,
    ) -> dict:
        if not get_memory_service:
            return {"error": "MemoryService not available in current environment"}

        service = get_memory_service()
        effective_scope = scope
        if effective_scope is None and shared:
            effective_scope = "shared"

        if action == "add":
            res = service.add_memory(agent_id, content, metadata, role=role, scope=effective_scope or "private")
            if res is None:
                return {"status": "error", "message": "Memory save failed", "result": None}
            return {"status": "success", "message": "Memory saved", "result": res}

        elif action == "search":
            memories = service.get_memories(agent_id, content, role=role, scope=effective_scope or "private")
            return {
                "status": "success",
                "count": len(memories),
                "memories": memories
            }

        else:
            return {"error": f"Unsupported action: {action}"}
