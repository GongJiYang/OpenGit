"""
Meta-Repository Sync Service

Handles synchronization from bare repo to running platform.
"""

import os
import subprocess
import shutil
import tempfile
import fnmatch
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

from sqlmodel import Session, select

from ..persistence import (
    MetaRepoConfig,
    PlatformUpdate,
    UpdateStatus,
)


class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


class MetaSyncService:
    """
    Handles synchronization from bare repo to running platform.

    This service:
    1. Gets changed files between commits
    2. Checks out specific commit to temp directory
    3. Syncs files to deploy root with safety checks
    4. Tracks sync progress in PlatformUpdate
    """

    def __init__(self, meta_config: MetaRepoConfig):
        self.config = meta_config
        self.repos_dir = Path("./agenthub_data/repos").resolve()
        self.bare_repo_path = self.repos_dir / meta_config.repo_name
        self.deploy_root = Path(meta_config.deploy_root).resolve()

    def get_changed_files(
        self,
        old_sha: Optional[str],
        new_sha: str
    ) -> List[str]:
        """
        Get list of files changed between commits.

        Args:
            old_sha: Previous commit SHA (None for first deploy)
            new_sha: New commit SHA

        Returns:
            List of changed file paths
        """
        if old_sha is None:
            # First deploy, get all tracked files
            cmd = ["git", "ls-tree", "-r", "--name-only", new_sha]
        else:
            cmd = ["git", "diff", "--name-only", old_sha, new_sha]

        result = subprocess.run(
            cmd,
            cwd=str(self.bare_repo_path),
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return []

        return [f for f in result.stdout.strip().split('\n') if f]

    def checkout_commit(self, target_dir: str, commit_sha: str) -> bool:
        """
        Checkout specific commit to temp directory.

        Args:
            target_dir: Directory to checkout to
            commit_sha: Commit SHA to checkout

        Returns:
            True if successful
        """
        try:
            # Clone bare repo
            subprocess.run(
                ["git", "clone", "--no-local", str(self.bare_repo_path), target_dir],
                capture_output=True,
                check=True
            )

            # Checkout specific commit
            subprocess.run(
                ["git", "checkout", commit_sha],
                cwd=target_dir,
                capture_output=True,
                check=True
            )

            return True
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Checkout failed: {e.stderr.decode() if e.stderr else str(e)}")
            return False

    def is_protected_path(self, file_path: str) -> bool:
        """
        Check if path is in protected list.

        Protected paths require elevated approval to modify.
        """
        for pattern in self.config.protected_paths:
            if fnmatch.fnmatch(file_path, pattern):
                return True
        return False

    def is_blocked_path(self, file_path: str) -> bool:
        """
        Check if path is blocked (never auto-synced).

        Blocked paths include:
        - .env files
        - credentials/secrets
        - certain config files
        """
        blocked_patterns = [
            ".env*",
            "**/secrets.yaml",
            "**/secrets.yml",
            "**/credentials.json",
            "**/.htpasswd",
            "**/id_rsa*",
            "**/*.pem",
            "**/*.key",
        ]

        for pattern in blocked_patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
        return False

    def validate_target_path(self, file_path: str) -> Path:
        """
        Validate and resolve target path with security checks.

        Raises:
            SecurityError: If path traversal or other security violation detected
        """
        target = self.deploy_root / file_path

        # Resolve to absolute path
        try:
            resolved = target.resolve()
        except Exception as e:
            raise SecurityError(f"Invalid path: {file_path}")

        # Check path is within deploy_root (prevent traversal)
        try:
            resolved.relative_to(self.deploy_root.resolve())
        except ValueError:
            raise SecurityError(f"Path escape attempt detected: {file_path}")

        # Check blocked paths
        if self.is_blocked_path(file_path):
            raise SecurityError(f"Blocked path: {file_path}")

        return resolved

    async def sync_single_file(
        self,
        source_dir: str,
        file_path: str,
    ) -> Tuple[bool, str]:
        """
        Sync a single file with safety checks.

        Args:
            source_dir: Source directory (temp checkout)
            file_path: Relative file path

        Returns:
            (success, error_message)
        """
        try:
            source = Path(source_dir) / file_path
            target = self.validate_target_path(file_path)

            if source.exists():
                # Create parent directories
                target.parent.mkdir(parents=True, exist_ok=True)

                # Copy file with metadata
                shutil.copy2(source, target)
                return True, ""
            else:
                # File was deleted in new commit
                if target.exists():
                    target.unlink()
                    print(f"[SYNC] Deleted: {file_path}")
                return True, ""

        except SecurityError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Sync error: {str(e)}"

    async def sync_commit(
        self,
        commit_sha: str,
        update: PlatformUpdate,
        session: Session
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Sync a specific commit to the running platform.

        Args:
            commit_sha: Commit SHA to sync
            update: PlatformUpdate record to track progress
            session: Database session

        Returns:
            (success, synced_files, failed_files)
        """
        synced = []
        failed = []

        # Update status
        update.status = UpdateStatus.SYNCING.value
        update.started_at = datetime.utcnow()
        session.add(update)
        session.commit()

        try:
            # 1. Get list of changed files
            changed_files = self.get_changed_files(
                update.previous_commit_sha,
                commit_sha
            )
            update.files_changed = changed_files
            session.add(update)
            session.commit()

            print(f"[SYNC] Syncing {len(changed_files)} files...")

            # 2. Create temp checkout
            with tempfile.TemporaryDirectory(prefix="meta_sync_") as temp_dir:
                if not self.checkout_commit(temp_dir, commit_sha):
                    raise Exception("Failed to checkout commit")

                # 3. Sync each file
                for file_path in changed_files:
                    success, error = await self.sync_single_file(temp_dir, file_path)

                    if success:
                        synced.append(file_path)
                        print(f"[SYNC] ✓ {file_path}")
                    else:
                        failed.append(file_path)
                        print(f"[SYNC] ✗ {file_path}: {error}")

                # 4. Update record
                update.files_synced = synced
                update.files_failed = failed

            # 5. Update config with new commit
            if len(failed) == 0:
                self.config.current_commit = commit_sha
                self.config.last_deploy_at = datetime.utcnow()
                session.add(self.config)

            session.add(update)
            session.commit()

            return len(failed) == 0, synced, failed

        except Exception as e:
            print(f"[ERROR] Sync failed: {e}")
            update.status = UpdateStatus.FAILED.value
            update.deploy_log = str(e)
            update.completed_at = datetime.utcnow()
            session.add(update)
            session.commit()

            return False, synced, failed

    def get_file_content(self, commit_sha: str, file_path: str) -> Optional[str]:
        """
        Get content of a file at a specific commit.

        Args:
            commit_sha: Commit SHA
            file_path: File path in repo

        Returns:
            File content or None if not found
        """
        try:
            result = subprocess.run(
                ["git", "show", f"{commit_sha}:{file_path}"],
                cwd=str(self.bare_repo_path),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                return result.stdout
            return None
        except Exception:
            return None

    def get_diff(self, old_sha: Optional[str], new_sha: str) -> Optional[str]:
        """
        Get diff between commits.

        Args:
            old_sha: Previous commit SHA (None for first deploy)
            new_sha: New commit SHA

        Returns:
            Diff string or None
        """
        try:
            if old_sha is None:
                # Show full diff of all files
                cmd = ["git", "diff", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", new_sha]
            else:
                cmd = ["git", "diff", old_sha, new_sha]

            result = subprocess.run(
                cmd,
                cwd=str(self.bare_repo_path),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                return result.stdout[:50000]  # Limit size
            return None
        except Exception:
            return None
