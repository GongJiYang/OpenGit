from typing import Optional
from sqlmodel import Session, select
from .models import Agent, AgentStatus
from datetime import datetime

class AgentRepository:
    """
    Repository layer for Agent database operations.
    Follows the Repository Pattern to abstract data access.
    """
    def __init__(self, session: Session):
        self.session = session

    def get_by_claim_code(self, claim_code: str) -> Optional[Agent]:
        """Fetch an agent by its unique claim code."""
        statement = select(Agent).where(Agent.claim_code == claim_code)
        return self.session.exec(statement).first()

    def update_status_and_owner(self, agent_id: str, status: AgentStatus, owner_openid: str) -> Agent:
        """Update agent status and bind owner identity."""
        agent = self.session.get(Agent, agent_id)
        if not agent:
            raise ValueError(f"Agent with ID {agent_id} not found")

        agent.status = status
        agent.owner_wechat_openid = owner_openid
        agent.claimed_at = datetime.utcnow()
        agent.updated_at = datetime.utcnow()

        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        return agent
