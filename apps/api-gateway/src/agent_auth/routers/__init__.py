"""
Agent Authentication Routers

FastAPI routers for agent registration, status, heartbeat, and claiming.
"""

from .agent import router as agent_router
from .claim import router as claim_router
from .oauth import router as oauth_router
from .wechat import router as wechat_router

__all__ = ["agent_router", "claim_router", "oauth_router", "wechat_router"]
