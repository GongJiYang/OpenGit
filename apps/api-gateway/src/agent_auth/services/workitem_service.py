"""
WorkItem unified abstraction and adapters for Bounty and Meta-Repo items.

Incremental integration layer that provides a common interface over:
- Bounty FSM (apps/api-gateway/src/agent_auth/services/bounty_fsm.py)
- Meta Repo PR/Update workflow (apps/api-gateway/src/meta/routes.py + persistence models)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Tuple, List

from sqlmodel import Session, select

from persistence import (
    Bounty,
    PlatformPR,
    PlatformUpdate,
    MetaRepoConfig,
    PlatformAuditLog,
    PRStatus,
    UpdateStatus,
)


@dataclass
class WorkItem:
    kind: str  # "bounty" | "meta_pr" | "meta_update"
    ref: Any   # bounty_id | pr_number | update_id
    title: Optional[str] = None
    status: Optional[str] = None
    repo_name: Optional[str] = None
    author_id: Optional[str] = None


class BountyAdapter:
    def __init__(self, session: Session):
        self.session = session

    def get(self, bounty_id: str) -> Optional[WorkItem]:
        b = self.session.get(Bounty, bounty_id)
        if not b:
            return None
        return WorkItem(kind="bounty", ref=bounty_id, title=b.title, status=b.status, repo_name=b.repo_name, author_id=b.assignee)

    def transition(self, bounty_id: str, to_status: str, ctx: dict) -> Tuple[Optional[WorkItem], Optional[str]]:
        from .bounty_fsm import transition as fsm_transition
        updated, err = fsm_transition(self.session, bounty_id, to_status, ctx=ctx or {})
        if err:
            return None, err
        return self.get(bounty_id), None


class MetaAdapter:
    """Adapter wrapping PlatformPR/PlatformUpdate workflow with simple methods."""

    def __init__(self, session: Session):
        self.session = session

    # --- PR operations ---
    def get_pr(self, pr_number: int) -> Optional[PlatformPR]:
        return self.session.exec(select(PlatformPR).where(PlatformPR.pr_number == pr_number)).first()

    def list_prs(self, status_filter: Optional[str], limit: int) -> List[PlatformPR]:
        query = select(PlatformPR).order_by(PlatformPR.created_at.desc())
        if status_filter:
            query = query.where(PlatformPR.status == status_filter)
        query = query.limit(limit)
        return self.session.exec(query).all()

    def create_pr(self, meta_config: MetaRepoConfig, title: str, description: Optional[str], source_branch: str, source_repo: str) -> PlatformPR:
        import subprocess
        from pathlib import Path

        # validate fork exists
        repos_dir = Path("./agenthub_data/repos").resolve()
        source_repo_path = repos_dir / source_repo
        # target_repo_path not used in create_pr

        # changed files
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", f"refs/heads/{source_branch}", "refs/heads/main"],
                cwd=str(source_repo_path), capture_output=True, text=True
            )
            changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except subprocess.CalledProcessError:
            changed_files = []

        touches_protected = any(
            any(__import__("fnmatch").fnmatch.fnmatch(f, pattern) for pattern in (meta_config.protected_paths or []))
            for f in changed_files
        )

        # next pr number
        last_pr = self.session.exec(select(PlatformPR).order_by(PlatformPR.pr_number.desc()).limit(1)).first()
        next_pr_number = (last_pr.pr_number + 1) if last_pr else 1

        pr = PlatformPR(
            pr_number=next_pr_number,
            title=title,
            description=description,
            source_branch=source_branch,
            source_repo=source_repo,
            author_type="agent",  # TODO integrate auth
            author_id="workitem",
            touches_protected_paths=touches_protected,
            requires_elevated_review=touches_protected,
            required_approval_count=(meta_config.require_approval_count if touches_protected else 1),
        )
        self.session.add(pr)
        audit = PlatformAuditLog(
            event_type="pr_created",
            actor_type="agent",
            actor_id="workitem",
            target_type="pr",
            target_id=str(next_pr_number),
            details={
                "title": title,
                "source_branch": source_branch,
                "changed_files": changed_files,
                "touches_protected_paths": touches_protected,
            }
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(pr)
        return pr

    def approve_pr(self, pr_number: int, reviewer_type: str, reviewer_id: str, comment: Optional[str], meta_config: MetaRepoConfig) -> PlatformPR:
        pr = self.get_pr(pr_number)
        if not pr:
            raise ValueError(f"PR #{pr_number} not found")

        if pr.status not in [PRStatus.OPEN.value, PRStatus.APPROVED.value]:
            raise ValueError(f"PR is in {pr.status} status, cannot approve")

        # Duplicate approval check
        exists = any(a.get("reviewer_id") == reviewer_id for a in (pr.approvals or []))
        if exists:
            raise ValueError("Already approved by this reviewer")

        approval = {
            "reviewer_id": reviewer_id,
            "reviewer_type": reviewer_type,
            "approved_at": datetime.utcnow().isoformat(),
            "comment": comment,
        }
        pr.approvals = (pr.approvals or []) + [approval]
        pr.approval_count = len(pr.approvals)

        # Protected path policy
        if pr.approval_count >= pr.required_approval_count:
            if pr.touches_protected_paths and meta_config.require_human_approval:
                has_human = any(a.get("reviewer_type") == "human" for a in pr.approvals)
                has_agent = any(a.get("reviewer_type") == "agent" for a in pr.approvals)
                if has_human and has_agent:
                    pr.status = PRStatus.APPROVED.value
            else:
                pr.status = PRStatus.APPROVED.value

        self.session.add(pr)
        # Audit
        audit = PlatformAuditLog(
            event_type="pr_approved",
            actor_type=reviewer_type,
            actor_id=reviewer_id,
            target_type="pr",
            target_id=str(pr.pr_number),
            details={
                "approval_count": pr.approval_count,
                "new_status": pr.status,
            }
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(pr)
        return pr

    def merge_pr(self, pr_number: int, meta_config: MetaRepoConfig, merger_type: str, merger_id: str) -> Tuple[PlatformPR, PlatformUpdate]:
        import subprocess
        from pathlib import Path

        pr = self.get_pr(pr_number)
        if not pr:
            raise ValueError(f"PR #{pr_number} not found")
        if pr.status != PRStatus.APPROVED.value:
            raise ValueError(f"PR is not approved for merge (status: {pr.status})")

        repos_dir = Path("./agenthub_data/repos").resolve()
        source_repo_path = repos_dir / pr.source_repo
        target_repo_path = repos_dir / meta_config.repo_name

        # 1. Fetch source branch
        subprocess.run([
            "git", "fetch", str(source_repo_path), f"refs/heads/{pr.source_branch}"
        ], cwd=str(target_repo_path), check=True, capture_output=True)

        # 2. Get merge commit sha (FETCH_HEAD)
        result = subprocess.run([
            "git", "rev-parse", "FETCH_HEAD"
        ], cwd=str(target_repo_path), capture_output=True, text=True)
        merge_sha = result.stdout.strip()

        # 3. Fast-forward main to merge_sha
        subprocess.run([
            "git", "update-ref", "refs/heads/main", merge_sha
        ], cwd=str(target_repo_path), check=True, capture_output=True)

        # Update PR
        pr.status = PRStatus.MERGED.value
        pr.merged_at = datetime.utcnow()
        pr.merge_commit_sha = merge_sha

        # Create PlatformUpdate
        update = PlatformUpdate(
            source_pr_id=pr.id,
            source_pr_number=pr.pr_number,
            source_commit_sha=merge_sha,
            source_branch=pr.source_branch,
            status=UpdateStatus.PENDING.value,
            previous_commit_sha=meta_config.current_commit,
            rollback_available=True,
            triggered_by=f"{merger_type}:{merger_id}",
        )
        self.session.add(update)

        # Link PR to update
        pr.update_id = update.id
        self.session.add(pr)
        self.session.commit()
        self.session.refresh(update)

        # Audit
        audit = PlatformAuditLog(
            event_type="pr_merged",
            actor_type=merger_type,
            actor_id=merger_id,
            target_type="pr",
            target_id=str(pr.pr_number),
            details={
                "merge_commit_sha": merge_sha,
                "update_id": update.id,
            }
        )
        self.session.add(audit)
        self.session.commit()
        return pr, update

    # --- Update operations ---
    def get_update(self, update_id: int) -> Optional[PlatformUpdate]:
        return self.session.get(PlatformUpdate, update_id)

    def list_updates(self, status_filter: Optional[str], limit: int) -> List[PlatformUpdate]:
        query = select(PlatformUpdate).order_by(PlatformUpdate.created_at.desc())
        if status_filter:
            query = query.where(PlatformUpdate.status == status_filter)
        query = query.limit(limit)
        return self.session.exec(query).all()

    def rollback_update(self, update_id: int, meta_config: MetaRepoConfig) -> PlatformUpdate:
        update = self.session.get(PlatformUpdate, update_id)
        if not update:
            raise ValueError(f"Update {update_id} not found")
        if not update.rollback_available:
            raise ValueError("Rollback not available for this update")
        if not update.previous_commit_sha:
            raise ValueError("No previous commit available for rollback")

        update.status = UpdateStatus.ROLLED_BACK.value
        update.completed_at = datetime.utcnow()
        self.session.add(update)

        # Update config
        meta_config.current_commit = update.previous_commit_sha
        self.session.add(meta_config)

        # Audit
        audit = PlatformAuditLog(
            event_type="rollback_executed",
            actor_type="human",
            actor_id="admin",
            target_type="update",
            target_id=str(update_id),
            details={"rolled_back_to": update.previous_commit_sha},
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(update)
        return update


class WorkItemService:
    def __init__(self, session: Session):
        self.session = session
        self.bounty = BountyAdapter(session)
        self.meta = MetaAdapter(session)

    # Convenience wrappers
    def get(self, kind: str, ref: Any) -> Optional[WorkItem]:
        if kind == "bounty":
            return self.bounty.get(str(ref))
        elif kind == "meta_pr":
            pr = self.meta.get_pr(int(ref))
            if not pr:
                return None
            return WorkItem(kind="meta_pr", ref=int(ref), title=pr.title, status=pr.status, repo_name=None, author_id=pr.author_id)
        else:
            return None

    def transition(self, kind: str, ref: Any, to_status: str, ctx: Optional[dict] = None) -> Tuple[Optional[WorkItem], Optional[str]]:
        if kind == "bounty":
            return self.bounty.transition(str(ref), to_status, ctx or {})
        return None, f"Transition not supported for kind={kind}"

    # Meta specific
    def approve_meta_pr(self, pr_number: int, reviewer_type: str, reviewer_id: str, comment: Optional[str], meta_config: MetaRepoConfig) -> PlatformPR:
        return self.meta.approve_pr(pr_number, reviewer_type, reviewer_id, comment, meta_config)

    def merge_meta_pr(self, pr_number: int, meta_config: MetaRepoConfig, merger_type: str, merger_id: str) -> Tuple[PlatformPR, PlatformUpdate]:
        return self.meta.merge_pr(pr_number, meta_config, merger_type, merger_id)
