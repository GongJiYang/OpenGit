import logging
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Optional


logger = logging.getLogger(__name__)

def _load_ensure_safe_path():
    """Load protocol path utility with monorepo path fallback."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    protocol_path = os.path.abspath(os.path.join(base_dir, "../../../../packages/protocol/src"))
    if protocol_path not in sys.path:
        sys.path.append(protocol_path)

    from agenthub_protocol.path_utils import ensure_safe_path

    return ensure_safe_path


def _resolve_hook_runtime_defaults() -> tuple[Optional[str], Optional[str]]:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    git_core_src = os.path.abspath(os.path.join(base_dir, ".."))
    if not os.path.isdir(os.path.join(git_core_src, "agenthub_git_core")):
        git_core_src = None

    protocol_src: Optional[str] = None
    try:
        import agenthub_protocol

        installed_protocol_src = os.path.abspath(os.path.join(os.path.dirname(agenthub_protocol.__file__), ".."))
        if os.path.isdir(os.path.join(installed_protocol_src, "agenthub_protocol")):
            protocol_src = installed_protocol_src
    except Exception:
        protocol_src = None

    if protocol_src is None:
        monorepo_protocol_src = os.path.abspath(os.path.join(base_dir, "../../../../packages/protocol/src"))
        if os.path.isdir(os.path.join(monorepo_protocol_src, "agenthub_protocol")):
            protocol_src = monorepo_protocol_src

    return git_core_src, protocol_src


def _build_runtime_hook_wrapper() -> str:
    default_git_core_src, default_protocol_src = _resolve_hook_runtime_defaults()
    default_git_core_src_literal = shlex.quote(default_git_core_src) if default_git_core_src else "''"
    default_protocol_src_literal = shlex.quote(default_protocol_src) if default_protocol_src else "''"

    return f"""#!/bin/sh
# AgentHub Hook Wrapper (runtime-resolved)
set -eu

DEFAULT_GIT_CORE_SRC={default_git_core_src_literal}
DEFAULT_PROTOCOL_SRC={default_protocol_src_literal}

resolve_python() {{
  if [ -n "${{AGENTHUB_HOOK_PYTHON:-}}" ]; then
    printf '%s' "${{AGENTHUB_HOOK_PYTHON}}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "python"
    return 0
  fi
  return 1
}}

resolve_valid_src() {{
  candidate="$1"
  package_dir="$2"
  if [ -z "${{candidate}}" ]; then
    return 0
  fi
  if [ -d "${{candidate}}/${{package_dir}}" ]; then
    printf '%s' "${{candidate}}"
  fi
}}

can_import_hook_module() {{
  "${{PYTHON_BIN}}" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('agenthub_git_core.hook_logic') else 1)" >/dev/null 2>&1
}}

run_hook_module() {{
  exec "${{PYTHON_BIN}}" -m agenthub_git_core.hook_logic
}}

PYTHON_BIN="$(resolve_python || true)"
if [ -z "${{PYTHON_BIN}}" ]; then
  echo "❌ REJECTED: Python runtime not found for AgentHub hook." >&2
  exit 1
fi

if can_import_hook_module; then
  run_hook_module
fi

RESOLVED_GIT_CORE_SRC="$(resolve_valid_src "${{AGENTHUB_GIT_CORE_SRC:-${{DEFAULT_GIT_CORE_SRC}}}}" "agenthub_git_core")"
RESOLVED_PROTOCOL_SRC="$(resolve_valid_src "${{AGENTHUB_PROTOCOL_SRC:-${{DEFAULT_PROTOCOL_SRC}}}}" "agenthub_protocol")"

EXTRA_PYTHONPATH=""
if [ -n "${{RESOLVED_GIT_CORE_SRC}}" ]; then
  EXTRA_PYTHONPATH="${{RESOLVED_GIT_CORE_SRC}}"
fi
if [ -n "${{RESOLVED_PROTOCOL_SRC}}" ]; then
  EXTRA_PYTHONPATH="${{EXTRA_PYTHONPATH:+${{EXTRA_PYTHONPATH}}:}}${{RESOLVED_PROTOCOL_SRC}}"
fi

if [ -n "${{EXTRA_PYTHONPATH}}" ]; then
  export PYTHONPATH="${{EXTRA_PYTHONPATH}}${{PYTHONPATH:+:${{PYTHONPATH}}}}"
  if can_import_hook_module; then
    run_hook_module
  fi
fi

