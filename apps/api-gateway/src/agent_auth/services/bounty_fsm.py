"""
Centralized Bounty Finite State Machine (FSM)

All bounty.status transitions must go through this module.
- Provides atomic, condition-checked updates to prevent races
- Centralizes precondition checks (dependencies, assignee presence)
- Emits audit log records for observability

Usage:
    bounty, err = transition(session, bounty_id, to_status, ctx={...})

Context (ctx) fields (optional):
- actor_id: who triggers the transition (agent/user id)
- actor_type: "agent" | "user" | "system"
- agent_id: claimant agent id (for open->in_progress)
- ip: source IP address (if available)

Notes:
- Authorization (repo membership, roles) should be enforced by route/service layers
- FSM only handles state-related business preconditions
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, update

from persistence import Bounty, BountyStatus, AuditLog


def _deps_completed(session: Session, bounty: Bounty) -> bool:
    """Return True if all dependencies of `bounty` are completed."""
    if not bounty.dependencies:
        return True
    for dep_id in bounty.dependencies:
        dep = session.get(Bounty, dep_id)
        if not dep or dep.status != BountyStatus.COMPLETED.value:
            return False
    return True


def _audit(session: Session, bounty: Bounty, from_status: str, to_status: str, ctx: Dict[str, Any]) -> None:
    """Emit an audit log entry for a status transition."""
    try:
        entry = AuditLog(
            repo_name=bounty.repo_name,
            agent_id=str(ctx.get("actor_id", "system")),
            action="status_transition",
            target="bounty",
            detail={
                "bounty_id": bounty.id,
                "from": from_status,
                "to": to_status,
                "actor_type": ctx.get("actor_type", "agent"),
            },
            ip_address=ctx.get("ip"),
            timestamp=datetime.utcnow(),
        )
        session.add(entry)
    except Exception:
        # Do not fail transition on audit log errors
        pass


def transition(session: Session, bounty_id: str, to_status: str, ctx: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Bounty], Optional[str]]:
    """
    Perform a state transition with atomic conditional update and auditing.

    Returns: (updated_bounty, error_message)
    """
    ctx = ctx or {}

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        return None, "Bounty not found"

    from_status = bounty.status

    # Dispatch by (from_status -> to_status)
    if from_status == BountyStatus.PENDING.value and to_status == BountyStatus.READY_FOR_PREPARATION.value:
        # No special preconditions besides current status
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.PENDING.value)
            .values(status=BountyStatus.READY_FOR_PREPARATION.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    if from_status == BountyStatus.PENDING.value and to_status == BountyStatus.OPEN.value:
        # Require dependencies completed
        if not _deps_completed(session, bounty):
            return None, "Dependencies are not all completed"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.PENDING.value)
            .values(status=BountyStatus.OPEN.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    if from_status == BountyStatus.OPEN.value and to_status == BountyStatus.IN_PROGRESS.value:
        # Claim: require available, set assignee from ctx.agent_id
        agent_id = ctx.get("agent_id")
        if not agent_id:
            return None, "agent_id required for claim"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.OPEN.value, Bounty.assignee.is_(None))
            .values(status=BountyStatus.IN_PROGRESS.value, assignee=str(agent_id), updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Race detected: bounty already claimed"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    if from_status == BountyStatus.READY_FOR_PREPARATION.value and to_status == BountyStatus.OPEN.value:
        # Dependencies completed, no preparer assigned
        if not _deps_completed(session, bounty):
            return None, "Dependencies are not all completed"
        if bounty.assignee:
            return None, "Preparer present; use in_progress transition"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.READY_FOR_PREPARATION.value, Bounty.assignee.is_(None))
            .values(status=BountyStatus.OPEN.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    if from_status == BountyStatus.READY_FOR_PREPARATION.value and to_status == BountyStatus.IN_PROGRESS.value:
        # Dependencies completed, preparer exists
        if not _deps_completed(session, bounty):
            return None, "Dependencies are not all completed"
        if not bounty.assignee:
            return None, "No preparer assigned"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.READY_FOR_PREPARATION.value, Bounty.assignee.is_not(None))
            .values(status=BountyStatus.IN_PROGRESS.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    return None, f"Transition {from_status} -> {to_status} not allowed or not implemented"
