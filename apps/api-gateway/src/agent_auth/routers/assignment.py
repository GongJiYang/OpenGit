"""
Agent Assignment Router - Smart Task Assignment API

API endpoints for intelligent task assignment:
- Get recommendations for a task
- Auto-assign best agent
- Get agent workload status
- Manual assignment with validation
"""

from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlmodel import Session

from ..models import (
    Agent,
    AgentProfileUpdateRequest,
    AgentProfileResponse,
    AgentMetricsResponse,
    AgentRecommendation,
    TaskRecommendationResponse,
    AgentWorkloadResponse,
    AllAgentsWorkloadResponse,
)
from ..database import get_db
from ..services.metrics_service import (
    get_or_create_metrics,
    get_agent_workload,
    get_all_agents_workload,
    get_agent_metrics_detail,
)
from ..services.matching_service import (
    find_best_agent,
    get_task_match_summary,
)
from persistence import Bounty


router = APIRouter(prefix="/assignment", tags=["Assignment"])


# ============== Agent Profile Management ==============

@router.patch("/agents/{agent_id}/profile", response_model=AgentProfileResponse)
async def update_agent_profile(
    agent_id: UUID,
    req: AgentProfileUpdateRequest,
    x_api_key: str = Header(..., description="Agent API key"),
    session: Session = Depends(get_db)
):
    """
    Update agent profile (skills, preferences, capacity).

    Only the agent themselves can update their profile.
    """
    # Get agent
    agent = session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Verify API key ownership
    if not x_api_key.startswith(agent.api_key_prefix):
        raise HTTPException(status_code=403, detail="Not authorized to update this agent")

    # Update fields
    if req.skills is not None:
        agent.skills = req.skills
    if req.preferred_tracks is not None:
        agent.preferred_tracks = req.preferred_tracks
    if req.max_concurrent_tasks is not None:
        agent.max_concurrent_tasks = req.max_concurrent_tasks

    agent.updated_at = datetime.utcnow()

    session.add(agent)
    session.commit()
    session.refresh(agent)

    # Get metrics for response
    metrics = get_or_create_metrics(session, agent_id)
    workload = get_agent_workload(session, agent_id)

    return AgentProfileResponse(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        status=agent.status,
        skills=agent.skills or [],
        preferred_tracks=agent.preferred_tracks or [],
        max_concurrent_tasks=agent.max_concurrent_tasks,
        current_active_tasks=metrics.current_active_tasks,
        availability=workload["availability"],
        reliability_tier=metrics.reliability_tier,
        overall_score=metrics.overall_score,
    )


@router.get("/agents/{agent_id}/metrics", response_model=AgentMetricsResponse)
async def get_agent_metrics(
    agent_id: UUID,
    x_api_key: str = Header(..., description="Agent API key"),
    session: Session = Depends(get_db)
):
    """
    Get detailed metrics for an agent.
    """
    # Get agent
    agent = session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    metrics_detail = get_agent_metrics_detail(session, agent_id)
    if not metrics_detail:
        raise HTTPException(status_code=404, detail="Metrics not found")

    return AgentMetricsResponse(**metrics_detail)


# ============== Task Recommendations ==============

@router.get("/bounties/{bounty_id}/recommend", response_model=TaskRecommendationResponse)
async def get_task_recommendations(
    bounty_id: str,
    x_api_key: str = Header(None, alias="X-API-Key", description="Optional API key"),
    session: Session = Depends(get_db)
):
    """
    Get recommended agents for a task.

    Returns list of agents sorted by match score.
    """
    # Get bounty
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    # Get match summary
    req_role = bounty.required_role.value if hasattr(bounty.required_role, "value") else bounty.required_role
    summary = get_task_match_summary(
        session=session,
        bounty_id=bounty_id,
        bounty_title=bounty.title,
        required_role=req_role,
        track=bounty.track,
    )

    return summary


@router.post("/bounties/{bounty_id}/auto-assign", response_model=AgentRecommendation)
async def auto_assign_task(
    bounty_id: str,
    x_api_key: str = Header(None, alias="X-API-Key", description="Optional API key"),
    session: Session = Depends(get_db)
):
    """
    Automatically assign task to best matching agent.

    Returns the assigned agent info.
    """
    # Get bounty
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    if bounty.status != "open":
        raise HTTPException(
            status_code=400,
            detail=f"Bounty is not available for assignment (status: {bounty.status})"
        )

    if bounty.assignee:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty already assigned to {bounty.assignee}"
        )

    # Find best agent
    role_str = bounty.required_role.value if hasattr(bounty.required_role, "value") else bounty.required_role
    best_match = find_best_agent(
        session=session,
        required_role=role_str,
        track=bounty.track,
    )

    if not best_match:
        raise HTTPException(
            status_code=404, detail="No suitable agent available for this task")

    # Assign task via FSM
    from ..services.bounty_fsm import transition
    updated, err = transition(session, bounty.id, to_status="in_progress", ctx={"actor_type": "system", "agent_id": str(best_match["agent_id"])})
    if err:
        raise HTTPException(status_code=409, detail=err)

    return best_match


# ============== Workload Management ==============
@router.get("/agents/workload")
async def get_all_workload(
    x_api_key: str = Header(None, alias="X-API-Key", description="Optional API key"),
    session: Session = Depends(get_db)
):
    """
    Get workload status for all agents.

    Returns sorted by availability (most available first).
    """
    workloads = get_all_agents_workload(session)

    # Sort by availability (most available first)
    workloads.sort(key=lambda w: -w["availability"])
    # Calculate aggregates
    total_agents = len(workloads)
    available_agents = len([w for w in workloads if w["availability"] > 0])
    avg_availability = sum(w["availability"] for w in workloads) / len(workloads) if total_agents > 0 else 0

    return AllAgentsWorkloadResponse(
        agents=[
            AgentWorkloadResponse(
                agent_id=w["agent_id"],
                agent_name=w["agent_name"],
                role=w["role"],
                status=w["status"],
                current_active_tasks=w["current_active_tasks"],
                max_concurrent_tasks=w["max_concurrent_tasks"],
                availability=w["availability"],
                active_task_ids=[],  # TODO: Get actual task IDs
                completion_rate_7d=None,
                avg_completion_time_hours_7d=None,
            )
            for w in workloads
        ],
        total_agents=total_agents,
        available_agents=available_agents,
        avg_availability=avg_availability,
    )


@router.get("/agents/{agent_id}/workload")
async def get_agent_workload_status(
    agent_id: UUID,
    x_api_key: str = Header(None, alias="X-API-Key", description="Optional API key"),
    session: Session = Depends(get_db)
):
    """
    Get workload status for a specific agent.
    """
    workload = get_agent_workload(session, agent_id)
    if not workload:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentWorkloadResponse(**workload)
