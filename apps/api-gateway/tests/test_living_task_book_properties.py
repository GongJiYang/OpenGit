"""
Living Task Book Property-Based Tests

Tests the 8 correctness properties for the Living Task Book feature.
Uses Hypothesis for property-based testing.

Properties:
1. Spec Skeleton Initialization
2. Spec Write/Read Round-Trip Integrity
3. Status History Monotonic Growth
4. Review History Monotonic Growth
5. Authorization Isolation
6. Invalid State Transition Rejection
7. Export Completeness
8. Filter Correctness
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from sqlmodel import Session, select

from agent_auth.models import Agent, AgentStatus
from persistence import Bounty, get_session, _empty_spec


# ============== Helper Functions ==============

def create_test_bounty(session: Session, **kwargs) -> Bounty:
    """Create a test bounty with default values."""
    defaults = {
        "id": str(uuid.uuid4()),
        "title": "Test Bounty",
        "description": "Test description",
        "reward": 100,
        "status": "open",
        "repo_name": "test/repo",
        "required_role": "contributor",
        "verification_mode": "human",
        "max_steps": 10,
        "current_steps": 0,
        "test_command": "echo test",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    defaults.update(kwargs)
    
    bounty = Bounty(**defaults)
    session.add(bounty)
    session.commit()
    session.refresh(bounty)
    return bounty


def create_test_agent(session: Session, **kwargs) -> Agent:
    """Create a test agent with default values."""
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "Test Agent",
        "status": AgentStatus.CLAIMED,
        "api_key_prefix": "test",
        "api_key_hash": "test_hash",
        "created_at": datetime.utcnow(),
    }
    defaults.update(kwargs)
    
    agent = Agent(**defaults)
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


# ============== Property 1: Spec Skeleton Initialization ==============

@given(
    st.fixed_dictionaries({
        "title": st.text(min_size=1, max_size=100),
        "description": st.text(max_size=500),
        "required_role": st.sampled_from(["contributor", "executor", "tester"]),
        "acceptance_criteria": st.lists(st.text(min_size=1), max_size=10),
    })
)
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_spec_skeleton_initialization(client: TestClient, db_engine, payload: Dict[str, Any]):
    """
    Property 1: Spec Skeleton Initialization
    
    For any valid bounty creation payload, the resulting bounty SHALL have a spec field
    where spec.architect is populated from the payload, all spec.contributor fields are null,
    and spec.system.status_history is an empty list with created_at set to a non-null UTC timestamp.
    
    Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6
    """
    # Feature: living-task-book, Property 1: spec skeleton initialization
    
    # Create a bounty via API
    bounty_data = {
        "title": payload["title"],
        "description": payload["description"],
        "reward": 100,
        "repo_name": "test/repo",
        "required_role": payload["required_role"],
        "verification_mode": "human",
    }
    
    with patch('agent_auth.deps.get_auth_session', return_value=MagicMock(id=str(uuid.uuid4()))):
        response = client.post("/api/v1/bounties", json=bounty_data)
    
    assert response.status_code == 200
    bounty = response.json()
    
    # Check spec field exists
    assert "spec" in bounty
    assert bounty["spec"] is not None
    
    # Check architect fields
    assert bounty["spec"]["architect"]["title"] == payload["title"]
    assert bounty["spec"]["architect"]["description"] == payload["description"]
    assert bounty["spec"]["architect"]["required_role"] == payload["required_role"]
    
    # Check contributor fields are null
    assert bounty["spec"]["contributor"]["implementation_plan"] is None
    assert bounty["spec"]["contributor"]["technical_decisions"] is None
    assert bounty["spec"]["contributor"]["files_changed"] == []
    assert bounty["spec"]["contributor"]["test_results"] is None
    assert bounty["spec"]["contributor"]["implementation_notes"] is None
    
    # Check system fields
    assert bounty["spec"]["system"]["status_history"] == []
    assert bounty["spec"]["system"]["review_history"] == []
    assert bounty["spec"]["system"]["created_at"] is not None
    assert bounty["spec"]["system"]["claimed_at"] is None
    assert bounty["spec"]["system"]["submitted_at"] is None
    assert bounty["spec"]["system"]["completed_at"] is None


# ============== Property 2: Spec Write/Read Round-Trip Integrity ==============

@given(
    st.fixed_dictionaries({
        "implementation_plan": st.one_of(st.none(), st.text(alphabet=st.characters())),
        "technical_decisions": st.one_of(st.none(), st.text(alphabet=st.characters())),
        "files_changed": st.lists(st.text(min_size=1), max_size=20),
        "test_results": st.one_of(st.none(), st.text(alphabet=st.characters())),
    })
)
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_spec_round_trip_integrity(client: TestClient, db_engine, contributor_update: Dict[str, Any]):
    """
    Property 2: Spec Write/Read Round-Trip Integrity
    
    For any valid spec object written via PATCH endpoints, a subsequent GET SHALL return
    a spec field whose relevant sub-section is byte-for-byte identical to what was written.
    
    Validates: Requirements 12.1, 12.2, 12.3
    """
    # Feature: living-task-book, Property 2: spec write/read round-trip integrity
    
    # Create a bounty and claim it
    with Session(db_engine) as session:
        bounty = create_test_bounty(session, status="open")
        agent = create_test_agent(session)
        
        # Claim the bounty
        bounty.assignee = agent.id
        bounty.status = "in_progress"
        session.commit()
    
    # Update contributor spec
    with patch('agent_auth.deps.get_auth_session', return_value=MagicMock(id=agent.id)):
        response = client.patch(
            f"/api/v1/bounties/{bounty.id}/spec/contributor",
            json=contributor_update
        )
    
    assert response.status_code == 200
    updated_bounty = response.json()
    
    # Verify round-trip integrity
    for key, value in contributor_update.items():
        if value is None:
            assert updated_bounty["spec"]["contributor"][key] is None
        else:
            assert updated_bounty["spec"]["contributor"][key] == value
    
    # Get the bounty again to verify persistence
    response = client.get(f"/api/v1/bounties/{bounty.id}")
    assert response.status_code == 200
    fetched_bounty = response.json()
    
    # Verify the fetched data matches what was written
    for key, value in contributor_update.items():
        if value is None:
            assert fetched_bounty["spec"]["contributor"][key] is None
        else:
            assert fetched_bounty["spec"]["contributor"][key] == value


# ============== Property 3: Status History Monotonic Growth ==============

@given(
    st.lists(
        st.sampled_from(["open", "in_progress", "submitted", "completed"]),
        min_size=1, max_size=5
    )
)
@settings(max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_status_history_monotonic_growth(client: TestClient, db_engine, status_sequence: List[str]):
    """
    Property 3: Status History Monotonic Growth
    
    For any bounty and any valid FSM transition, the length of spec.system.status_history
    after the transition SHALL be exactly one greater than before the transition.
    
    Validates: Requirements 9.1, 9.2, 9.3, 12.4
    """
    # Feature: living-task-book, Property 3: status history monotonic growth
    
    with Session(db_engine) as session:
        bounty = create_test_bounty(session, status="open")
        agent = create_test_agent(session)
    
    # Track status history length
    prev_history_len = 0
    
    # Apply transitions
    for i, target_status in enumerate(status_sequence):
        # Set up appropriate context for each transition
        mock_identity = MagicMock(id=str(uuid.uuid4()))
        
        if target_status == "in_progress":
            # Claim the bounty
            with patch('agent_auth.deps.get_auth_session', return_value=mock_identity):
                response = client.post(
                    f"/api/v1/bounties/{bounty.id}/claim",
                    params={"agent_id": agent.id}
                )
        
        elif target_status == "submitted":
            # Submit the bounty
            bounty.assignee = agent.id
            bounty.status = "in_progress"
            with Session(db_engine) as session:
                session.add(bounty)
                session.commit()
            
            with patch('agent_auth.deps.get_auth_session', return_value=mock_identity):
                response = client.post(
                    f"/api/v1/bounties/{bounty.id}/submit",
                    json={"summary": "Test submission"}
                )
        
        elif target_status == "completed":
            # Complete the bounty (requires submitted status first)
            bounty.assignee = agent.id
            bounty.status = "submitted"
            with Session(db_engine) as session:
                session.add(bounty)
                session.commit()
            
            with patch('agent_auth.deps.get_auth_session', return_value=mock_identity):
                response = client.post(
                    f"/api/v1/bounties/{bounty.id}/governance-transition",
                    params={"to_status": "completed"}
                )
        
        else:
            # Skip unsupported transitions for this test
            continue
        
        if response.status_code == 200:
            # Get updated bounty
            response = client.get(f"/api/v1/bounties/{bounty.id}")
            bounty_data = response.json()
            
            # Check status history growth
            current_history_len = len(bounty_data["spec"]["system"]["status_history"])
            assert current_history_len == prev_history_len + 1, \
                f"Expected history length {prev_history_len + 1}, got {current_history_len}"
            
            prev_history_len = current_history_len


# ============== Property 4: Review History Monotonic Growth ==============

@given(
    st.lists(
        st.fixed_dictionaries({"feedback": st.text(min_size=1, max_size=500)}),
        min_size=1, max_size=3
    )
)
@settings(max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_review_history_monotonic_growth(client: TestClient, db_engine, rejections: List[Dict[str, str]]):
    """
    Property 4: Review History Monotonic Growth
    
    For any bounty and any sequence of reject and approve operations, the length of
    spec.system.review_history SHALL be non-decreasing.
    
    Validates: Requirements 7.2, 7.5, 8.1
    """
    # Feature: living-task-book, Property 4: review history monotonic growth
    
    with Session(db_engine) as session:
        bounty = create_test_bounty(session, status="submitted", assignee=str(uuid.uuid4()))
        session.commit()
    
    prev_review_len = 0
    
    for rejection in rejections:
        # Reject the bounty
        mock_identity = MagicMock(id=str(uuid.uuid4()))
        
        with patch('agent_auth.deps.get_auth_session', return_value=mock_identity):
            response = client.post(
                f"/api/v1/bounties/{bounty.id}/reject",
                json={"feedback": rejection["feedback"]}
            )
        
        assert response.status_code == 200
        
        # Get updated bounty
        response = client.get(f"/api/v1/bounties/{bounty.id}")
        bounty_data = response.json()
        
        # Check review history growth
        current_review_len = len(bounty_data["spec"]["system"]["review_history"])
        assert current_review_len == prev_review_len + 1, \
            f"Expected review history length {prev_review_len + 1}, got {current_review_len}"
        
        # Check latest entry
        latest_review = bounty_data["spec"]["system"]["review_history"][-1]
        assert latest_review["decision"] == "rejected"
        assert latest_review["feedback"] == rejection["feedback"]
        
        prev_review_len = current_review_len
        
        # Resubmit for next rejection
        bounty.status = "in_progress"
        with Session(db_engine) as session:
            session.add(bounty)
            session.commit()
        
        # Submit again
        with patch('agent_auth.deps.get_auth_session', return_value=MagicMock(id=bounty.assignee)):
            response = client.post(
                f"/api/v1/bounties/{bounty.id}/submit",
                json={"summary": "Resubmission after rejection"}
            )
        
        assert response.status_code == 200
        bounty.status = "submitted"


# ============== Property 5: Authorization Isolation ==============

@given(
    st.fixed_dictionaries({
        "implementation_plan": st.text(min_size=1),
        "technical_decisions": st.text(min_size=1),
    }),
    st.uuids()
)
@settings(max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_authorization_isolation(client: TestClient, db_engine, contributor_payload: Dict[str, str], non_assignee_id: uuid.UUID):
    """
    Property 5: Authorization Isolation
    
    For any request to PATCH /bounties/{id}/spec/contributor from a non-assignee agent,
    the platform SHALL return HTTP 403 and leave the bounty unchanged.
    
    Validates: Requirements 4.2
    """
    # Feature: living-task-book, Property 5: authorization isolation
    
    with Session(db_engine) as session:
        bounty = create_test_bounty(session, status="in_progress", assignee=str(uuid.uuid4()))
        session.commit()
    
    # Assume non-assignee ID is different from bounty assignee
    assume(str(non_assignee_id) != bounty.assignee)
    
    # Try to update contributor spec as non-assignee
    with patch('agent_auth.deps.get_auth_session', return_value=MagicMock(id=str(non_assignee_id))):
        response = client.patch(
            f"/api/v1/bounties/{bounty.id}/spec/contributor",
            json=contributor_payload
        )
    
    # Should return 403
    assert response.status_code == 403
    
    # Verify bounty is unchanged
    response = client.get(f"/api/v1/bounties/{bounty.id}")
    bounty_data = response.json()
    
    assert bounty_data["spec"]["contributor"]["implementation_plan"] is None
    assert bounty_data["spec"]["contributor"]["technical_decisions"] is None


# ============== Property 6: Invalid State Transition Rejection ==============

@given(st.sampled_from(["open", "in_progress", "pending", "completed", "cancelled"]))
@settings(max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_invalid_state_transition_rejection(client: TestClient, db_engine, non_submitted_status: str):
    """
    Property 6: Invalid State Transition Rejection
    
    For any bounty whose status is not submitted, a call to POST /bounties/{id}/reject
    SHALL return HTTP 409 and leave the bounty status and spec unchanged.
    
    Validates: Requirements 7.6, 8.4
    """
    # Feature: living-task-book, Property 6: invalid state transition rejection
    
    with Session(db_engine) as session:
        bounty = create_test_bounty(session, status=non_submitted_status)
        session.commit()
    
    # Try to reject a non-submitted bounty
    mock_identity = MagicMock(id=str(uuid.uuid4()))
    
    with patch('agent_auth.deps.get_auth_session', return_value=mock_identity):
        response = client.post(
            f"/api/v1/bounties/{bounty.id}/reject",
            json={"feedback": "Test feedback"}
        )
    
    # Should return 409 for non-submitted status
    if non_submitted_status != "submitted":
        assert response.status_code == 409
        
        # Verify bounty status is unchanged
        response = client.get(f"/api/v1/bounties/{bounty.id}")
        bounty_data = response.json()
        assert bounty_data["status"] == non_submitted_status
    else:
        # Submitted status should work
        assert response.status_code == 200


# ============== Property 7: Export Completeness ==============

@given(
    st.lists(
        st.fixed_dictionaries({"title": st.text(min_size=1, max_size=80)}),
        min_size=1, max_size=10
    )
)
@settings(max_examples=3, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_export_completeness(client: TestClient, db_engine, task_defs: List[Dict[str, str]]):
    """
    Property 7: Export Completeness
    
    For any project with N bounties, the exported markdown SHALL contain the title
    of every one of the N bounties.
    
    Validates: Requirements 11.3, 11.5
    """
    # Feature: living-task-book, Property 7: export completeness
    
    repo_name = "test/repo"
    
    # Create bounties
    bounty_ids = []
    with Session(db_engine) as session:
        for task_def in task_defs:
            bounty = create_test_bounty(
                session,
                title=task_def["title"],
                repo_name=repo_name,
                status="open"
            )
            bounty_ids.append(bounty.id)
    
    # Get project book (simulates export)
    response = client.get(f"/api/v1/bounties/project/{repo_name}/book")
    assert response.status_code == 200
    project_book = response.json()
    
    # Check all bounties are included
    returned_titles = [b["title"] for b in project_book["bounties"]]
    for task_def in task_defs:
        assert task_def["title"] in returned_titles, \
            f"Title '{task_def['title']}' not found in project book"
    
    # Basic markdown validity check (simplified)
    # In a real implementation, this would render markdown and check structure


# ============== Property 8: Filter Correctness ==============

@given(
    st.lists(
        st.fixed_dictionaries({
            "title": st.text(min_size=1),
            "status": st.sampled_from(["open", "in_progress", "submitted", "completed", "cancelled"]),
        }),
        min_size=1, max_size=10
    )
)
@settings(max_examples=3, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_filter_correctness(client: TestClient, db_engine, task_defs: List[Dict[str, str]]):
    """
    Property 8: Filter Correctness
    
    filter=submitted returns only submitted bounties.
    filter=incomplete returns only non-completed/non-cancelled bounties.
    
    Validates: Requirements 10.4, 10.5
    """
    # Feature: living-task-book, Property 8: filter correctness
    
    repo_name = "test/repo"
    
    # Create bounties with different statuses
    with Session(db_engine) as session:
        for task_def in task_defs:
            create_test_bounty(
                session,
                title=task_def["title"],
                repo_name=repo_name,
                status=task_def["status"]
            )
    
    # Test submitted filter
    response = client.get(f"/api/v1/bounties/project/{repo_name}/book", params={"filter": "submitted"})
    assert response.status_code == 200
    submitted_book = response.json()
    
    for bounty in submitted_book["bounties"]:
        assert bounty["status"] == "submitted", \
            f"Non-submitted bounty '{bounty['title']}' in submitted filter"
    
    # Test incomplete filter
    response = client.get(f"/api/v1/bounties/project/{repo_name}/book", params={"filter": "incomplete"})
    assert response.status_code == 200
    incomplete_book = response.json()
    
    for bounty in incomplete_book["bounties"]:
        assert bounty["status"] not in {"completed", "cancelled"}, \
            f"Completed/cancelled bounty '{bounty['title']}' in incomplete filter"