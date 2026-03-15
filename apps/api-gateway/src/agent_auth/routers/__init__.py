"""
Agent Authentication Routers

FastAPI routers for agent registration, status, heartbeat, and claiming.
"""

from .agent import router as agent_router
from .claim import router as claim_router
from .oauth import router as oauth_router
from .wechat import router as wechat_router
from .assignment import router as assignment_router
from .collaboration import router as collaboration_router
from .recovery import router as recovery_router

__all__ = [
    "agent_router",
    "claim_router",
    "oauth_router",
    "wechat_router",
    "assignment_router",
    "collaboration_router",
    "recovery_router",
]
