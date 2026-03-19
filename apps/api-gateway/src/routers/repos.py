import os
import subprocess

from fastapi import APIRouter, HTTPException

from core.security import get_secure_repo_path, validate_blob_path

router = APIRouter()


@router.get("/api/v1/repos/{repo_name}/tree")
@router.get("/repos/{repo_name}/tree")
def get_repo_tree(repo_name: str):
    """List all files in the repo (HEAD)."""
    repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    try:
        # git ls-tree -r --name-only HEAD
        cmd = ["git", "ls-tree", "-r", "--name-only", "HEAD"]
        output = subprocess.check_output(cmd, cwd=repo_path, stderr=subprocess.DEVNULL).decode()
        return {"files": output.splitlines()}
    except subprocess.CalledProcessError:
        return {"files": []}  # Empty repo or no commits


@router.get("/api/v1/repos/{repo_name}/blob")
@router.get("/repos/{repo_name}/blob")
def get_repo_file(repo_name: str, path: str):
    """Get code content of a file."""
    validate_blob_path(path)
    repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    try:
        # git show HEAD:path/to/file
        # Note: path security check should be here in prod
        cmd = ["git", "show", f"HEAD:{path}"]
        content = subprocess.check_output(cmd, cwd=repo_path, stderr=subprocess.PIPE).decode()
        return {"content": content}
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=404, detail="File not found or cannot read")
