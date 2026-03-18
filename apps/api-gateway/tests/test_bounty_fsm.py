import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlmodel import SQLModel, Session, create_engine

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from persistence import Bounty, BountyStatus  # noqa: E402
from agent_auth.services.bounty_fsm import transition  # noqa: E402


@pytest.fixture()
def session():
    # In-memory SQLite for FSM unit tests
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def create_bounty(s: Session, title: str = "T", status: str = BountyStatus.OPEN.value, **kwargs) -> Bounty:
    b = Bounty(
        title=title,
        description="",
        reward=1,
        repo_name="owner/repo",
        required_role="contributor",  # stored as string in model; DB CHECK enforces allowed set
        status=status,
        dependencies=kwargs.pop("dependencies", []),
        assignee=kwargs.pop("assignee", None),
        is_temporary_claim=kwargs.pop("is_temporary_claim", False),
        claim_expires_at=kwargs.pop("claim_expires_at", None),
        test_command="pytest",
        verification_mode="auto",
    )
    s.add(b)
    s.commit()
    s.refresh(b)
    return b


def test_pending_to_open_requires_completed_deps(session: Session):
    dep = create_bounty(session, title="dep", status=BountyStatus.IN_PROGRESS.value)
    b = create_bounty(session, status=BountyStatus.PENDING.value, dependencies=[dep.id])

    updated, err = transition(session, b.id, BountyStatus.OPEN.value)
    assert err == "Dependencies are not all completed"

    # Complete dep and retry
    dep.status = BountyStatus.COMPLETED.value
    session.add(dep)
    session.commit()
    updated, err = transition(session, b.id, BountyStatus.OPEN.value)
    assert err is None
    assert updated.status == BountyStatus.OPEN.value


def test_open_to_in_progress_claim_is_atomic(session: Session):
    b = create_bounty(session, status=BountyStatus.OPEN.value)

    # First claim succeeds
    u1, e1 = transition(session, b.id, BountyStatus.IN_PROGRESS.value, ctx={"agent_id": "a1"})
    assert e1 is None and u1.status == BountyStatus.IN_PROGRESS.value and u1.assignee == "a1"

    # Second claim fails due to race
    u2, e2 = transition(session, b.id, BountyStatus.IN_PROGRESS.value, ctx={"agent_id": "a2"})
    assert u2 is None and "Race" in e2


def test_ready_for_preparation_transitions(session: Session):
    # No preparer -> OPEN
    d = create_bounty(session, title="dep", status=BountyStatus.COMPLETED.value)
    b1 = create_bounty(session, status=BountyStatus.READY_FOR_PREPARATION.value, dependencies=[d.id])
    u1, e1 = transition(session, b1.id, BountyStatus.OPEN.value)
    assert e1 is None and u1.status == BountyStatus.OPEN.value

    # With preparer -> IN_PROGRESS
    b2 = create_bounty(session, status=BountyStatus.READY_FOR_PREPARATION.value, dependencies=[d.id], assignee="p1")
    u2, e2 = transition(session, b2.id, BountyStatus.IN_PROGRESS.value)
    assert e2 is None and u2.status == BountyStatus.IN_PROGRESS.value


def test_submit_and_revert(session: Session):
    # in_progress -> submitted (must be assignee)
    b = create_bounty(session, status=BountyStatus.IN_PROGRESS.value, assignee="dev")
    u1, e1 = transition(session, b.id, BountyStatus.SUBMITTED.value, ctx={"agent_id": "dev"})
    assert e1 is None and u1.status == BountyStatus.SUBMITTED.value

    # submitted -> in_progress (e.g., blackbox fail)
    u2, e2 = transition(session, b.id, BountyStatus.IN_PROGRESS.value)
    assert e2 is None and u2.status == BountyStatus.IN_PROGRESS.value


def test_temporary_claim_create_and_cleanup(session: Session):
    # Create temp claim (open -> in_progress) then cleanup back to open
    b = create_bounty(session, status=BountyStatus.OPEN.value)

    u1, e1 = transition(session, b.id, BountyStatus.IN_PROGRESS.value, ctx={"agent_id": "temp"})
    assert e1 is None and u1.status == BountyStatus.IN_PROGRESS.value

    # Simulate flags for temporary claim
    u1.is_temporary_claim = True
    u1.claim_expires_at = datetime.utcnow() - timedelta(hours=1)
    session.add(u1)
    session.commit()

    # Cleanup path calls FSM with IN_PROGRESS -> OPEN (only for temp claims)
    u2, e2 = transition(session, b.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
    assert (e2 is None and u2.status == BountyStatus.OPEN.value) or (
        # If FSM enforces temp-flag check strictly, then allow only when temp flag is set
        # We already set is_temporary_claim=True, so expect success
        False
    )
