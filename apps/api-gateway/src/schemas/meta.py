from typing import List, Optional

from pydantic import BaseModel, Field


class MetaRepoInitRequest(BaseModel):
    """Request to initialize the meta-repository."""

    deploy_root: str = Field(description="Absolute path to platform root directory")
    protected_paths: Optional[List[str]] = None
    require_approval_count: int = Field(default=2)
    require_human_approval: bool = Field(default=True)


class CreateForkRequest(BaseModel):
    """Request to create a fork of the meta-repo."""

    fork_name: Optional[str] = Field(default=None, description="Custom fork name")


class CreatePRRequest(BaseModel):
    """Request to create a Pull Request."""

    title: str = Field(max_length=255)
    description: Optional[str] = None
    source_branch: str
    source_repo: str  # Fork repo name


class ApprovePRRequest(BaseModel):
    """Request to approve a PR."""

    comment: Optional[str] = None