echo "❌ REJECTED: hook runtime unavailable for AgentHub hook module." >&2
echo "   python=${{PYTHON_BIN}}" >&2
echo "   AGENTHUB_GIT_CORE_SRC=${{AGENTHUB_GIT_CORE_SRC:-<unset>}}" >&2
echo "   AGENTHUB_PROTOCOL_SRC=${{AGENTHUB_PROTOCOL_SRC:-<unset>}}" >&2
echo "   default_git_core_src=${{DEFAULT_GIT_CORE_SRC:-<unset>}}" >&2
echo "   default_protocol_src=${{DEFAULT_PROTOCOL_SRC:-<unset>}}" >&2
echo "   resolved_git_core_src=${{RESOLVED_GIT_CORE_SRC:-<unset>}}" >&2
echo "   resolved_protocol_src=${{RESOLVED_PROTOCOL_SRC:-<unset>}}" >&2
echo "   Install agenthub-git-core in hook runtime, or set AGENTHUB_GIT_CORE_SRC and AGENTHUB_PROTOCOL_SRC to valid source roots." >&2
exit 1
"""


class RepoManager:
    def __init__(self, storage_root: str):
        self.storage_root = os.path.abspath(storage_root)
        os.makedirs(self.storage_root, exist_ok=True)
        self._ensure_safe_path = _load_ensure_safe_path()

    @staticmethod
    def _new_operation_id() -> str:
        return secrets.token_hex(8)

    @staticmethod
    def _normalize_idempotency_token(token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        cleaned = token.strip()
        return cleaned or None

    @staticmethod
    def _idempotency_marker_path(repo_path: str) -> str:
        return os.path.join(repo_path, "hooks", ".agenthub-create-idempotency-token")

    @classmethod
    def _write_idempotency_marker(cls, repo_path: str, token: Optional[str]) -> None:
        if not token:
            return
        marker_path = cls._idempotency_marker_path(repo_path)
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as marker_file:
            marker_file.write(token)

    @classmethod
    def _read_idempotency_marker(cls, repo_path: str) -> Optional[str]:
        marker_path = cls._idempotency_marker_path(repo_path)
        if not os.path.exists(marker_path):
            return None
        with open(marker_path, "r", encoding="utf-8") as marker_file:
            return marker_file.read().strip() or None

    def create_repo(
        self,
        repo_name: str,
        *,
        actor_id: str = "unknown",
        idempotency_token: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> str:
        """Initialize a bare git repo and install the AgentHub hook."""
        op_id = self._new_operation_id()
        idem_token = self._normalize_idempotency_token(idempotency_token)
        actor = actor_id or "unknown"
        req_id = request_id or "-"

        try:
            repo_path_obj = self._ensure_safe_path(
                self.storage_root,
                repo_name,
                "Invalid repository name",
            )
            repo_path = str(repo_path_obj)
        except ValueError as e:
            logger.warning(
                "[repo_manager][create_repo][rejected] op_id=%s request_id=%s actor=%s repo=%s reason=%s",
                op_id,
                req_id,
                actor,
                repo_name,
                str(e),
            )
            raise ValueError(str(e))

        # Enforce simple naming: single level and .git suffix
        if "/" in repo_name or "\\" in repo_name:
            logger.warning(
                "[repo_manager][create_repo][rejected] op_id=%s request_id=%s actor=%s repo=%s reason=nested-path",
                op_id,
                req_id,
                actor,
                repo_name,
            )
            raise ValueError("Invalid repository name: nested paths are not allowed")
        if not repo_name.endswith(".git"):
            logger.warning(
                "[repo_manager][create_repo][rejected] op_id=%s request_id=%s actor=%s repo=%s reason=missing-dot-git",
                op_id,
                req_id,
                actor,
                repo_name,
            )
            raise ValueError("Invalid repository name: must end with .git")

        if os.path.exists(repo_path):
            if idem_token:
                existing_token = self._read_idempotency_marker(repo_path)
                if existing_token and existing_token == idem_token:
                    logger.info(
                        "[repo_manager][create_repo][idempotent-hit] op_id=%s request_id=%s actor=%s repo=%s",
                        op_id,
                        req_id,
                        actor,
                        repo_name,
                    )
                    return repo_path
            logger.warning(
                "[repo_manager][create_repo][rejected] op_id=%s request_id=%s actor=%s repo=%s reason=already-exists",
                op_id,
                req_id,
                actor,
                repo_name,
            )
            raise ValueError("Repository already exists")

        logger.info(
            "[repo_manager][create_repo][start] op_id=%s request_id=%s actor=%s repo=%s",
            op_id,
            req_id,
            actor,
            repo_name,
        )

        subprocess.run(["git", "init", "--bare", repo_path], check=True, capture_output=True)
        try:
            self.install_hook(repo_path, actor_id=actor, request_id=req_id, op_id=op_id)
            self._write_idempotency_marker(repo_path, idem_token)
        except Exception as e:
            shutil.rmtree(repo_path, ignore_errors=True)
            logger.exception(
                "[repo_manager][create_repo][rollback] op_id=%s request_id=%s actor=%s repo=%s error=%s",
                op_id,
                req_id,
                actor,
                repo_name,
                str(e),
            )
            raise RuntimeError(f"Failed to install hook: {e}")

        logger.info(
            "[repo_manager][create_repo][success] op_id=%s request_id=%s actor=%s repo=%s path=%s",
            op_id,
            req_id,
            actor,
            repo_name,
            repo_path,
        )
        return repo_path

    def install_hook(
        self,
        repo_path: str,
        *,
        actor_id: str = "system",
        request_id: Optional[str] = None,
        op_id: Optional[str] = None,
    ):
        """Install runtime-resilient pre-receive hook wrapper."""
        hooks_dir = os.path.join(repo_path, "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        hook_path = os.path.join(hooks_dir, "pre-receive")

        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(_build_runtime_hook_wrapper())

        st = os.stat(hook_path)
        os.chmod(hook_path, st.st_mode | stat.S_IEXEC)
        logger.info(
            "[repo_manager][install_hook] op_id=%s request_id=%s actor=%s repo_path=%s hook=%s",
            op_id or "-",
            request_id or "-",
            actor_id or "unknown",
            repo_path,
            hook_path,
        )

    def refresh_existing_hooks(self) -> int:
        """Reinstall pre-receive wrapper for all existing repositories."""
        refreshed = 0
        if not os.path.isdir(self.storage_root):
            return refreshed

        for entry in os.scandir(self.storage_root):
            if not entry.is_dir() or not entry.name.endswith(".git"):
                continue
            self.install_hook(entry.path)
            refreshed += 1

        return refreshed

if __name__ == "__main__":
    # Test creation
    # Ensure current dir is writable or use a temp dir
    mgr = RepoManager("./temp_git_store")
    repo = mgr.create_repo("test-project.git")
    print(f"Unit Test Repo Created: {repo}")
