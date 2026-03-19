import os
import re

from fastapi import HTTPException

from agenthub_protocol.path_utils import ensure_safe_path


_INSECURE_DEFAULTS = {
    "JWT_SECRET": "change-this-in-production",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production",
    "WECHAT_TOKEN": "agenthub_token",
}


def validate_security_env() -> None:
    """Validate critical security env vars and fail-fast on insecure config."""
    mode = os.getenv("APP_SECURITY_MODE", "strict").strip().lower()
    if mode not in {"strict", "warn"}:
        mode = "strict"

    problems: list[str] = []

    for key, bad in _INSECURE_DEFAULTS.items():
        value = os.getenv(key)
        if not value:
            problems.append(f"{key} is not set")
        elif value == bad:
            problems.append(f"{key} uses insecure default")

    for key in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        if not os.getenv(key):
            problems.append(f"{key} is not set")

    if not os.getenv("INTERNAL_API_TOKEN"):
        problems.append("INTERNAL_API_TOKEN is not set")

    if not problems:
        return

    message = "Security configuration invalid: " + "; ".join(problems)
    if mode == "warn":
        print(f"[security-warning] {message}")
        return
    raise RuntimeError(message)

STORE_ROOT = os.path.abspath("./agenthub_data/repos")

_REF_ALLOWED_RE = re.compile(r"^[A-Za-z0-9/_\-\.]+$")


def get_secure_repo_path(repo_name: str) -> str:
    """Ensures repo_name stays within STORE_ROOT."""
    try:
        return str(ensure_safe_path(STORE_ROOT, repo_name, "Invalid repository name"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def validate_blob_path(path: str):
    """Simple check to prevent escaping git tree structure via path parameter."""
    # Since we don't have a 'base' directory for the git tree yet here
    # (it's internal to git), we still use the basic check,
    # but we can also use ensure_safe_path with a dummy base if needed.
    # However, for git blobs, the path is relative to the repo root.
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if any(ch in path for ch in [":", "\\", "\x00"]) or path.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid file path")


def ensure_safe_ref(ref: str):
    if not _REF_ALLOWED_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid ref name")
    if ".." in ref or ref.startswith("/") or ref.endswith("/") or ref.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid ref name")
    if any(ch in ref for ch in [":", "~", "^", " ", "\\"]):
        raise HTTPException(status_code=400, detail="Invalid ref name")
