"""
Agent Metrics Service

Manages agent performance tracking and reputation scoring.

Key Features:
- Track task completion/failure rates
- Calculate quality scores from submissions
- Compute response time percentiles
- Update reliability tiers
- Provide workload status
"""

import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlmodel import Session, select

from ..models import Agent, AgentStatus, AgentMetrics


# ============== Reliability Tier Thresholds ==============

TIER_THRESHOLDS = {
    "new": {"min_score": 0, "min_tasks": 0},
    "bronze": {"min_score": 30, "min_tasks": 5},
    "silver": {"min_score": 50, "min_tasks": 15},
    "gold": {"min_score": 75, "min_tasks": 30},
    "platinum": {"min_score": 90, "min_tasks": 50},
}


# ============== Helper Functions ==============

def get_or_create_metrics(session: Session, agent_id: UUID) -> AgentMetrics:
    """Get existing metrics or create new record for agent."""
    metrics = session.exec(
        select(AgentMetrics).where(AgentMetrics.agent_id == agent_id)
    ).first()

    if not metrics:
        metrics = AgentMetrics(agent_id=agent_id)
        session.add(metrics)
        session.commit()
        session.refresh(metrics)

    return metrics


def calculate_percentile(values: List[float], percentile: int) -> Optional[float]:
    """Calculate percentile value from a list."""
    if not values:
        return None

    sorted_values = sorted(values)
    n = len(sorted_values)
    index = (percentile / 100) * (n - 1)

    lower = int(index)
    upper = lower + 1

    if upper >= n:
        return sorted_values[-1]

    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def calculate_overall_score(metrics: AgentMetrics) -> float:
    """
    Calculate overall performance score (0-100).

    Formula:
    - Completion rate: 40%
    - Quality score: 30%
    - Response time: 20%
    - First attempt success: 10%
    """
    score = 0.0

    # Completion rate (40%)
    if metrics.total_tasks_assigned > 0:
        completion_rate = metrics.total_tasks_completed / metrics.total_tasks_assigned
        score += completion_rate * 40

    # Quality score (30%)
    if metrics.avg_quality_score is not None:
        # Convert 0-5 scale to 0-30
        score += (metrics.avg_quality_score / 5) * 30

    # Response time (20%) - faster is better
    if metrics.response_time_p50_hours is not None:
        # Assume 1 hour is ideal, 24 hours is minimum score
        if metrics.response_time_p50_hours <= 1:
            score += 20
        elif metrics.response_time_p50_hours >= 24:
            score += 0
        else:
            score += 20 * (1 - (metrics.response_time_p50_hours - 1) / 23)

    # First attempt success (10%)
    if metrics.first_attempt_success_rate is not None:
        score += metrics.first_attempt_success_rate * 10

    return min(100, max(0, round(score, 2)))


def determine_reliability_tier(score: float, total_tasks: int) -> str:
    """Determine reliability tier based on score and task count."""
    # Check tiers from highest to lowest
    for tier in ["platinum", "gold", "silver", "bronze"]:
        threshold = TIER_THRESHOLDS[tier]
        if score >= threshold["min_score"] and total_tasks >= threshold["min_tasks"]:
            return tier

    return "new"


# ============== Metric Update Functions ==============

def record_task_assigned(session: Session, agent_id: UUID) -> AgentMetrics:
    """Record that a task was assigned to an agent."""
    metrics = get_or_create_metrics(session, agent_id)

    metrics.total_tasks_assigned += 1
    metrics.current_active_tasks += 1
    metrics.updated_at = datetime.utcnow()

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


def increment_active_tasks(session: Session, agent_id: UUID) -> AgentMetrics:
    """Increment active task count for an agent."""
    metrics = get_or_create_metrics(session, agent_id)

    metrics.current_active_tasks += 1
    metrics.updated_at = datetime.utcnow()

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


def decrement_active_tasks(session: Session, agent_id: UUID) -> AgentMetrics:
    """Decrement active task count for an agent."""
    metrics = get_or_create_metrics(session, agent_id)
    metrics.current_active_tasks = max(0, metrics.current_active_tasks - 1)
    metrics.updated_at = datetime.utcnow()

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


