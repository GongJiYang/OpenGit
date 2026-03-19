from typing import List, Optional

from pydantic import BaseModel


class CommitRequest(BaseModel):
    """API-based commit payload."""

    files: dict  # {"path/to/file.py": "content"}
    diff_summary: str
    reasoning_trace: List[str]
    intent_category: str = "feature"  # feature, fix, refactor
    intent_description: str
    agent_id: str
    model_name: str
    bounty_id: Optional[str] = None


class VerificationRequest(BaseModel):
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    note: Optional[str] = None


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
