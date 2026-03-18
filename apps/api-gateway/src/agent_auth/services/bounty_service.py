"""
Bounty Service - Unified Bounty Management

Encapsulates all bounty-related business logic:
- Validation eligibility
- Repository resolution
- Claim management (authenticated and temporary)
- Metrics tracking for agent performance
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple
from uuid import UUID

from sqlmodel import Session, select

from ..models import Agent, AgentStatus
from ..models.platform import Repo, RepoMember, MembershipStatus
from .metrics_service import (
    increment_active_tasks,
    get_agent_workload,
)
from persistence import Bounty


# Temporary claim expiration time (1 day)
TEMPORARY_CLAIM_EXPIRATION_HOURS = 24


@dataclass
class ClaimEligibility:
    """Result of claim eligibility check."""
    is_eligible: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    bounty: Optional[Bounty] = None
    agent: Optional[Agent] = None


@dataclass
class BountyContext:
    """Context for bounty operations with resolved references."""
    bounty: Bounty
    repo: Optional[Repo] = None
    repo_member: Optional[RepoMember] = None


class BountyService:
    """
    Unified service for Bounty management.

    Deep module: simple interface hiding complex validation logic.
    """

    def __init__(self, bounty_session: Session, auth_session: Session = None):
        self.bounty_session = bounty_session
        self.auth_session = auth_session or bounty_session

    # ============== Repository Resolution ==============

    def resolve_repo(self, repo_name_or_id: str) -> Optional[Repo]:
        """
        Resolve repository by name or ID.

        Hides the complexity of looking up repos from callers.
        """
        # Try by ID first (UUID format)
        try:
            repo_id = UUID(repo_name_or_id)
            return self.auth_session.get(Repo, repo_id)
        except (ValueError, TypeError):
            pass

        # Try by full_name
        statement = select(Repo).where(Repo.full_name == repo_name_or_id)
        return self.auth_session.exec(statement).first()

    def get_or_create_repo_for_bounty(
        self,
        repo_name: str,
        github_repo_id: int = None,
        description: str = None,
    ) -> Tuple[Repo, bool]:
        """
        Get existing repo or create new one.

        Returns: (repo, was_created)
        """
        repo = self.resolve_repo(repo_name)
        if repo:
            return repo, False

        # Create new repo
        owner, name = repo_name.split("/", 1) if "/" in repo_name else ("unknown", repo_name)
        repo = Repo(
            full_name=repo_name,
            name=name,
            owner=owner,
            github_repo_id=github_repo_id,
            description=description,
        )
        self.auth_session.add(repo)
        self.auth_session.commit()
        self.auth_session.refresh(repo)
        return repo, True

    # ============== Unified Validation ==============

    def validate_claim_eligibility(
        self,
        bounty_id: str,
        agent_id: str,
    ) -> ClaimEligibility:
        """
        Validate all conditions for claiming a bounty.

        This is a DEEP method that hides all validation complexity.
        Callers get a simple result without knowing the validation steps.

        Returns: ClaimEligibility with all resolved context
        """
        # Step 1: Get Bounty
        bounty = self.bounty_session.get(Bounty, bounty_id)
        if not bounty:
            return ClaimEligibility(
                is_eligible=False,
                error_code="BOUNTY_NOT_FOUND",
                error_message="Bounty not found"
            )

        # Step 2: Check bounty status
        if bounty.status != "open":
            return ClaimEligibility(
                is_eligible=False,
                error_code="BOUNTY_NOT_OPEN",
                error_message=f"Bounty is {bounty.status}, assigned to {bounty.assignee}"
            )

        # Step 3: Get Agent
        agent = self._resolve_agent(agent_id)
        if not agent:
            return ClaimEligibility(
                is_eligible=False,
                error_code="AGENT_NOT_FOUND",
                error_message="Agent not found in registry"
            )

        # Step 4: Check agent status
        if agent.status == AgentStatus.SUSPENDED:
            return ClaimEligibility(
                is_eligible=False,
                error_code="AGENT_SUSPENDED",
                error_message="Agent is suspended"
            )

        # Step 5: Role match
        if agent.role.lower() != bounty.required_role.lower():
            return ClaimEligibility(
                is_eligible=False,
                error_code="ROLE_MISMATCH",
                error_message=f"This task requires role '{bounty.required_role}', agent has '{agent.role}'"
            )

        # Step 6: Repository membership (if repo_id exists)
        repo = None
        if bounty.repo_id:
            repo = self.auth_session.get(Repo, bounty.repo_id)
            if repo:
                membership = self._check_repo_membership(repo.id, agent.id)
                if not membership:
                    return ClaimEligibility(
                        is_eligible=False,
                        error_code="MEMBERSHIP_REQUIRED",
                        error_message=f"Must join repository '{repo.full_name}' before claiming"
                    )

        # All checks passed
        return ClaimEligibility(
            is_eligible=True,
            bounty=bounty,
            agent=agent,
        )

    def _resolve_agent(self, agent_id: str) -> Optional[Agent]:
        """Resolve agent by ID (UUID or string)."""
        # Try direct lookup
        statement = select(Agent).where(Agent.id == agent_id)
        agent = self.auth_session.exec(statement).first()

        if not agent:
            # Try UUID conversion
            try:
                agent = self.auth_session.get(Agent, UUID(agent_id))
            except (ValueError, TypeError):
                pass

        return agent

    def _check_repo_membership(self, repo_id: UUID, agent_id: UUID) -> Optional[RepoMember]:
        """Check if agent is active member of repo."""
        statement = select(RepoMember).where(
            RepoMember.repo_id == repo_id,
            RepoMember.agent_id == agent_id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
        return self.auth_session.exec(statement).first()

    # ============== Claim Operations ==============

    def claim_bounty(
        self,
        bounty_id: str,
        agent_id: str,
    ) -> Tuple[Optional[Bounty], Optional[str]]:
        """
        Claim a bounty with full validation.

        Returns: (bounty, error_message)
        """
        eligibility = self.validate_claim_eligibility(bounty_id, agent_id)

        if not eligibility.is_eligible:
            return None, eligibility.error_message

        # Check agent workload capacity
        workload = get_agent_workload(self.auth_session, eligibility.agent.id)
        if workload["availability"] <= 0:
            return None, f"Agent at full capacity ({workload['current_active_tasks']}/{workload['max_concurrent_tasks']} tasks)"

        # Perform claim
        eligibility.bounty.status = "in_progress"
        eligibility.bounty.assignee = str(agent_id)
        eligibility.bounty.updated_at = datetime.utcnow()

        self.bounty_session.add(eligibility.bounty)
        self.bounty_session.commit()
        self.bounty_session.refresh(eligibility.bounty)

        # Update agent metrics - increment active tasks
        increment_active_tasks(self.auth_session, eligibility.agent.id)

        return eligibility.bounty, None

    # ============== Temporary Claim (Unauthenticated User) ==============

    def validate_temporary_claim_eligibility(
        self,
        bounty_id: str,
    ) -> ClaimEligibility:
        """
        Validate eligibility for temporary claim (unauthenticated user).

        Restrictions:
        - Can only claim tasks with required_role = 'contributor' (not 'architect')
        - Bounty must be open

        Returns: ClaimEligibility with result
        """
        # Step 1: Get Bounty
        bounty = self.bounty_session.get(Bounty, bounty_id)
        if not bounty:
            return ClaimEligibility(
                is_eligible=False,
                error_code="BOUNTY_NOT_FOUND",
                error_message="Bounty not found"
            )

        # Step 2: Check bounty status
        if bounty.status != "open":
            return ClaimEligibility(
                is_eligible=False,
                error_code="BOUNTY_NOT_OPEN",
                error_message=f"Bounty is {bounty.status}, assigned to {bounty.assignee}"
            )

        # Step 3: Check role restriction (temporary claims only for contributor)
        role_lower = (bounty.required_role or "").lower()
        if role_lower == "architect":
            # Preserve existing behavior for architect → 401 in route layer
            return ClaimEligibility(
                is_eligible=False,
                error_code="ARCHITECT_REQUIRES_LOGIN",
                error_message="Architect role requires login. Please login to claim this bounty."
            )
        if role_lower != "contributor":
            return ClaimEligibility(
                is_eligible=False,
                error_code="TEMPORARY_CLAIM_ROLE_NOT_ALLOWED",
                error_message="Temporary claims are only allowed for 'contributor' tasks."
            )

        # All checks passed
        return ClaimEligibility(
            is_eligible=True,
            bounty=bounty,
            agent=None,
        )

    def create_temporary_claim(
        self,
        bounty_id: str,
        agent_id: str,
    ) -> Tuple[Optional[Bounty], Optional[str]]:
        """
        Create a temporary claim for unauthenticated user.

        The claim will expire in 24 hours if not converted to permanent claim.

        Returns: (bounty, error_message)
        """
        eligibility = self.validate_temporary_claim_eligibility(bounty_id)

        if not eligibility.is_eligible:
            return None, eligibility.error_message

        # Create temporary claim
        now = datetime.utcnow()
        expires_at = now + timedelta(hours=TEMPORARY_CLAIM_EXPIRATION_HOURS)

        eligibility.bounty.status = "in_progress"
        eligibility.bounty.assignee = str(agent_id)
        eligibility.bounty.is_temporary_claim = True
        eligibility.bounty.claim_expires_at = expires_at
        eligibility.bounty.updated_at = now

        self.bounty_session.add(eligibility.bounty)
        self.bounty_session.commit()
        self.bounty_session.refresh(eligibility.bounty)

        return eligibility.bounty, None

    def convert_temporary_claim_to_permanent(
        self,
        bounty_id: str,
        user_id: str,
        agent_id: str,
    ) -> Tuple[Optional[Bounty], Optional[str]]:
        """
        Convert a temporary claim to permanent (user logged in).

        Notes:
        - Temporary claims set bounty.status to 'in_progress'.
        - We MUST NOT require 'open' status during conversion.
        - We still validate agent existence/status/role and optional repo membership.

        Returns: (bounty, error_message)
        """
        bounty = self.bounty_session.get(Bounty, bounty_id)
        if not bounty:
            return None, "Bounty not found"

        if not bounty.is_temporary_claim:
            return None, "This is not a temporary claim"

        if str(bounty.assignee) != str(agent_id):
            return None, "Agent ID mismatch - this claim belongs to another agent"

        # Check expiration
        now = datetime.utcnow()
        if bounty.claim_expires_at and bounty.claim_expires_at <= now:
            return None, "Temporary claim has expired"

        # Validate agent
        agent = self._resolve_agent(agent_id)
        if not agent:
            return None, "Agent not found in registry"
        if agent.status == AgentStatus.SUSPENDED:
            return None, "Agent is suspended"

        # Role match
        if bounty.required_role and agent.role.lower() != bounty.required_role.lower():
            return None, f"This task requires role '{bounty.required_role}', agent has '{agent.role}'"

        # Optional: repository membership if repo_id present
        if bounty.repo_id:
            repo = self.auth_session.get(Repo, bounty.repo_id)
            if repo:
                membership = self._check_repo_membership(repo.id, agent.id)
                if not membership:
                    return None, f"Must join repository '{repo.full_name}' before claiming"

        # Convert to permanent claim (keep status as-is, typically 'in_progress')
        bounty.is_temporary_claim = False
        bounty.claim_expires_at = None
        bounty.claimed_by_user_id = str(user_id)
        bounty.updated_at = now

        self.bounty_session.add(bounty)
        self.bounty_session.commit()
        self.bounty_session.refresh(bounty)

        return bounty, None

    def cleanup_expired_temporary_claims(self) -> dict:
        """
        Clean up expired temporary claims.

        Called by the scheduler to release bounties that were temporarily claimed
        but not converted to permanent claims within 24 hours.

        Returns: dict with cleanup statistics
        """
        now = datetime.utcnow()

        # Find expired temporary claims
        statement = select(Bounty).where(
            Bounty.is_temporary_claim.is_(True),
            Bounty.claim_expires_at < now,
            Bounty.status == "in_progress"
        )
        expired_claims = self.bounty_session.exec(statement).all()

        released_count = 0
        for bounty in expired_claims:
            # Release the bounty back to open
            bounty.status = "open"
            bounty.assignee = None
            bounty.is_temporary_claim = False
            bounty.claim_expires_at = None
            bounty.updated_at = now
            self.bounty_session.add(bounty)
            released_count += 1

        self.bounty_session.commit()

        return {
            "released_count": released_count,
            "checked_at": now.isoformat(),
        }

    # ============== Permission Checks ==============

    def can_agent_modify_bounty(
        self,
        bounty: Bounty,
        agent_id: str,
    ) -> Tuple[bool, str]:
        """
        Check if agent can modify bounty (submit work, etc).

        Returns: (can_modify, error_message)
        """
        if not bounty.assignee:
            return False, "Bounty has no assignee"

        if str(bounty.assignee) != str(agent_id):
            return False, f"Bounty is assigned to {bounty.assignee}"

        return True, None

    # ============== Bounty Creation Helper ==============

    def create_bounty(
        self,
        title: str,
        description: str,
        repo_name: str,
        reward: int,
        required_role: str = "contributor",
        **kwargs
    ) -> Bounty:
        """
        Create a bounty with automatic repo resolution.

        This method handles the repo_name → repo_id resolution internally,
        so callers don't need to know about the dual-field complexity.
        """
        # Resolve repo
        repo, _ = self.get_or_create_repo_for_bounty(repo_name)

        # Create bounty with repo_id
        bounty = Bounty(
            title=title,
            description=description,
            repo_name=repo_name,  # Keep for backward compatibility
            repo_id=str(repo.id),  # New field
            reward=reward,
            required_role=required_role,
            **kwargs
        )

        self.bounty_session.add(bounty)
        self.bounty_session.commit()
        self.bounty_session.refresh(bounty)

        return bounty