def record_task_completed(
    session: Session,
    agent_id: UUID,
    completion_time_hours: float,
    quality_score: Optional[float] = None,
    first_attempt: bool = True,
    track: Optional[str] = None,
    skills_used: Optional[List[str]] = None
) -> AgentMetrics:
    """
    Record task completion with metrics.

    Args:
        session: Database session
        agent_id: Agent UUID
        completion_time_hours: Time taken to complete the task
        quality_score: Quality score 0-5 (optional)
        first_attempt: Whether task passed on first submission
        track: Task track (backend, frontend, etc.)
        skills_used: Skills used in this task
    """
    metrics = get_or_create_metrics(session, agent_id)

    # Update basic counters
    metrics.total_tasks_completed += 1
    metrics.current_active_tasks = max(0, metrics.current_active_tasks - 1)

    # Update completion time
    metrics.total_completion_time_hours += completion_time_hours
    metrics.avg_completion_time_hours = (
        metrics.total_completion_time_hours / metrics.total_tasks_completed
    )

    # Update quality score
    if quality_score is not None:
        metrics.total_quality_score += quality_score
        metrics.avg_quality_score = (
            metrics.total_quality_score / metrics.total_tasks_completed
        )

    # Update first attempt success rate
    if first_attempt:
        current_rate = metrics.first_attempt_success_rate or 0
        n = metrics.total_tasks_completed
        metrics.first_attempt_success_rate = (
            (current_rate * (n - 1) + 1.0) / n
        )
    else:
        current_rate = metrics.first_attempt_success_rate or 1.0
        n = metrics.total_tasks_completed
        metrics.first_attempt_success_rate = (
            (current_rate * (n - 1)) / n
        )

    # Update response time tracking
    response_times = []
    if metrics.response_times_json:
        try:
            response_times = json.loads(metrics.response_times_json)
        except:
            pass
    response_times.append(completion_time_hours)

    # Keep only last 100 response times
    response_times = response_times[-100:]
    metrics.response_times_json = json.dumps(response_times)

    # Calculate percentiles
    if response_times:
        metrics.response_time_p50_hours = calculate_percentile(response_times, 50)
        metrics.response_time_p95_hours = calculate_percentile(response_times, 95)

    # Update track-specific performance
    if track:
        track_perf = {}
        if metrics.track_performance_json:
            try:
                track_perf = json.loads(metrics.track_performance_json)
            except:
                pass

        if track not in track_perf:
            track_perf[track] = {"completed": 0, "total_time": 0, "quality_sum": 0}

        track_perf[track]["completed"] += 1
        track_perf[track]["total_time"] += completion_time_hours
        if quality_score:
            track_perf[track]["quality_sum"] += quality_score

        metrics.track_performance_json = json.dumps(track_perf)

    # Update skill-specific performance
    if skills_used:
        skill_perf = {}
        if metrics.skill_performance_json:
            try:
                skill_perf = json.loads(metrics.skill_performance_json)
            except:
                pass

        for skill in skills_used:
            if skill not in skill_perf:
                skill_perf[skill] = {"completed": 0, "quality_sum": 0}

            skill_perf[skill]["completed"] += 1
            if quality_score:
                skill_perf[skill]["quality_sum"] += quality_score

        metrics.skill_performance_json = json.dumps(skill_perf)

    # Update timestamps
    metrics.last_task_at = datetime.utcnow()
    metrics.updated_at = datetime.utcnow()

    # Recalculate overall score and tier
    metrics.overall_score = calculate_overall_score(metrics)
    metrics.reliability_tier = determine_reliability_tier(
        metrics.overall_score,
        metrics.total_tasks_completed
    )

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


