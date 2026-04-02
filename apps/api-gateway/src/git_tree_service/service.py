import json
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional

from agenthub_protocol.schemas import (
    TRACE_COMMIT_PROTOCOL_VERSION,
    TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES,
)
from agenthub_protocol.signing import (
    compute_binding_hash,
    compute_diff_hash_from_patch,
    compute_reasoning_hash,
    get_trace_signing_secret,
    sign_trace_commit,
    verify_trace_commit_signature,
)
from agenthub_protocol.validator import TraceValidator
from sqlmodel import Session, select

from persistence import Bounty, BountyStatus

class GitTreeService:
    SYSTEM_TASK_TREE_AGENT_ID = "system/task-tree-sync"

    def __init__(self, session: Session, storage_root: str):
        self.session = session
        self.storage_root = os.path.abspath(storage_root)

    @staticmethod
    def _build_system_trace_commit(
        *,
        author_agent_id: str,
        model_name: str,
        diff_summary: str,
        reasoning_trace: list[str],
        rejected_alternatives: list[str],
        file_paths: list[str],
        tree_hash: str,
        diff_hash: str,
        parent_sha: Optional[str],
        timestamp_iso: str,
        signing_secret: str,
    ) -> dict:
        trigger_note = (
            author_agent_id
            if isinstance(author_agent_id, str) and author_agent_id.strip()
            else "system"
        )
        trace_commit = {
            "protocol_version": TRACE_COMMIT_PROTOCOL_VERSION,
            "tree_hash": tree_hash,
            "diff_hash": diff_hash,
            "reasoning_hash": compute_reasoning_hash(reasoning_trace),
            "diff_summary": diff_summary,
            "reasoning_trace": reasoning_trace,
            "rejected_alternatives": rejected_alternatives,
            "context_snapshot": {
                "file_paths": sorted(file_paths),
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {},
            },
            "intent": {
                "description": "synchronize repository task tree documentation",
                "category": "docs",
                "vector": [0.0],
            },
            "author": {
                "agent_id": GitTreeService.SYSTEM_TASK_TREE_AGENT_ID,
                "model_name": model_name,
            },
            "parent_sha": parent_sha,
            "timestamp": timestamp_iso,
            "automation": {
                "triggered_by_agent_id": trigger_note,
            },
        }
        trace_commit["binding_hash"] = compute_binding_hash(trace_commit)
        trace_commit["signature"] = sign_trace_commit(
            trace_commit,
            signing_secret,
            agent_id=GitTreeService.SYSTEM_TASK_TREE_AGENT_ID,
        )
        return trace_commit

    def generate_mermaid(self, repo_name: str) -> str:
        """Generate a Mermaid flowchart for the repository's bounties."""
        statement = select(Bounty).where(Bounty.repo_name == repo_name)
        bounties = self.session.exec(statement).all()

        if not bounties:
            return "No tasks found for this repository."

        mermaid = ["flowchart LR"]

        # Styles
        mermaid.append("    classDef completed fill:#22c55e,stroke:#16a34a,color:#fff")
        mermaid.append("    classDef in_progress fill:#3b82f6,stroke:#2563eb,color:#fff")
        mermaid.append("    classDef open fill:#eab308,stroke:#ca8a04,color:#fff")
        mermaid.append("    classDef pending fill:#52525b,stroke:#3f3f46,color:#a1a1aa")
        mermaid.append("    classDef ready fill:#f97316,stroke:#ea580c,color:#fff")

        for b in bounties:
            # Node definition with status-based styling
            status = b.status
            style_class = "pending"
            if status == BountyStatus.COMPLETED.value:
                style_class = "completed"
            elif status == BountyStatus.IN_PROGRESS.value:
                style_class = "in_progress"
            elif status == BountyStatus.OPEN.value:
                style_class = "open"
            elif status == "ready_for_preparation":
                style_class = "ready"

            clean_title = b.title.replace('"', "'")
            mermaid.append(f'    node_{b.id}["{clean_title}"]::: {style_class}')

            # Dependencies
            if b.dependencies:
                for dep_id in b.dependencies:
                    mermaid.append(f"    node_{dep_id} --> node_{b.id}")

        return "\n".join(mermaid)

    @staticmethod
    def _build_commit_message(trace_commit: dict) -> str:
        commit_msg = json.dumps(trace_commit, ensure_ascii=False, separators=(",", ":"))
        commit_msg_bytes = len(commit_msg.encode("utf-8"))
        if commit_msg_bytes > TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES:
            raise RuntimeError(
                "TraceCommit payload too large for git commit message "
                f"({commit_msg_bytes} bytes > {TRACE_COMMIT_MAX_COMMIT_MESSAGE_BYTES})"
            )
        return commit_msg

    def sync_repo_task_tree(self, repo_name: str, actor_agent_id: str = "system"):
        """Update BOUNTY_TREE.md in bare repo as system actor; keep trigger agent in automation metadata."""
        bare_repo_path = os.path.join(self.storage_root, repo_name)
        if not os.path.exists(bare_repo_path):
            return

        signing_secret = get_trace_signing_secret()
        if not signing_secret:
            raise RuntimeError("Trace signing secret is not configured")

        mermaid_code = self.generate_mermaid(repo_name)
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        content = f"# Repository Task Tree\n\n```mermaid\n{mermaid_code}\n```\n\n*Last updated: {timestamp_iso}*"

        work_dir = tempfile.mkdtemp(prefix="agenthub_tree_update_")
        try:
            # Clone and update
            subprocess.run(["git", "clone", bare_repo_path, work_dir], check=True, capture_output=True)

            file_path = os.path.join(work_dir, "BOUNTY_TREE.md")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            subprocess.run(["git", "add", "BOUNTY_TREE.md"], cwd=work_dir, check=True, capture_output=True)

            # Check if there are changes to commit
            status = subprocess.run(["git", "status", "--porcelain"], cwd=work_dir, capture_output=True, text=True)
            if not status.stdout.strip():
                return

            parent_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
            parent_sha = parent_result.stdout.strip() if parent_result.returncode == 0 else None
            if parent_sha == "HEAD":
                parent_sha = None

            tree_hash = subprocess.run(["git", "write-tree"], cwd=work_dir, check=True, capture_output=True, text=True).stdout.strip()
            diff_patch = subprocess.run(
                ["git", "diff", "--cached", "--binary", "--full-index"],
                cwd=work_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            diff_hash = compute_diff_hash_from_patch(diff_patch)

            reasoning_trace = [
                "Detected repository task graph changes requiring documentation update.",
                "Regenerated Mermaid task tree from current bounty state.",
                "Prepared structured TraceCommit payload for system-authored documentation sync.",
            ]
            rejected_alternatives = [
                "Plain-text commit message; rejected because hook requires TraceCommit JSON.",
            ]

            trace_commit = self._build_system_trace_commit(
                author_agent_id=actor_agent_id,
                model_name="agenthub-system/git-tree-service",
                diff_summary="update BOUNTY_TREE.md task graph visualization",
                reasoning_trace=reasoning_trace,
                rejected_alternatives=rejected_alternatives,
                file_paths=["BOUNTY_TREE.md"],
                tree_hash=tree_hash,
                diff_hash=diff_hash,
                parent_sha=parent_sha,
                timestamp_iso=timestamp_iso,
                signing_secret=signing_secret,
            )

            TraceValidator.validate_commit(
                trace_commit,
                require_parent_sha=bool(parent_sha),
                require_timezone_aware_timestamp=True,
            )
            if not verify_trace_commit_signature(trace_commit, signing_secret):
                raise RuntimeError("Generated TraceCommit signature verification failed")

            commit_msg = self._build_commit_message(trace_commit)
            git_identity_env = {
                **os.environ,
                "GIT_AUTHOR_NAME": self.SYSTEM_TASK_TREE_AGENT_ID,
                "GIT_AUTHOR_EMAIL": "system-task-tree-sync@agenthub.dev",
                "GIT_COMMITTER_NAME": self.SYSTEM_TASK_TREE_AGENT_ID,
                "GIT_COMMITTER_EMAIL": "system-task-tree-sync@agenthub.dev",
            }
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=work_dir,
                check=True,
                capture_output=True,
                env=git_identity_env,
            )

            review_branch = (
                f"system/task-tree-sync/task-tree-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
            )
            subprocess.run(["git", "checkout", "-b", review_branch], cwd=work_dir, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", review_branch], cwd=work_dir, check=True, capture_output=True)
        finally:
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)
