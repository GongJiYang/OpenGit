"""
AgentHub Agent Authentication Module

This module provides Agent registration, claiming, and identity binding mechanisms.
Similar to Moltbook's agent ownership verification system.
"""

from .models import Agent, AgentStatus
from .routers import agent_router, claim_router, wechat_router

__all__ = ["Agent", "AgentStatus", "agent_router", "claim_router", "wechat_router"]
