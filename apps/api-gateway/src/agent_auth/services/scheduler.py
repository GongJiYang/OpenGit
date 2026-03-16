"""
Agent Authentication Services

Background tasks and scheduled jobs for agent management.
"""

from datetime import datetime, timedelta
from typing import Set, Optional
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from ..models import Agent, AgentStatus
from ..utils.heartbeat_cache import get_heartbeat_cache
from persistence import Bounty


# ============== Configuration ==============

# Heartbeat flush interval (how often to write cached heartbeats to DB)
HEARTBEAT_FLUSH_INTERVAL_MINUTES = 5

# Expiration check interval
EXPIRATION_CHECK_INTERVAL_MINUTES = 60

# Heartbeat timeout (suspend agents that don't heartbeat for this long)
HEARTBEAT_TIMEOUT_HOURS = 2

# Temporary bounty claim cleanup interval
TEMPORARY_CLAIM_CLEANUP_INTERVAL_MINUTES = 60

# Runner health check interval
RUNNER_HEALTH_CHECK_INTERVAL_SECONDS = 30

# Runner offline threshold (seconds without heartbeat)
RUNNER_OFFLINE_THRESHOLD_SECONDS = 60


# ============== Heartbeat Flush Task ==============

def flush_heartbeat_cache(session: Session) -> dict:
    """
    Flush accumulated heartbeats from memory cache to database.

    This batch update reduces database write contention significantly.

    Args:
        session: Database session

    Returns:
        dict: Flush statistics
    """
    cache = get_heartbeat_cache()

    # Get batch of records to flush
    records = cache.get_flush_batch()
    if not records:
        return {"flushed": 0, "message": "No records to flush"}

    flushed_ids: Set[UUID] = set()

    for record in records:
        try:
            # Find agent
            statement = select(Agent).where(Agent.id == record.agent_id)
            agent = session.exec(statement).first()

            if agent and agent.status == AgentStatus.CLAIMED:
                # Update heartbeat timestamp and count
                agent.last_heartbeat_at = record.timestamp
                agent.heartbeat_count += record.count
                session.add(agent)
                flushed_ids.add(record.agent_id)

        except Exception as e:
            print(f"Error flushing heartbeat for {record.agent_id}: {e}")

    # Commit all updates
    session.commit()

    # Mark as flushed in cache
    cache.mark_flushed(flushed_ids)

    return {
        "flushed": len(flushed_ids),
        "total_records": len(records),
    }


# ============== Expiration Cleanup Task ==============

def cleanup_expired_claims(session: Session) -> dict:
    """
    Clean up expired unclaimed agent registrations.

    Agents that have been in PENDING status past their claim_expires_at
    will be marked as EXPIRED.

    Args:
        session: Database session

    Returns:
        dict: Cleanup statistics
    """
    now = datetime.utcnow()

    # Find expired pending agents
    statement = select(Agent).where(
        Agent.status == AgentStatus.PENDING,
        Agent.claim_expires_at < now
    )
    expired_agents = session.exec(statement).all()

    expired_count = 0
    for agent in expired_agents:
        agent.status = AgentStatus.EXPIRED
        session.add(agent)
        expired_count += 1

    session.commit()

    return {
        "expired_count": expired_count,
        "checked_at": now.isoformat(),
    }


# ============== Temporary Bounty Claim Cleanup Task ==============

def cleanup_expired_temporary_claims(session: Session) -> dict:
    """
    Clean up expired temporary bounty claims.

    Bounties that were temporarily claimed but not converted to permanent
    claims within 24 hours will be released back to open status.

    Args:
        session: Database session

    Returns:
        dict: Cleanup statistics
    """
    now = datetime.utcnow()

    # Find expired temporary claims
    statement = select(Bounty).where(
        Bounty.is_temporary_claim == True,
        Bounty.claim_expires_at < now,
        Bounty.status == "in_progress"
    )
    expired_claims = session.exec(statement).all()

    released_count = 0
    for bounty in expired_claims:
        # Release the bounty back to open
        bounty.status = "open"
        bounty.assignee = None
        bounty.is_temporary_claim = False
        bounty.claim_expires_at = None
        bounty.updated_at = now
        session.add(bounty)
        released_count += 1

    session.commit()

    return {
        "released_count": released_count,
        "checked_at": now.isoformat(),
    }


