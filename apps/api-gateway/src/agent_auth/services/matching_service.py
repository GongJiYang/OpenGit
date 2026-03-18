"""
Agent Matching Service

Intelligent task-agent matching based on:
- Skill matching (40% weight)
- Availability/Workload (30% weight)
- Historical performance (20% weight)
- Track preference (10% weight)

Matching Algorithm:
1. Filter agents by role compatibility
2. Filter agents with availability > 0
3. Calculate match score for each agent
4. Sort by score and return recommendations
"""

from typing import List, Optional, Dict, Any, Tuple
from sqlmodel import Session, select

from ..models import Agent, AgentStatus
from .metrics_service import (
    get_or_create_metrics,
    get_agent_workload,
)


# ============== Weight Configuration ==============

WEIGHT_SKILL_MATCH = 0.4
WEIGHT_AVAILABILITY = 0.3
WEIGHT_PERFORMANCE = 0.2
WEIGHT_PREFERENCE = 0.1

WEIGHTS = {
    "skill_match": WEIGHT_SKILL_MATCH,
    "availability": WEIGHT_AVAILABILITY,
    "performance": WEIGHT_PERFORMANCE,
    "preference": WEIGHT_PREFERENCE,
}

# ============== Skill Taxonomy ==============

# Track to skill mapping
TRACK_SKILLS = {
    "backend": ["python", "java", "go", "rust", "nodejs", "api", "database", "sql"],
    "frontend": ["react", "vue", "angular", "typescript", "javascript", "css", "html"],
    "testing": ["pytest", "jest", "unittest", "e2e", "integration", "tdd"],
    "infrastructure": ["docker", "kubernetes", "aws", "gcp", "terraform", "ci", "cd", "linux"],
    "ml": ["pytorch", "tensorflow", "scikit", "numpy", "pandas", "machine-learning", "ai"],
}

# Role to skill mapping
ROLE_SKILLS = {
    "architect": ["design", "architecture", "planning", "documentation", "api-design"],
    "contributor": ["coding", "implementation", "debugging", "refactoring"],
    "executor": ["testing", "verification", "ci", "cd", "deployment"],
    "tester": ["testing", "e2e", "blackbox", "api-testing", "security-testing"],
}


# ============== Core Matching Functions ==============

