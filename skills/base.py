from abc import ABC, abstractmethod
from typing import Type, Any, Optional, Dict, List
from pydantic import BaseModel
from enum import Enum
from uuid import uuid4


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobProgress(BaseModel):
    pct: float = 0.0
    msg: str = ""


class SkillJob(BaseModel):
    id: str
    status: JobStatus
    progress: Optional[JobProgress] = None
    resume_token: Optional[str] = None


class ErrorInfo(BaseModel):
    code: str
    reason: str
    retriable: bool = False
    details: Optional[Dict[str, Any]] = None


class Paging(BaseModel):
    cursor: Optional[str] = None
    next_cursor: Optional[str] = None
    has_more: bool = False
    limit: int = 0
    total: Optional[int] = None


class Envelope(BaseModel):
    ok: bool
    data: Optional[Any] = None
    message: str = ""
    error: Optional[ErrorInfo] = None
    meta: Optional[Dict[str, Any]] = None
    job: Optional[SkillJob] = None
    paging: Optional[Paging] = None
    warnings: Optional[List[str]] = None
    evidence: Optional[List[Dict[str, Any]]] = None
    next_suggested_action: Optional[str] = None
    description: Optional[str] = None


class Skill(ABC):
    """
    Abstract Base Class for all Skills.
    Enforces Strict Inputs via Pydantic.

    M3: Adds optional job lifecycle & envelope helpers while keeping
    validate_and_execute() behavior backward-compatible.
    """

    name: str = "base_skill"
    description: str = "Base skill description"
    input_schema: Type[BaseModel]  # The Pydantic model class for arguments
    root_dir: Optional[str] = None

    # Whether this skill supports async execution (M3 capability flag)
    supports_async: bool = False

    def __init__(self, root_dir: Optional[str] = None):
        self.root_dir = root_dir

    # -------- Lifecycle hooks (no-op by default) --------
    def on_queued(self, job: SkillJob) -> None:
        pass

    def on_started(self, job: SkillJob) -> None:
        pass

    def on_progress(self, job: SkillJob) -> None:
        pass

    def on_finished(self, job: SkillJob) -> None:
        pass

    def on_failed(self, job: SkillJob, err: Exception) -> None:
        pass

    def on_canceled(self, job: SkillJob) -> None:
        pass

    # -------- Core abstract execution --------
    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """
        Execute the skill with validated arguments.
        Return value is used as "data" for synchronous invocations.
        """
        pass

    # -------- Existing sync path (kept unchanged for compatibility) --------
    def validate_and_execute(self, **kwargs) -> Any:
        """
        Validates input against input_schema then executes.
        This preserves the historical behavior: returns raw execute() result.
        """
        validated_args = self.input_schema(**kwargs)
        return self.execute(**validated_args.model_dump())

    # -------- Helpers for job + unified envelope (opt-in) --------
    def _new_job(self, status: JobStatus = JobStatus.running) -> SkillJob:
        return SkillJob(id=str(uuid4()), status=status)

    def update_progress(self, job: SkillJob, pct: float, msg: str = "") -> None:
        job.progress = JobProgress(pct=pct, msg=msg)
        self.on_progress(job)

    def make_envelope(
        self,
        ok: bool,
        *,
        data: Optional[Any] = None,
        message: str = "",
        error: Optional[ErrorInfo] = None,
        job: Optional[SkillJob] = None,
        meta: Optional[Dict[str, Any]] = None,
        paging: Optional[Paging] = None,
        warnings: Optional[List[str]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        next_suggested_action: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        env = Envelope(
            ok=ok,
            data=data,
            message=message,
            error=error,
            meta=meta,
            job=job,
            paging=paging,
            warnings=warnings,
            evidence=evidence,
            next_suggested_action=next_suggested_action,
            description=description,
        )
        return env.model_dump()

    def make_paged_envelope(
        self,
        data: List[Any],
        *,
        cursor: Optional[str],
        next_cursor: Optional[str],
        has_more: bool,
        limit: int,
        total: Optional[int] = None,
        message: str = "ok",
        description: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        paging = Paging(
            cursor=cursor,
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
            total=total,
        )
        return self.make_envelope(
            ok=True,
            data=data,
            message=message,
            paging=paging,
            description=description,
            meta=meta,
        )

    def run_with_envelope(self, **kwargs) -> Dict[str, Any]:
        """
        Validates inputs, runs execute(), and returns a unified envelope
        including a transient job object. This does not persist jobs and is
        intended as the sync baseline for M3 adoption.
        """
        job = self._new_job(JobStatus.running)
        self.on_started(job)
        try:
            validated_args = self.input_schema(**kwargs)
            data = self.execute(**validated_args.model_dump())
            job.status = JobStatus.succeeded
            self.on_finished(job)
            return self.make_envelope(
                ok=True,
                data=data,
                message="ok",
                job=job,
            )
        except Exception as e:  # noqa: BLE001 keep broad for envelope
            job.status = JobStatus.failed
            self.on_failed(job, e)
            return self.make_envelope(
                ok=False,
                data=None,
                message="skill execution failed",
                error=ErrorInfo(code="skill_execution_error", reason=str(e), retriable=False),
                job=job,
            )