# ============== Heartbeat Timeout Check Task ==============

def check_heartbeat_timeouts(session: Session) -> dict:
    """
    Suspend agents that haven't sent heartbeat for too long.

    Args:
        session: Database session

    Returns:
        dict: Check statistics
    """
    timeout_threshold = datetime.utcnow() - timedelta(hours=HEARTBEAT_TIMEOUT_HOURS)

    # Find agents with stale heartbeats
    statement = select(Agent).where(
        Agent.status == AgentStatus.CLAIMED,
        (Agent.last_heartbeat_at == None) | (Agent.last_heartbeat_at < timeout_threshold)
    )
    stale_agents = session.exec(statement).all()

    suspended_count = 0
    for agent in stale_agents:
        # Only suspend if they had at least one heartbeat before
        if agent.last_heartbeat_at is not None:
            agent.status = AgentStatus.SUSPENDED
            session.add(agent)
            suspended_count += 1

    session.commit()

    return {
        "suspended_count": suspended_count,
        "timeout_hours": HEARTBEAT_TIMEOUT_HOURS,
    }


# ============== Runner Health Check Task ==============

def check_runner_health(session: Session) -> dict:
    """
    Check runner health and handle offline runners.

    This task:
    1. Marks runners as OFFLINE if they haven't sent heartbeat in a while
    2. Handles jobs assigned to offline runners (timeout or reassign)
    3. Updates runner status for UI display

    Args:
        session: Database session

    Returns:
        dict: Health check statistics
    """
    from ..models.runner import Runner, RunnerStatus, ComputeJob, ComputeJobStatus

    now = datetime.utcnow()
    offline_threshold = now - timedelta(seconds=RUNNER_OFFLINE_THRESHOLD_SECONDS)

    # Find runners that should be marked as OFFLINE
    online_runners = session.exec(
        select(Runner).where(
            Runner.status.in_([RunnerStatus.ONLINE, RunnerStatus.BUSY])
        )
    ).all()

    marked_offline = 0
    jobs_affected = 0

    for runner in online_runners:
        # Check if heartbeat is stale
        if runner.last_heartbeat_at is None or runner.last_heartbeat_at < offline_threshold:
            # Mark runner as offline
            old_status = runner.status
            runner.status = RunnerStatus.OFFLINE
            session.add(runner)
            marked_offline += 1

            # Handle any running job
            if runner.current_job_id:
                job = session.get(ComputeJob, runner.current_job_id)
                if job and job.status in [ComputeJobStatus.ASSIGNED, ComputeJobStatus.RUNNING]:
                    # Check if job has been running too long
                    if job.started_at:
                        running_time = (now - job.started_at).total_seconds()
                        if running_time > job.timeout_seconds:
                            # Job timed out
                            job.status = ComputeJobStatus.TIMEOUT
                            job.completed_at = now
                        else:
                            # Reset job to PENDING for reassignment
                            job.status = ComputeJobStatus.PENDING
                            job.runner_id = None
                            job.assigned_at = None
                            job.started_at = None
                    else:
                        # Job was assigned but never started
                        job.status = ComputeJobStatus.PENDING
                        job.runner_id = None
                        job.assigned_at = None

                    session.add(job)
                    jobs_affected += 1

            # Clear current job reference
            runner.current_job_id = None
            session.add(runner)

    session.commit()

    return {
        "marked_offline": marked_offline,
        "jobs_affected": jobs_affected,
        "offline_threshold_seconds": RUNNER_OFFLINE_THRESHOLD_SECONDS,
        "checked_at": now.isoformat(),
    }


