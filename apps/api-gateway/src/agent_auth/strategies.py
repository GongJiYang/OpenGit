from abc import ABC, abstractmethod
from pydantic import BaseModel
from .repository import AgentRepository
from .models import AgentStatus

class ClaimResult(BaseModel):
    """Unified result for claim operations."""
    success: bool
    message: str

class BaseClaimStrategy(ABC):
    """
    Strategy Pattern interface for Agent claiming mechanisms.
    Supports future-proof expansion (Email, GitHub, etc.)
    """
    @abstractmethod
    async def execute_claim(self, claim_code: str, user_identity: str) -> ClaimResult:
        pass

class WeChatClaimStrategy(BaseClaimStrategy):
    """
    Strategy implementation for WeChat-based claiming.
    """
    def __init__(self, repository: AgentRepository):
        self.repository = repository

    async def execute_claim(self, claim_code: str, user_identity: str) -> ClaimResult:
        # 1. Look up agent
        agent = self.repository.get_by_claim_code(claim_code)

        if not agent:
            return ClaimResult(success=False, message=f"未找到认领码 [{claim_code}] 对应的 Agent。")

        if agent.status == AgentStatus.CLAIMED:
            return ClaimResult(success=False, message="该 Agent 已被成功认领。")

        # 2. Update status and bind OpenID
        try:
            self.repository.update_status_and_owner(
                agent_id=agent.id,
                status=AgentStatus.CLAIMED,
                owner_openid=user_identity
            )
            return ClaimResult(success=True, message=f"恭喜！您已成功认领 Agent: {agent.name}")
        except Exception as e:
            return ClaimResult(success=False, message=f"系统错误: {str(e)}")
