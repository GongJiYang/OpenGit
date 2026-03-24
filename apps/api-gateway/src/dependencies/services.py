from typing import Optional

from fastapi import HTTPException, Request

from agenthub_execution_vmm.sandbox import Sandbox
from agenthub_git_core.repo_manager import RepoManager
from agenthub_semantic_store.indexer import VectorIndexer


def get_repo_manager(request: Request) -> RepoManager:
    mgr = getattr(request.app.state, "repo_manager", None)
    if not mgr:
        raise HTTPException(status_code=500, detail="RepoManager not initialized")
    return mgr


def get_indexer(request: Request) -> Optional[VectorIndexer]:
    return getattr(request.app.state, "indexer", None)


def get_sandbox(request: Request) -> Optional[Sandbox]:
    return getattr(request.app.state, "sandbox", None)