def check_stale_jobs(session: Session) -> dict:
    """
    Check for jobs that have been in ASSIGNED state too long without starting.

    This catches cases where:
    - Runner accepted job but crashed before starting
    - Network issues prevented job from starting

    Args:
        session: Database session

    Returns:
        dict: Check statistics
    """
    from ..models.runner import ComputeJob, ComputeJobStatus

    now = datetime.utcnow()
    # Jobs should start within 5 minutes of assignment
    assignment_timeout = now - timedelta(minutes=5)

    # Find stale assigned jobs
    stale_jobs = session.exec(
        select(ComputeJob).where(
            ComputeJob.status == ComputeJobStatus.ASSIGNED,
            ComputeJob.assigned_at < assignment_timeout
        )
    ).all()

    reset_count = 0
    for job in stale_jobs:
        # Reset to PENDING for reassignment
        job.status = ComputeJobStatus.PENDING
        job.runner_id = None
        job.assigned_at = None
        session.add(job)
        reset_count += 1

    session.commit()

    return {
        "reset_count": reset_count,
        "assignment_timeout_minutes": 5,
        "checked_at": now.isoformat(),
    }


# ============== Scheduler Setup ==============

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def setup_scheduled_tasks(session_factory) -> AsyncIOScheduler:
    """
    Set up all scheduled background tasks.

    Args:
        session_factory: Callable that returns a database session

    Returns:
        AsyncIOScheduler: Configured scheduler
    """
    scheduler = get_scheduler()

    # Heartbeat cache flush job
    @scheduler.scheduled_job(
        IntervalTrigger(minutes=HEARTBEAT_FLUSH_INTERVAL_MINUTES),
        id="heartbeat_flush",
        name="Flush heartbeat cache to database"
    )
    def heartbeat_flush_job():
        with session_factory() as session:
            result = flush_heartbeat_cache(session)
            print(f"[Scheduler] Heartbeat flush: {result}")

    # Expiration cleanup job
    @scheduler.scheduled_job(
        IntervalTrigger(minutes=EXPIRATION_CHECK_INTERVAL_MINUTES),
        id="expiration_cleanup",
        name="Clean up expired claims"
    )
    def expiration_cleanup_job():
        with session_factory() as session:
            result = cleanup_expired_claims(session)
            print(f"[Scheduler] Expiration cleanup: {result}")

    # Heartbeat timeout check job
    @scheduler.scheduled_job(
        IntervalTrigger(minutes=30),
        id="heartbeat_timeout_check",
        name="Check for stale agents"
    )
    def heartbeat_timeout_job():
        with session_factory() as session:
            result = check_heartbeat_timeouts(session)
            print(f"[Scheduler] Heartbeat timeout check: {result}")

    # Temporary bounty claim cleanup job
    @scheduler.scheduled_job(
        IntervalTrigger(minutes=TEMPORARY_CLAIM_CLEANUP_INTERVAL_MINUTES),
        id="temporary_claim_cleanup",
        name="Clean up expired temporary bounty claims"
    )
    def temporary_claim_cleanup_job():
        with session_factory() as session:
            from ..services.bounty_service import BountyService
            service = BountyService(bounty_session=session)
            result = service.cleanup_expired_temporary_claims()
            print(f"[Scheduler] Temporary claim cleanup: {result}")

    # Runner health check job
    @scheduler.scheduled_job(
        IntervalTrigger(seconds=RUNNER_HEALTH_CHECK_INTERVAL_SECONDS),
        id="runner_health_check",
        name="Check runner health and handle offline runners"
    )
    def runner_health_check_job():
        with session_factory() as session:
            result = check_runner_health(session)
            if result["marked_offline"] > 0 or result["jobs_affected"] > 0:
                print(f"[Scheduler] Runner health check: {result}")

    # Stale job check job
    @scheduler.scheduled_job(
        IntervalTrigger(minutes=1),
        id="stale_job_check",
        name="Check for jobs stuck in ASSIGNED state"
    )
    def stale_job_check_job():
        with session_factory() as session:
            result = check_stale_jobs(session)
            if result["reset_count"] > 0:
                print(f"[Scheduler] Stale job check: {result}")

    return scheduler


def start_scheduler(session_factory):
    """
    Start the background task scheduler.

    Args:
        session_factory: Callable that returns a database session
    """
    scheduler = setup_scheduled_tasks(session_factory)
    scheduler.start()
    print("[Scheduler] Started background task scheduler")


def stop_scheduler():
    """Stop the background task scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        print("[Scheduler] Stopped background task scheduler")
