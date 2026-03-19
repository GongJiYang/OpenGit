from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ServiceReadyRequest(BaseModel):
    """Request to report service endpoint is ready."""

    job_id: UUID
    service_endpoint: str = Field(
        ...,
        max_length=500,
        description="URL where the deployed service is accessible",
    )
    health_check_path: str = Field(
        default="/health",
        max_length=100,
        description="Health check endpoint path",
    )
    access_token_validity_hours: int = Field(
        default=1,
        ge=1,
        le=24,
        description="How long the access token should be valid",
    )


class ServiceReadyResponse(BaseModel):
    """Response for service ready notification."""

    success: bool
    job_id: UUID
    service_endpoint: str
    access_token: str
    expires_at: datetime
    message: str


class UpdateRepoBindingRequest(BaseModel):
    """Request to update runner's repository bindings."""

    allowed_repo_ids: List[str] = Field(description="List of repo IDs the runner can serve")
    is_global: bool = Field(default=False, description="If True, runner serves all repos")


class SubmitAuditResultRequest(BaseModel):
    """Request to submit audit result from trusted infrastructure."""

    audit_id: UUID
    audited_stdout: str
    audited_exit_code: int


class ServiceStatusResponse(BaseModel):
    """Response for service status query."""

    job_id: UUID
    status: str
    service_endpoint: Optional[str] = None
    access_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    token_expires_in_seconds: Optional[int] = None
    runner_id: Optional[UUID] = None
    runner_status: Optional[str] = None
    is_ready_for_testing: bool = False
    message: str


class EndpointInfoResponse(BaseModel):
    """Response for quick endpoint info (for authenticated testers)."""

    job_id: UUID
    bounty_id: str
    service_endpoint: str
    access_token: str
    expires_at: datetime
    expires_in_seconds: int
    health_check_url: Optional[str] = None
