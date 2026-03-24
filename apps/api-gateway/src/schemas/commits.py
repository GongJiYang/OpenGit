from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


class CommitRequest(BaseModel):
    """API-based commit payload."""

    files: dict  # {"path/to/file.py": "content"}
    diff_summary: str
    reasoning_trace: List[str]
    rejected_alternatives: List[str]
    intent_category: str = "feature"  # feature, fix, refactor
    intent_description: str
    intent_vector: List[float] = Field(default_factory=lambda: [0.0])
    agent_id: str
    model_name: str
    bounty_id: Optional[str] = None


class VerificationRequest(BaseModel):
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    note: Optional[str] = None


class VerificationResult(BaseModel):
    exit_code: Optional[int] = None
    passed: Optional[bool] = None
    runner_job_id: Optional[str] = None
    execution_mode: Optional[str] = None
    execution_mode_source: Optional[str] = None


class CommitResponse(BaseModel):
    success: bool
    repo: str
    files_committed: List[str]
    agent: str
    sha: Optional[str] = None
    quality_warnings: List[str] = Field(default_factory=list)
    verification: VerificationResult
    task_tree_sync: Dict[str, Any] = Field(default_factory=dict)


class BlackboxTestResult(BaseModel):
    api_path: str
    method: str
    payload: Optional[dict] = None
    expected: int
    actual: int
    passed: bool


class BlackboxReport(BaseModel):
    test_id: str
    endpoint: str
    results: List[BlackboxTestResult]
    overall_verdict: str  # PASS or FAIL
