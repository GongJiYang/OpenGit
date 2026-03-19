from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ApproveReviewRequest(BaseModel):
    reviewer_id: UUID
    notes: str = ""


class RejectReviewRequest(BaseModel):
    reviewer_id: UUID
    reason: str


class HumanReviewJobResponse(BaseModel):
    id: UUID
    bounty_id: str
    retry_count: int
    max_retries: int
    failure_reason: Optional[str]
    failure_severity: Optional[str]
    created_at: datetime
    updated_at: datetime
    original_runner_id: Optional[UUID]
    stdout_preview: Optional[str] = None  # First 500 chars

    class Config:
        from_attributes = True


class RecoveryStatsResponse(BaseModel):
    pending_retries: int
    human_review_queue: int
    partial_passes: int
    total_in_recovery: int
