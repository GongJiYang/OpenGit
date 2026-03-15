"""
Collaboration Service

Enables multi-agent collaboration with:
- Conflict detection before code changes
- File locking mechanism
- Change region markers
- Merge conflict resolution

Features:
1. File Locking: Prevent concurrent edits to same file
2. Change Regions: Mark areas being modified
3. Merge Preview: Show potential conflicts before merging
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Set, Any
from uuid import UUID
from sqlmodel import Session

from ..models import Agent
from .metrics_service import get_agent_workload


class FileLock:
    """File lock for preventing concurrent edits."""

    def __init__(self, file_path: str, agent_id: UUID, timeout_seconds: int = 300):
        self.file_path = file_path
        self.agent_id = agent_id
        self.locked_at = datetime.utcnow()
        self.timeout_seconds = timeout_seconds

    def is_expired(self) -> bool:
        """Check if lock has expired."""
        return (datetime.utcnow() - self.locked_at).total_seconds() > self.timeout_seconds

    def is_owned_by(self, agent_id: UUID) -> bool:
        """Check if lock is owned by specific agent."""
        return self.agent_id == agent_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "agent_id": str(self.agent_id),
            "locked_at": self.locked_at.isoformat(),
            "expires_at": (self.locked_at + timedelta(seconds=self.timeout_seconds)).isoformat(),
        }


class ChangeRegion:
    """Tracks a region being modified by an agent."""

    def __init__(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        agent_id: UUID,
        description: str = ""
    ):
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.agent_id = agent_id
        self.description = description
        self.created_at = datetime.utcnow()

    def overlaps_with(self, other: "ChangeRegion") -> bool:
        """Check if this region overlaps with another."""
        if self.file_path != other.file_path:
            return False
        return not (self.end_line < other.start_line or self.start_line > other.end_line)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "agent_id": str(self.agent_id),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


class MergeConflict:
    """Represents a detected merge conflict."""

    def __init__(
        self,
        file_path: str,
        region1: ChangeRegion,
        region2: ChangeRegion,
        conflict_type: str = "overlap"
    ):
        self.file_path = file_path
        self.region1 = region1
        self.region2 = region2
        self.conflict_type = conflict_type
        self.detected_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "conflict_type": self.conflict_type,
            "agents": [str(self.region1.agent_id), str(self.region2.agent_id)],
            "regions": [
                {"start": self.region1.start_line, "end": self.region1.end_line},
                {"start": self.region2.start_line, "end": self.region2.end_line},
            ],
            "detected_at": self.detected_at.isoformat(),
        }


class CodeReview:
    """Represents a code review request."""

    def __init__(
        self,
        review_id: str,
        file_path: str,
        agent_id: UUID,
        reviewer_id: Optional[UUID] = None,
        status: str = "pending"
    ):
        self.review_id = review_id
        self.file_path = file_path
        self.agent_id = agent_id
        self.reviewer_id = reviewer_id
        self.status = status  # pending, approved, rejected, changes_requested
        self.comments: List[Dict[str, Any]] = []
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def add_comment(self, author_id: UUID, content: str, line_number: Optional[int] = None):
        """Add a review comment."""
        self.comments.append({
            "author_id": str(author_id),
            "content": content,
            "line_number": line_number,
            "created_at": datetime.utcnow().isoformat(),
        })
        self.updated_at = datetime.utcnow()

    def approve(self, reviewer_id: UUID):
        """Approve the review."""
        self.reviewer_id = reviewer_id
        self.status = "approved"
        self.updated_at = datetime.utcnow()

    def reject(self, reviewer_id: UUID, reason: str):
        """Reject the review."""
        self.reviewer_id = reviewer_id
        self.status = "rejected"
        self.add_comment(reviewer_id, f"Rejected: {reason}")
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "review_id": self.review_id,
            "file_path": self.file_path,
            "agent_id": str(self.agent_id),
            "reviewer_id": str(self.reviewer_id) if self.reviewer_id else None,
            "status": self.status,
            "comments": self.comments,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class CollaborationService:
    """Service for managing multi-agent collaboration."""

    def __init__(self, session: Session):
        self.session = session
        self.active_locks: Dict[str, FileLock] = {}
        self.change_regions: Dict[str, List[ChangeRegion]] = {}
        self.pending_reviews: Dict[str, CodeReview] = {}
        self.conflict_history: List[MergeConflict] = []

    # ==================== File Locking ====================

    def acquire_lock(
        self,
        file_path: str,
        agent_id: UUID,
        timeout_seconds: int = 300
    ) -> Optional[FileLock]:
        """
        Attempt to acquire a file lock.

        Args:
            file_path: Path to the file to lock
            agent_id: Agent requesting the lock
            timeout_seconds: Lock timeout in seconds

        Returns:
            FileLock object if successful, None if file is already locked
        """
        # Clean up expired locks first
        self._cleanup_expired_locks()

        # Check if file is already locked
        if file_path in self.active_locks:
            existing_lock = self.active_locks[file_path]
            if not existing_lock.is_expired():
                # Allow same agent to re-acquire
                if existing_lock.is_owned_by(agent_id):
                    existing_lock.locked_at = datetime.utcnow()
                    return existing_lock
                return None

        # Check agent workload
        workload = get_agent_workload(self.session, agent_id)
        if workload and workload["availability"] < 0.1:
            return None  # Agent at capacity

        # Create new lock
        lock = FileLock(
            file_path=file_path,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds
        )
        self.active_locks[file_path] = lock
        return lock

    def release_lock(self, file_path: str, agent_id: UUID) -> bool:
        """
        Release a file lock.

        Returns True if lock was released, False if not owned or doesn't exist.
        """
        if file_path in self.active_locks:
            lock = self.active_locks[file_path]
            if lock.is_owned_by(agent_id):
                del self.active_locks[file_path]
                return True
        return False

    def get_lock_status(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get current lock status for a file."""
        self._cleanup_expired_locks()
        if file_path in self.active_locks:
            return self.active_locks[file_path].to_dict()
        return None

    def _cleanup_expired_locks(self):
        """Remove expired locks."""
        expired = [path for path, lock in self.active_locks.items() if lock.is_expired()]
        for path in expired:
            del self.active_locks[path]

    # ==================== Change Region Tracking ====================

    def register_change_region(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        agent_id: UUID,
        description: str = ""
    ) -> ChangeRegion:
        """
        Register a change region being modified.

        This helps detect potential conflicts before they happen.
        """
        region = ChangeRegion(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            agent_id=agent_id,
            description=description
        )

        if file_path not in self.change_regions:
            self.change_regions[file_path] = []

        # Remove old regions from same agent in same file
        self.change_regions[file_path] = [
            r for r in self.change_regions[file_path]
            if r.agent_id != agent_id
        ]

        self.change_regions[file_path].append(region)
        return region

    def unregister_change_region(self, file_path: str, agent_id: UUID) -> bool:
        """Remove change regions for an agent in a file."""
        if file_path in self.change_regions:
            original_count = len(self.change_regions[file_path])
            self.change_regions[file_path] = [
                r for r in self.change_regions[file_path]
                if r.agent_id != agent_id
            ]
            return len(self.change_regions[file_path]) < original_count
        return False

    def clear_change_regions(self, file_path: str):
        """Clear all change regions for a file (after commit)."""
        if file_path in self.change_regions:
            del self.change_regions[file_path]

    # ==================== Conflict Detection ====================

    def detect_conflicts(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        agent_id: UUID
    ) -> List[Dict[str, Any]]:
        """
        Detect potential conflicts with other agents' changes.

        Returns list of conflicts found.
        """
        conflicts = []
        new_region = ChangeRegion(file_path, start_line, end_line, agent_id)

        if file_path not in self.change_regions:
            return conflicts

        for existing_region in self.change_regions[file_path]:
            # Skip own changes
            if existing_region.agent_id == agent_id:
                continue

            # Check for overlap
            if new_region.overlaps_with(existing_region):
                conflict = MergeConflict(
                    file_path=file_path,
                    region1=new_region,
                    region2=existing_region
                )
                conflicts.append(conflict.to_dict())

        return conflicts

    def get_file_conflicts(self, file_path: str) -> List[Dict[str, Any]]:
        """Get all current conflicts for a file."""
        conflicts = []

        if file_path not in self.change_regions:
            return conflicts

        regions = self.change_regions[file_path]
        for i, region1 in enumerate(regions):
            for region2 in regions[i + 1:]:
                if region1.overlaps_with(region2):
                    conflict = MergeConflict(file_path, region1, region2)
                    conflicts.append(conflict.to_dict())

        return conflicts

    # ==================== Code Review ====================

    def create_review(
        self,
        review_id: str,
        file_path: str,
        agent_id: UUID
    ) -> CodeReview:
        """Create a new code review request."""
        review = CodeReview(
            review_id=review_id,
            file_path=file_path,
            agent_id=agent_id
        )
        self.pending_reviews[review_id] = review
        return review

    def get_review(self, review_id: str) -> Optional[CodeReview]:
        """Get a review by ID."""
        return self.pending_reviews.get(review_id)

    def submit_review(
        self,
        review_id: str,
        reviewer_id: UUID,
        status: str,
        comments: Optional[List[Dict]] = None
    ) -> Optional[CodeReview]:
        """Submit a review decision."""
        review = self.pending_reviews.get(review_id)
        if not review:
            return None

        review.reviewer_id = reviewer_id
        review.status = status

        if comments:
            for comment in comments:
                review.add_comment(
                    author_id=reviewer_id,
                    content=comment.get("content", ""),
                    line_number=comment.get("line_number")
                )

        review.updated_at = datetime.utcnow()
        return review

    def get_pending_reviews_for_agent(self, agent_id: UUID) -> List[Dict[str, Any]]:
        """Get all reviews authored by an agent."""
        return [
            r.to_dict() for r in self.pending_reviews.values()
            if r.agent_id == agent_id
        ]

    def get_reviews_to_review(self, reviewer_id: UUID) -> List[Dict[str, Any]]:
        """Get reviews assigned to or awaiting a reviewer."""
        return [
            r.to_dict() for r in self.pending_reviews.values()
            if r.status == "pending" or r.reviewer_id == reviewer_id
        ]

    # ==================== Status & Reporting ====================

    def get_file_status(self, file_path: str, agent_id: UUID) -> Dict[str, Any]:
        """
        Get comprehensive status of a file.

        Returns lock info, change regions, and any conflicts.
        """
        lock_status = self.get_lock_status(file_path)
        regions = self.change_regions.get(file_path, [])
        conflicts = self.get_file_conflicts(file_path)

        return {
            "file_path": file_path,
            "locked": lock_status is not None,
            "lock_info": lock_status,
            "change_regions": [r.to_dict() for r in regions],
            "conflicts": conflicts,
            "can_edit": lock_status is None or lock_status.get("agent_id") == str(agent_id),
        }

    def get_agent_collaboration_status(self, agent_id: UUID) -> Dict[str, Any]:
        """Get collaboration status for an agent."""
        held_locks = [
            lock.to_dict() for lock in self.active_locks.values()
            if lock.is_owned_by(agent_id)
        ]

        active_regions = []
        for regions in self.change_regions.values():
            for region in regions:
                if region.agent_id == agent_id:
                    active_regions.append(region.to_dict())

        return {
            "agent_id": str(agent_id),
            "held_locks": held_locks,
            "active_change_regions": active_regions,
            "pending_reviews": self.get_pending_reviews_for_agent(agent_id),
        }

    def get_global_status(self) -> Dict[str, Any]:
        """Get global collaboration status."""
        all_conflicts = []
        for file_path in self.change_regions:
            all_conflicts.extend(self.get_file_conflicts(file_path))

        return {
            "active_locks": len(self.active_locks),
            "files_with_changes": len(self.change_regions),
            "pending_reviews": len(self.pending_reviews),
            "active_conflicts": len(all_conflicts),
            "conflicts": all_conflicts,
        }
