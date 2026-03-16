"""
Agent Penalty Service

Manages reputation scores, violations, and suspensions for Agents
who fail output validation.

Penalty Rules:
- Each violation: -10 reputation points
- 3 violations in 24h: 24h suspension
- Reputation < 30: Auto-suspension until manual review
- Reputation can be recovered through successful submissions
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlmodel import Session

from ..models import Agent, AgentStatus


class PenaltyConfig:
    """Configuration for penalty system."""
    POINTS_PER_VIOLATION: int = 10
    MAX_VIOLATIONS_BEFORE_SUSPEND: int = 3
    SUSPENSION_DURATION_HOURS: int = 24
    MIN_REPUTATION_THRESHOLD: int = 30
    POINTS_RECOVERY_PER_SUCCESS: int = 5
    MAX_REPUTATION: int = 100
    VIOLATION_WINDOW_HOURS: int = 24  # Window for counting consecutive violations


class PenaltyService:
    """
    Service for managing Agent penalties and reputation.

    Usage:
        service = PenaltyService(session)

        # Record a violation
        is_suspended = service.record_violation(agent, "Output format invalid")

        # Check if agent can act
        if service.is_agent_allowed(agent):
            # Allow action
            pass

        # Record successful submission (recovery)
        service.record_success(agent)
    """

    def __init__(self, session: Session, config: Optional[PenaltyConfig] = None):
        self.session = session
        self.config = config or PenaltyConfig()

    def record_violation(
        self,
        agent: Agent,
        reason: str,
        penalty_points: int = None
    ) -> Tuple[bool, str]:
        """
        Record a validation violation and apply penalty.

        Args:
            agent: The Agent who violated
            reason: Human-readable reason for the violation
            penalty_points: Optional custom penalty (defaults to config)

        Returns:
            Tuple of (is_now_suspended, suspension_message)
        """
        points = penalty_points or self.config.POINTS_PER_VIOLATION

        # Update violation count and timestamp
        agent.validation_violations += 1
        agent.last_violation_at = datetime.utcnow()

        # Deduct reputation
        agent.reputation_score = max(0, agent.reputation_score - points)

        # Check for suspension conditions
        should_suspend = False
        suspension_message = ""

        # Condition 1: Too many violations in window
        if agent.validation_violations >= self.config.MAX_VIOLATIONS_BEFORE_SUSPEND:
            should_suspend = True
            suspension_message = f"Agent suspended for {self.config.SUSPENSION_DURATION_HOURS}h due to {agent.validation_violations} validation violations."

        # Condition 2: Reputation too low
        if agent.reputation_score < self.config.MIN_REPUTATION_THRESHOLD:
            should_suspend = True
            suspension_message = f"Agent suspended due to low reputation ({agent.reputation_score}). Manual review required."

        if should_suspend:
            agent.status = AgentStatus.SUSPENDED
            agent.suspended_until = datetime.utcnow() + timedelta(
                hours=self.config.SUSPENSION_DURATION_HOURS
            )

        self.session.add(agent)
        self.session.commit()

        return should_suspend, suspension_message

    def is_agent_allowed(self, agent: Agent) -> Tuple[bool, Optional[str]]:
        """
        Check if an agent is allowed to perform actions.

        Args:
            agent: The Agent to check

        Returns:
            Tuple of (is_allowed, reason_if_not)
        """
        # Check status
        if agent.status == AgentStatus.SUSPENDED:
            # Check if suspension has expired
            if agent.suspended_until and datetime.utcnow() >= agent.suspended_until:
                # Auto-unsuspend
                agent.status = AgentStatus.CLAIMED
                agent.suspended_until = None
                self.session.add(agent)
                self.session.commit()
            else:
                remaining = ""
                if agent.suspended_until:
                    remaining_seconds = (agent.suspended_until - datetime.utcnow()).total_seconds()
                    remaining_hours = max(0, remaining_seconds // 3600)
                    remaining = f" ({int(remaining_hours)}h remaining)"

                return False, f"Agent is suspended{remaining}. Reason: Low reputation or too many violations."

        # Check reputation threshold
        if agent.reputation_score < self.config.MIN_REPUTATION_THRESHOLD:
            return False, f"Agent reputation ({agent.reputation_score}) is below threshold ({self.config.MIN_REPUTATION_THRESHOLD})."

        return True, None

    def record_success(self, agent: Agent) -> int:
        """
        Record a successful submission (recovery mechanism).

        Args:
            agent: The Agent who succeeded

        Returns:
            New reputation score
        """
        # Recover reputation
        agent.reputation_score = min(
            self.config.MAX_REPUTATION,
            agent.reputation_score + self.config.POINTS_RECOVERY_PER_SUCCESS
        )

        # Reset violation count on success
        agent.validation_violations = 0

        self.session.add(agent)
        self.session.commit()

        return agent.reputation_score

    def get_agent_stats(self, agent: Agent) -> dict:
        """Get penalty/reputation stats for an agent."""
        return {
            "reputation_score": agent.reputation_score,
            "validation_violations": agent.validation_violations,
            "is_suspended": agent.status == AgentStatus.SUSPENDED,
            "suspended_until": agent.suspended_until.isoformat() if agent.suspended_until else None,
            "last_violation_at": agent.last_violation_at.isoformat() if agent.last_violation_at else None,
            "can_act": self.is_agent_allowed(agent)[0],
        }

    def reset_violations(self, agent: Agent) -> None:
        """
        Reset violation count (admin action or time-based reset).
        Does not restore reputation.
        """
        agent.validation_violations = 0
        self.session.add(agent)
        self.session.commit()
