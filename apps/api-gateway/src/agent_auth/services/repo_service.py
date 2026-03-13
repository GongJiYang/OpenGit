"""
Repository Membership Service

Handles:
- Repository registration
- Agent joining/leaving repos
- Role-based permissions
- Architect kick functionality
"""

from datetime import datetime
from typing import Optional, List, Tuple
from uuid import UUID

from sqlmodel import Session, select

from ..models.platform import (
    Repo,
    RepoMember,
    RepoRole,
    MembershipStatus,
    RolePermission,
    DEFAULT_PERMISSIONS,
    ROLE_HIERARCHY,
    RepoMemberResponse,
    RepoResponse,
)
from ..models import Agent, AgentStatus


class RepoService:
    """Service for repository and membership management."""

    def __init__(self, session: Session):
        self.session = session

    # ============== Repository CRUD ==============

    def get_repo_by_id(self, repo_id: UUID) -> Optional[Repo]:
        """Get repo by ID."""
        return self.session.get(Repo, repo_id)

    def get_repo_by_full_name(self, full_name: str) -> Optional[Repo]:
        """Get repo by owner/repo name."""
        statement = select(Repo).where(Repo.full_name == full_name)
        return self.session.exec(statement).first()

    def get_or_create_repo(
        self,
        full_name: str,
        github_repo_id: int = None,
        description: str = None,
        is_private: bool = False,
    ) -> Repo:
        """Get existing repo or create new one."""
        repo = self.get_repo_by_full_name(full_name)
        if repo:
            return repo

        owner, name = full_name.split("/", 1)
        repo = Repo(
            full_name=full_name,
            name=name,
            owner=owner,
            github_repo_id=github_repo_id,
            description=description,
            is_private=is_private,
        )
        self.session.add(repo)
        self.session.commit()
        self.session.refresh(repo)
        return repo

    # ============== Membership Management ==============

    def get_membership(self, repo_id: UUID, agent_id: UUID) -> Optional[RepoMember]:
        """Get membership record for an agent in a repo."""
        statement = select(RepoMember).where(
            RepoMember.repo_id == repo_id,
            RepoMember.agent_id == agent_id,
        )
        return self.session.exec(statement).first()

    def is_member(self, repo_id: UUID, agent_id: UUID) -> bool:
        """Check if agent is an active member of the repo."""
        membership = self.get_membership(repo_id, agent_id)
        return membership is not None and membership.status == MembershipStatus.ACTIVE

    def get_agent_role(self, repo_id: UUID, agent_id: UUID) -> Optional[RepoRole]:
        """Get agent's role in a repo, or None if not a member."""
        membership = self.get_membership(repo_id, agent_id)
        if membership and membership.status == MembershipStatus.ACTIVE:
            return membership.role
        return None

    def join_repo(
        self,
        repo_id: UUID,
        agent_id: UUID,
        role: RepoRole = RepoRole.CONTRIBUTOR,
        added_by_agent_id: UUID = None,
    ) -> RepoMember:
        """
        Add an agent as a member to a repo.

        Raises:
            ValueError: If agent is already a member
        """
        existing = self.get_membership(repo_id, agent_id)
        if existing:
            if existing.status == MembershipStatus.ACTIVE:
                raise ValueError("Agent is already a member of this repo")
            # Reactivate suspended member
            existing.status = MembershipStatus.ACTIVE
            existing.role = role
            existing.kicked_by_agent_id = None
            existing.kicked_at = None
            existing.kick_reason = None
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return existing

        membership = RepoMember(
            repo_id=repo_id,
            agent_id=agent_id,
            role=role,
            status=MembershipStatus.ACTIVE,
            added_by_agent_id=added_by_agent_id,
        )
        self.session.add(membership)
        self.session.commit()
        self.session.refresh(membership)
        return membership

    def leave_repo(self, repo_id: UUID, agent_id: UUID) -> bool:
        """Remove an agent from a repo."""
        membership = self.get_membership(repo_id, agent_id)
        if not membership:
            return False

        self.session.delete(membership)
        self.session.commit()
        return True

    # ============== Architect Kick Functionality ==============

    def can_kick(self, kicker_role: RepoRole, target_role: RepoRole) -> bool:
        """
        Check if a member with kicker_role can kick a member with target_role.

        Rules:
        - Only Architects can kick
        - Cannot kick members with equal or higher role
        """
        if kicker_role != RepoRole.ARCHITECT:
            return False

        kicker_level = ROLE_HIERARCHY.get(kicker_role, 0)
        target_level = ROLE_HIERARCHY.get(target_role, 0)

        return kicker_level > target_level

    def kick_member(
        self,
        repo_id: UUID,
        target_agent_id: UUID,
        kicker_agent_id: UUID,
        reason: str = None,
    ) -> Tuple[bool, str]:
        """
        Kick a member from the repository.

        Only Architects can kick, and they can only kick lower-role members.

        Returns:
            Tuple of (success, message)
        """
        # Get kicker's membership
        kicker_membership = self.get_membership(repo_id, kicker_agent_id)
        if not kicker_membership or kicker_membership.status != MembershipStatus.ACTIVE:
            return False, "You are not a member of this repo"

        # Check if kicker is Architect
        if kicker_membership.role != RepoRole.ARCHITECT:
            return False, "Only Architects can kick members"

        # Get target's membership
        target_membership = self.get_membership(repo_id, target_agent_id)
        if not target_membership:
            return False, "Target agent is not a member of this repo"

        if target_membership.status != MembershipStatus.ACTIVE:
            return False, "Target agent is not an active member"

        # Check role hierarchy
        if not self.can_kick(kicker_membership.role, target_membership.role):
            return False, f"Cannot kick a {target_membership.role.value}"

        # Perform kick
        target_membership.status = MembershipStatus.SUSPENDED
        target_membership.kicked_by_agent_id = kicker_agent_id
        target_membership.kicked_at = datetime.utcnow()
        target_membership.kick_reason = reason

        self.session.add(target_membership)
        self.session.commit()

        return True, f"Successfully kicked {target_agent_id}"

    # ============== Permission Checking ==============

    def has_permission(
        self,
        repo_id: UUID,
        agent_id: UUID,
        permission: str,
    ) -> bool:
        """
        Check if an agent has a specific permission in a repo.

        Uses role-permission mapping.
        """
        role = self.get_agent_role(repo_id, agent_id)
        if not role:
            return False

        # Check default permissions
        role_permissions = DEFAULT_PERMISSIONS.get(role, [])
        return permission in role_permissions

    def require_permission(
        self,
        repo_id: UUID,
        agent_id: UUID,
        permission: str,
    ) -> Tuple[bool, str]:
        """
        Require a permission or return error.

        Returns:
            Tuple of (has_permission, error_message)
        """
        if not self.is_member(repo_id, agent_id):
            return False, "You must be a member of this repository"

        if not self.has_permission(repo_id, agent_id, permission):
            role = self.get_agent_role(repo_id, agent_id)
            return False, f"Your role ({role.value}) does not have '{permission}' permission"

        return True, None

    # ============== Bounty Claim Check ==============

    def can_claim_bounty(self, repo_id: UUID, agent_id: UUID) -> Tuple[bool, str]:
        """
        Check if an agent can claim a bounty in this repo.

        Rules:
        - Must be an active member
        - Must have 'claim_bounty' permission
        """
        # Check membership
        if not self.is_member(repo_id, agent_id):
            return False, "You must join this repository before claiming bounties"

        # Check permission
        return self.require_permission(repo_id, agent_id, "claim_bounty")

    # ============== Listing ==============

    def list_repo_members(
        self,
        repo_id: UUID,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> List[RepoMember]:
        """List all members of a repo."""
        statement = select(RepoMember).where(
            RepoMember.repo_id == repo_id,
            RepoMember.status == status,
        )
        return list(self.session.exec(statement).all())

    def list_agent_repos(
        self,
        agent_id: UUID,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> List[Tuple[Repo, RepoMember]]:
        """List all repos an agent is a member of."""
        statement = (
            select(RepoMember, Repo)
            .join(Repo, RepoMember.repo_id == Repo.id)
            .where(
                RepoMember.agent_id == agent_id,
                RepoMember.status == status,
            )
        )
        results = self.session.exec(statement).all()
        return [(repo, member) for member, repo in results]

    # ============== Response Builders ==============

    def build_repo_response(
        self,
        repo: Repo,
        agent_id: UUID = None,
        user_id: UUID = None,
    ) -> RepoResponse:
        """Build a RepoResponse with optional member context."""
        members = self.list_repo_members(repo.id)
        member_count = len([m for m in members if m.status == MembershipStatus.ACTIVE])

        is_member = False
        your_role = None
        is_owner = False

        if agent_id:
            membership = self.get_membership(repo.id, agent_id)
            if membership and membership.status == MembershipStatus.ACTIVE:
                is_member = True
                your_role = membership.role

        if user_id and repo.created_by_user_id:
            is_owner = repo.created_by_user_id == user_id

        return RepoResponse(
            id=repo.id,
            full_name=repo.full_name,
            name=repo.name,
            owner=repo.owner,
            description=repo.description,
            member_count=member_count,
            bounty_count=0,  # TODO: Count bounties
            is_member=is_member,
            your_role=your_role,
            is_owner=is_owner,
            created_at=repo.created_at,
        )
