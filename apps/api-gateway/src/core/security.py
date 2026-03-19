import os
import re

from fastapi import HTTPException

from agenthub_protocol.path_utils import ensure_safe_path

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
