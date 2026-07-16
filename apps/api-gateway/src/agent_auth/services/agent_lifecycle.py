"""
AgentLifecycleService - Agent soft-delete and lifecycle management.

Handles permanent deletion of Agents:
- Sets status to DELETED (terminal state)
- Atomically suspends all ACTIVE RepoMember records
- Idempotent: safe to call on already-deleted agents
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from ..models import Agent, AgentStatus
from ..models.platform import RepoMember, MembershipStatus

KICK_REASON_AGENT_DELETED = "agent_deleted"


class AgentLifecycleService:
    """Service for managing Agent lifecycle, including permanent deletion."""

    def __init__(self, session: Session):
        self.session = session

    def delete_agent(self, agent: Agent, deleted_by: str = "self") -> dict:
        """
        Permanently delete an Agent (soft-delete).

        Sets agent.status = DELETED and atomically suspends all ACTIVE
        RepoMember records for that agent.

        Args:
            agent: The Agent to delete.
            deleted_by: Who initiated the deletion. Allowed: 'self', 'admin'.

        Returns:
            dict with keys:
              - success (bool)
              - reason (str, only when success=False)
              - deleted_at (datetime, only when success=True)
              - memberships_suspended (int, only when success=True)

        Correctness properties:
        - P4: DELETED is terminal — no further status transitions allowed
        - P5: After deletion, no ACTIVE RepoMember records remain
        - P6: Idempotent — calling on already-DELETED agent returns success=False
        """
        # P6: Idempotent guard
        if agent.status == AgentStatus.DELETED:
            return {"success": False, "reason": "already_deleted"}

        now = datetime.utcnow()

        # Step 1: Mark agent as DELETED
        agent.status = AgentStatus.DELETED
        agent.deleted_at = now
        agent.deleted_by = deleted_by
        self.session.add(agent)

        # Step 2: Batch-suspend all ACTIVE RepoMember records
        # Loop invariant: each processed membership is SUSPENDED after iteration
        active_memberships = self.session.exec(
            select(RepoMember).where(
                RepoMember.agent_id == agent.id,
                RepoMember.status == MembershipStatus.ACTIVE,
            )
        ).all()

        for membership in active_memberships:
            membership.status = MembershipStatus.SUSPENDED
            membership.kicked_at = now
            membership.kick_reason = KICK_REASON_AGENT_DELETED
            self.session.add(membership)

        # Step 3: Atomic commit
        self.session.commit()
        self.session.refresh(agent)

        return {
            "success": True,
            "deleted_at": agent.deleted_at,
            "memberships_suspended": len(active_memberships),
        }
