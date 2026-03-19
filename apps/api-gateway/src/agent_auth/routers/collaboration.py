"""
Collaboration Router

API endpoints for multi-agent collaboration:
- File locking
- Change region tracking
- Conflict detection
- Code review workflow
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from schemas.collaboration import (
    AcquireLockRequest,
    CreateReviewRequest,
    DetectConflictRequest,
    RegisterRegionRequest,
    ReleaseLockRequest,
    SubmitReviewRequest,
)
from ..database import get_db
from ..services.collaboration_service import CollaborationService

router = APIRouter(prefix="/collaboration", tags=["collaboration"])

# Global collaboration service instance (per-session would be better for production)
_collaboration_services: dict = {}


def get_collaboration_service(session: Session = Depends(get_db)) -> CollaborationService:
    """Get or create collaboration service for this session."""
    session_id = id(session)
    if session_id not in _collaboration_services:
        _collaboration_services[session_id] = CollaborationService(session)
    return _collaboration_services[session_id]


# ==================== File Locking Endpoints ====================

@router.post("/locks/acquire")
async def acquire_lock(
    request: AcquireLockRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """
    Acquire a file lock.

    Returns lock info if successful, error if already locked.
    """
    lock = service.acquire_lock(
        file_path=request.file_path,
        agent_id=request.agent_id,
        timeout_seconds=request.timeout_seconds
    )

    if lock is None:
        # Check why it failed
        lock_status = service.get_lock_status(request.file_path)
        if lock_status:
            raise HTTPException(
                status_code=423,
                detail={
                    "error": "file_locked",
                    "message": "File is locked by another agent",
                    "lock_info": lock_status
                }
            )
        else:
            raise HTTPException(
                status_code=429,
                detail="Agent at capacity or unable to acquire lock"
            )

    return {"success": True, "lock": lock.to_dict()}


@router.post("/locks/release")
async def release_lock(
    request: ReleaseLockRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Release a file lock."""
    success = service.release_lock(
        file_path=request.file_path,
        agent_id=request.agent_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Lock not found or not owned by this agent"
        )

    return {"success": True, "message": "Lock released"}


@router.get("/locks/{file_path:path}")
async def get_lock_status(
    file_path: str,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get lock status for a file."""
    status = service.get_lock_status(file_path)
    return {
        "file_path": file_path,
        "locked": status is not None,
        "lock_info": status
    }


# ==================== Change Region Endpoints ====================

@router.post("/regions/register")
async def register_change_region(
    request: RegisterRegionRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """
    Register a change region.

    Also checks for potential conflicts and returns them.
    """
    # First detect any conflicts
    conflicts = service.detect_conflicts(
        file_path=request.file_path,
        start_line=request.start_line,
        end_line=request.end_line,
        agent_id=request.agent_id
    )

    # Register the region
    region = service.register_change_region(
        file_path=request.file_path,
        start_line=request.start_line,
        end_line=request.end_line,
        agent_id=request.agent_id,
        description=request.description
    )

    return {
        "success": True,
        "region": region.to_dict(),
        "conflicts": conflicts,
        "has_conflicts": len(conflicts) > 0
    }


@router.delete("/regions/{file_path:path}/{agent_id}")
async def unregister_change_region(
    file_path: str,
    agent_id: UUID,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Unregister change regions for an agent in a file."""
    success = service.unregister_change_region(file_path, agent_id)
    return {"success": success}


@router.get("/regions/{file_path:path}")
async def get_file_regions(
    file_path: str,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get all change regions for a file."""
    regions = service.change_regions.get(file_path, [])
    return {
        "file_path": file_path,
        "regions": [r.to_dict() for r in regions]
    }


# ==================== Conflict Detection Endpoints ====================

@router.post("/conflicts/detect")
async def detect_conflicts(
    request: DetectConflictRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Detect potential conflicts for a planned change."""
    conflicts = service.detect_conflicts(
        file_path=request.file_path,
        start_line=request.start_line,
        end_line=request.end_line,
        agent_id=request.agent_id
    )

    return {
        "file_path": request.file_path,
        "conflicts": conflicts,
        "has_conflicts": len(conflicts) > 0,
        "safe_to_proceed": len(conflicts) == 0
    }


@router.get("/conflicts/{file_path:path}")
async def get_file_conflicts(
    file_path: str,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get all current conflicts for a file."""
    conflicts = service.get_file_conflicts(file_path)
    return {
        "file_path": file_path,
        "conflicts": conflicts,
        "conflict_count": len(conflicts)
    }


@router.get("/conflicts")
async def get_all_conflicts(
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get all current conflicts across all files."""
    status = service.get_global_status()
    return {
        "active_conflicts": status["active_conflicts"],
        "conflicts": status["conflicts"]
    }


# ==================== Code Review Endpoints ====================

@router.post("/reviews/create")
async def create_review(
    request: CreateReviewRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Create a new code review request."""
    # Check if review already exists
    if service.get_review(request.review_id):
        raise HTTPException(
            status_code=409,
            detail="Review with this ID already exists"
        )

    review = service.create_review(
        review_id=request.review_id,
        file_path=request.file_path,
        agent_id=request.agent_id
    )

    return {"success": True, "review": review.to_dict()}


@router.get("/reviews/{review_id}")
async def get_review(
    review_id: str,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get a review by ID."""
    review = service.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    return review.to_dict()


@router.post("/reviews/{review_id}/submit")
async def submit_review(
    review_id: str,
    request: SubmitReviewRequest,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Submit a review decision."""
    if request.status not in ["approved", "rejected", "changes_requested"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid status. Must be: approved, rejected, or changes_requested"
        )

    review = service.submit_review(
        review_id=review_id,
        reviewer_id=request.reviewer_id,
        status=request.status,
        comments=request.comments
    )

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    return {"success": True, "review": review.to_dict()}


@router.get("/reviews/agent/{agent_id}")
async def get_agent_reviews(
    agent_id: UUID,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get all reviews for an agent (authored by them)."""
    reviews = service.get_pending_reviews_for_agent(agent_id)
    return {"agent_id": str(agent_id), "reviews": reviews}


@router.get("/reviews/reviewer/{reviewer_id}")
async def get_reviews_for_reviewer(
    reviewer_id: UUID,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get reviews awaiting a specific reviewer."""
    reviews = service.get_reviews_to_review(reviewer_id)
    return {"reviewer_id": str(reviewer_id), "reviews": reviews}


# ==================== Status Endpoints ====================

@router.get("/status/file/{file_path:path}")
async def get_file_status(
    file_path: str,
    agent_id: Optional[UUID] = None,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """
    Get comprehensive status for a file.

    Includes lock info, change regions, and conflicts.
    """
    if agent_id:
        status = service.get_file_status(file_path, agent_id)
    else:
        lock_status = service.get_lock_status(file_path)
        regions = service.change_regions.get(file_path, [])
        conflicts = service.get_file_conflicts(file_path)
        status = {
            "file_path": file_path,
            "locked": lock_status is not None,
            "lock_info": lock_status,
            "change_regions": [r.to_dict() for r in regions],
            "conflicts": conflicts,
        }

    return status


@router.get("/status/agent/{agent_id}")
async def get_agent_status(
    agent_id: UUID,
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get collaboration status for an agent."""
    return service.get_agent_collaboration_status(agent_id)


@router.get("/status/global")
async def get_global_status(
    service: CollaborationService = Depends(get_collaboration_service)
):
    """Get global collaboration status."""
    return service.get_global_status()