def calculate_skill_match_score(
    agent_skills: List[str],
    required_role: str,
    track: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """
    Calculate skill matching score.

    Returns: (score 0-1, matched_skills)
    """
    if not agent_skills:
        return 0.3, []

    agent_skills_lower = [s.lower() for s in agent_skills]
    matched_skills = []

    # Role match
    role_required_skills = ROLE_SKILLS.get(required_role.lower(), [])
    for skill in role_required_skills:
        if skill.lower() in agent_skills_lower:
            matched_skills.append(skill)

    # Track match
    if track:
        track_skills = TRACK_SKILLS.get(track.lower(), [])
        for skill in track_skills:
            if skill.lower() in agent_skills_lower and skill not in matched_skills:
                matched_skills.append(skill)

    # Calculate score
    if not matched_skills:
        return 0.2, []

    # Score based on number of matched skills
    max_expected = 5
    score = min(1.0, len(matched_skills) / max_expected)
    return round(score, 2), matched_skills


def calculate_availability_score(
    current_active_tasks: int,
    max_concurrent_tasks: int
) -> float:
    """
    Calculate availability score.

    Higher availability = higher score.
    0 tasks = 1.0 score
    at capacity = 0.0 score
    """
    if max_concurrent_tasks <= 0:
        return 0.0

    if current_active_tasks >= max_concurrent_tasks:
        return 0.0

    availability = (max_concurrent_tasks - current_active_tasks) / max_concurrent_tasks
    return round(availability, 2)


def calculate_performance_score(
    overall_score: Optional[float],
    reliability_tier: str
) -> float:
    """
    Calculate performance score based on historical metrics.

    New agents get 0.5 (benefit of doubt)
    Platinum/Gold get bonus
    """
    if overall_score is None:
        return 0.5  # New agent gets benefit of doubt

    # Base score from overall score
    base_score = (overall_score or 50) / 100

    # Tier bonus
    tier_bonus = {
        "new": 0.0,
        "bronze": 0.05,
        "silver": 0.1,
        "gold": 0.15,
        "platinum": 0.2,
    }

    score = base_score + tier_bonus.get(reliability_tier, 0)
    return min(1.0, max(0.3, round(score, 2)))


def calculate_preference_score(
    preferred_tracks: List[str],
    track: Optional[str]
) -> float:
    """
    Calculate preference score.

    If agent prefers this track, higher score.
    If track is in preferred tracks, score is boosted.
    Related tracks also get partial score.
    """
    if not track:
        return 0.5

    if not preferred_tracks:
        return 0.3

    preferred_lower = [t.lower() for t in preferred_tracks]
    track_lower = track.lower()

    # Direct match
    if track_lower in preferred_lower:
        return 1.0

    # Related tracks boost
    related_tracks = {
        "backend": ["testing", "infrastructure"],
        "frontend": ["testing", "backend"],
        "testing": ["backend", "frontend"],
        "infrastructure": ["backend", "testing"],
    }

    related = related_tracks.get(track_lower, [])
    for related_track in related:
        if related_track in preferred_lower:
            return 0.7

    return 0.3


def calculate_total_match_score(
    skill_score: float,
    availability_score: float,
    performance_score: float,
    preference_score: float
) -> float:
    """Calculate weighted total match score."""
    total = (
        skill_score * WEIGHT_SKILL_MATCH +
        availability_score * WEIGHT_AVAILABILITY +
        performance_score * WEIGHT_PERFORMANCE +
        preference_score * WEIGHT_PREFERENCE
    )
    return round(total, 3)


# ============== Main Matching Functions ==============

def find_matching_agents(
    session: Session,
    required_role: str,
    track: Optional[str] = None,
    required_skills: Optional[List[str]] = None,
    min_availability: float = 0.0,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Find best matching agents for a task.

    Args:
        session: Database session
        required_role: Required role (architect, contributor, executor, tester)
        track: Task track (backend, frontend, testing, infrastructure)
        required_skills: Specific skills required
        min_availability: Minimum availability threshold (0-1)
        limit: Maximum number of recommendations

    Returns:
        List of agent recommendations sorted by match score
    """
    # Get all active agents with matching role
    query = select(Agent).where(
        Agent.status == AgentStatus.CLAIMED
    )
    if required_role:
        role_str = required_role.value if hasattr(required_role, "value") else required_role
        query = query.where(Agent.role == role_str)

    agents = session.exec(query).all()

    recommendations = []

    for agent in agents:
        # Get workload
        workload = get_agent_workload(session, agent.id)
        if not workload:
            continue

        # Filter by minimum availability
        if workload["availability"] < min_availability:
            continue

        # Get metrics
        metrics = get_or_create_metrics(session, agent.id)

        # Calculate skill match
        agent_skills = agent.skills or []
        skill_score, matched_skills = calculate_skill_match_score(
            agent_skills,
            required_role,
            track
        )

        # Add required skills matching if specified
        if required_skills:
            req_skills_lower = [s.lower() for s in required_skills]
            agent_skills_lower = [s.lower() for s in agent_skills]
            for skill in req_skills_lower:
                if skill in agent_skills_lower and skill not in [s.lower() for s in matched_skills]:
                    matched_skills.append(skill)

        # Calculate scores
        availability_score = calculate_availability_score(
            workload["current_active_tasks"],
            workload["max_concurrent_tasks"]
        )
        performance_score = calculate_performance_score(
            metrics.overall_score,
            metrics.reliability_tier
        )
        preference_score = calculate_preference_score(
            agent.preferred_tracks or [],
            track
        )
        total_score = calculate_total_match_score(
            skill_score,
            availability_score,
            performance_score,
            preference_score
        )

        recommendations.append({
            "agent_id": agent.id,
            "agent_name": agent.name,
            "role": agent.role,
            "match_score": total_score,
            "match_breakdown": {
                "skill_match": skill_score,
                "availability": availability_score,
                "performance": performance_score,
                "preference": preference_score,
            },
            "skill_match_score": skill_score,
            "availability_score": availability_score,
            "performance_score": performance_score,
            "preference_score": preference_score,
            "current_active_tasks": workload["current_active_tasks"],
            "max_concurrent_tasks": workload["max_concurrent_tasks"],
            "reliability_tier": metrics.reliability_tier,
            "matched_skills": matched_skills,
            "overall_score": metrics.overall_score,
        })

    # Sort by match score (highest first)
    recommendations.sort(key=lambda r: -r["match_score"])

    # Return top N recommendations
    return recommendations[:limit]


def find_best_agent(
    session: Session,
    required_role: str,
    track: Optional[str] = None,
    required_skills: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find single best matching agent for a task.

    Returns: Best agent recommendation or None
    """
    recommendations = find_matching_agents(
        session=session,
        required_role=required_role,
        track=track,
        required_skills=required_skills,
        min_availability=0.1,
        limit=1,
    )
    return recommendations[0] if recommendations else None


def get_task_match_summary(
    session: Session,
    bounty_id: str,
    bounty_title: str,
    required_role: str,
    track: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get matching summary for a task.

    Returns detailed matching info for UI display.
    """
    recommendations = find_matching_agents(
        session=session,
        required_role=required_role,
        track=track,
        limit=5,
    )
    # Count total eligible
    total_agents = len(recommendations)
    available_agents = len([r for r in recommendations if r["availability_score"] > 0])

    return {
        "bounty_id": bounty_id,
        "bounty_title": bounty_title,
        "required_role": required_role,
        "track": track,
        "recommendations": recommendations,
        "total_agents_evaluated": total_agents,
        "available_agents": available_agents,
        "best_match": recommendations[0] if recommendations else None,
    }
