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

from persistence import Bounty, BountyStatus, AuditLog, _ensure_spec



def _append_status_history(bounty: Bounty, from_status: str, to_status: str, ctx: Dict[str, Any]) -> None:
    """Append a status history entry to bounty.spec, reassigning to trigger SQLAlchemy dirty tracking."""
    _ensure_spec(bounty)
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "actor_type": ctx.get("actor_type", "system") if ctx else "system",
        "actor_id": ctx.get("actor_id", "") if ctx else "",
        "from_status": from_status,
        "to_status": to_status,
    }
    new_spec = dict(bounty.spec)
    new_spec["system"] = dict(new_spec["system"])
    new_spec["system"]["status_history"] = list(new_spec["system"]["status_history"]) + [entry]
    bounty.spec = new_spec  # reassign to trigger SQLAlchemy dirty tracking


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
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
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
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
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
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
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
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # Ready-for-preparation -> Ready-for-preparation (claim preparation assignee)
    if from_status == BountyStatus.READY_FOR_PREPARATION.value and to_status == BountyStatus.READY_FOR_PREPARATION.value:
        agent_id = ctx.get("agent_id")
        if not agent_id:
            return None, "agent_id required for preparation claim"
        stmt = (
            update(Bounty)
            .where(
                Bounty.id == bounty_id,
                Bounty.status == BountyStatus.READY_FOR_PREPARATION.value,
                Bounty.assignee.is_(None),
            )
            .values(assignee=str(agent_id), updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Race detected: bounty already claimed for preparation"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
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
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # In-progress -> Submitted (agent submits work)
    if from_status == BountyStatus.IN_PROGRESS.value and to_status == BountyStatus.SUBMITTED.value:
        agent_id = ctx.get("agent_id")
        if not agent_id or str(bounty.assignee) != str(agent_id):
            return None, "Only assignee can submit this bounty"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.IN_PROGRESS.value, Bounty.assignee == str(agent_id))
            .values(status=BountyStatus.SUBMITTED.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # Submitted -> In-progress (e.g., blackbox fail or requested changes)
    if from_status == BountyStatus.SUBMITTED.value and to_status == BountyStatus.IN_PROGRESS.value:
        # Keep assignee; just move back to in_progress
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.SUBMITTED.value)
            .values(status=BountyStatus.IN_PROGRESS.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # Submitted -> Completed (approved/accepted)
    if from_status == BountyStatus.SUBMITTED.value and to_status == BountyStatus.COMPLETED.value:
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.SUBMITTED.value)
            .values(status=BountyStatus.COMPLETED.value, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # In-progress -> Open (temporary claim expired)
    if from_status == BountyStatus.IN_PROGRESS.value and to_status == BountyStatus.OPEN.value:
        # Allow only for temporary claims via context flag
        if not bounty.is_temporary_claim:
            return None, "Only temporary claims can be released to open"
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.IN_PROGRESS.value)
            .values(status=BountyStatus.OPEN.value, updated_at=datetime.utcnow(), assignee=None)
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # Duplicate claim attempt while already in progress should surface as race
    if from_status == BountyStatus.IN_PROGRESS.value and to_status == BountyStatus.IN_PROGRESS.value:
        agent_id = ctx.get("agent_id")
        if bounty.assignee and (not agent_id or str(agent_id) != str(bounty.assignee)):
            return None, "Race detected: bounty already claimed"

    # === Cancellation and Restore ===
    # * -> CANCELLED (allowed from pending/ready/open/in_progress/submitted)
    if to_status == BountyStatus.CANCELLED.value:
        # Optional guard for in_progress/submitted (authorization outside FSM)
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status.in_([
                BountyStatus.PENDING.value,
                BountyStatus.READY_FOR_PREPARATION.value,
                BountyStatus.OPEN.value,
                BountyStatus.IN_PROGRESS.value,
                BountyStatus.SUBMITTED.value,
            ]))
            .values(status=BountyStatus.CANCELLED.value, updated_at=datetime.utcnow(), cancelled_at=datetime.utcnow(), cancelled_by=ctx.get("actor_id"), cancelled_reason=ctx.get("reason"))
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update or invalid state"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    # CANCELLED -> OPEN or PENDING depending on dependency completion
    if from_status == BountyStatus.CANCELLED.value and to_status in (BountyStatus.OPEN.value, BountyStatus.PENDING.value):
        # Decide target based on deps
        want_open = to_status == BountyStatus.OPEN.value
        deps_ok = _deps_completed(session, bounty)
        if want_open and not deps_ok:
            return None, "Dependencies are not all completed"
        if not want_open and deps_ok:
            # If deps are satisfied but asked for pending, normalize to open
            to_status = BountyStatus.OPEN.value
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id, Bounty.status == BountyStatus.CANCELLED.value)
            .values(status=to_status, updated_at=datetime.utcnow(), cancelled_at=None, cancelled_by=None, cancelled_reason=None)
            .execution_options(synchronize_session=False)
        )
        res = session.exec(stmt)
        if getattr(res, "rowcount", 0) == 0:
            return None, "Transition rejected due to concurrent update"
        session.commit()
        updated = session.get(Bounty, bounty_id)
        _append_status_history(updated, from_status, to_status, ctx)
        session.add(updated)
        _audit(session, updated, from_status, to_status, ctx)
        session.commit()
        return updated, None

    return None, f"Transition {from_status} -> {to_status} not allowed or not implemented"