def record_task_failed(
    session: Session,
    agent_id: UUID,
    reason: str = "unknown"
) -> AgentMetrics:
    """Record task failure."""
    metrics = get_or_create_metrics(session, agent_id)

    metrics.total_tasks_failed += 1
    metrics.current_active_tasks = max(0, metrics.current_active_tasks - 1)
    metrics.updated_at = datetime.utcnow()

    # Recalculate score (failure reduces score)
    metrics.overall_score = calculate_overall_score(metrics)
    metrics.reliability_tier = determine_reliability_tier(
        metrics.overall_score,
        metrics.total_tasks_completed
    )

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


def record_task_cancelled(session: Session, agent_id: UUID) -> AgentMetrics:
    """Record task cancellation."""
    metrics = get_or_create_metrics(session, agent_id)

    metrics.total_tasks_cancelled += 1
    metrics.current_active_tasks = max(0, metrics.current_active_tasks - 1)
    metrics.updated_at = datetime.utcnow()

    session.add(metrics)
    session.commit()
    session.refresh(metrics)

    return metrics


# ============== Query Functions ==============

def get_agent_workload(session: Session, agent_id: UUID) -> Dict[str, Any]:
    """Get current workload status for an agent."""
    agent = session.get(Agent, agent_id)
    if not agent:
        return None

    metrics = get_or_create_metrics(session, agent_id)

    # Calculate availability
    max_tasks = agent.max_concurrent_tasks
    current_tasks = metrics.current_active_tasks
    availability = max(0, (max_tasks - current_tasks) / max_tasks) if max_tasks > 0 else 0

    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "role": agent.role,
        "status": agent.status,
        "current_active_tasks": current_tasks,
        "max_concurrent_tasks": max_tasks,
        "availability": availability,
        "reliability_tier": metrics.reliability_tier,
        "overall_score": metrics.overall_score,
    }


def get_all_agents_workload(session: Session) -> List[Dict[str, Any]]:
    """Get workload status for all active agents."""
    # Get all claimed agents
    agents = session.exec(
        select(Agent).where(Agent.status == AgentStatus.CLAIMED)
    ).all()

    workloads = []
    for agent in agents:
        workload = get_agent_workload(session, agent.id)
        if workload:
            workloads.append(workload)

    return workloads


def get_available_agents(
    session: Session,
    role: Optional[str] = None,
    min_availability: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Get agents that are available for new tasks.

    Args:
        session: Database session
        role: Filter by role (optional)
        min_availability: Minimum availability threshold (0-1)
    """
    workloads = get_all_agents_workload(session)

    # Filter by role
    if role:
        workloads = [w for w in workloads if w["role"] == role]

    # Filter by availability
    workloads = [w for w in workloads if w["availability"] >= min_availability]

    # Sort by availability (highest first), then by score
    workloads.sort(key=lambda w: (-w["availability"], -(w["overall_score"] or 0)))

    return workloads


def get_agent_metrics_detail(session: Session, agent_id: UUID) -> Optional[Dict[str, Any]]:
    """Get detailed metrics for an agent."""
    agent = session.get(Agent, agent_id)
    if not agent:
        return None

    metrics = get_or_create_metrics(session, agent_id)

    # Calculate completion rate
    completion_rate = None
    if metrics.total_tasks_assigned > 0:
        completion_rate = metrics.total_tasks_completed / metrics.total_tasks_assigned

    # Calculate availability
    availability = get_agent_workload(session, agent_id)["availability"]

    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "total_tasks_assigned": metrics.total_tasks_assigned,
        "total_tasks_completed": metrics.total_tasks_completed,
        "total_tasks_failed": metrics.total_tasks_failed,
        "completion_rate": completion_rate,
        "avg_completion_time_hours": metrics.avg_completion_time_hours,
        "response_time_p50_hours": metrics.response_time_p50_hours,
        "response_time_p95_hours": metrics.response_time_p95_hours,
        "avg_quality_score": metrics.avg_quality_score,
        "first_attempt_success_rate": metrics.first_attempt_success_rate,
        "current_active_tasks": metrics.current_active_tasks,
        "availability": availability,
        "overall_score": metrics.overall_score,
        "reliability_tier": metrics.reliability_tier,
    }
