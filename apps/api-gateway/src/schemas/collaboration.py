from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class AcquireLockRequest(BaseModel):
    agent_id: UUID
    file_path: str
    timeout_seconds: int = 300


class ReleaseLockRequest(BaseModel):
    agent_id: UUID
    file_path: str


class RegisterRegionRequest(BaseModel):
    agent_id: UUID
    file_path: str
    start_line: int
    end_line: int
    description: str = ""


class DetectConflictRequest(BaseModel):
    agent_id: UUID
    file_path: str
    start_line: int
    end_line: int


class CreateReviewRequest(BaseModel):
    review_id: str
    file_path: str
    agent_id: UUID


class SubmitReviewRequest(BaseModel):
    reviewer_id: UUID
    status: str  # pending, approved, rejected, changes_requested
    comments: Optional[List[dict]] = None
