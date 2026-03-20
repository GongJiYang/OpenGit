import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_auth.services.authz import start_scheduler, stop_scheduler
from agenthub_execution_vmm.sandbox import SubprocessSandbox
from agenthub_git_core.repo_manager import RepoManager
from agenthub_semantic_store.ast_parser import PythonASTParser
from agenthub_semantic_store.indexer import VectorIndexer

from core.security import validate_security_env
from core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_env()

    settings = get_settings()

    # 读取开关（默认关闭，测试安全）
    enable_indexer = settings.app_enable_indexer
    enable_sandbox = settings.app_enable_sandbox

    # 统一初始化到 app.state
    app.state.store_root = settings.store_root
    os.makedirs(app.state.store_root, exist_ok=True)

    app.state.repo_manager = RepoManager(app.state.store_root)

    app.state.indexer = None
    if enable_indexer:
        idx = VectorIndexer(collection_name="agenthub_prod", embedding_dim=1024)
        if not getattr(idx.embedder, "client", None):
            # 缺密钥时不启
            idx = None
        app.state.indexer = idx

    app.state.parser = PythonASTParser()

    app.state.sandbox = SubprocessSandbox() if enable_sandbox else None

    # 可选：按需启动 scheduler（多 pod 场景建议加开关 RUN_SCHEDULER=1）
    if settings.run_scheduler:
        def session_factory():
            from persistence import get_session as _get_session

            # Wrap generator to function returning a Session context manager
            class _Factory:
                def __enter__(self):
                    self._gen = _get_session()
                    return next(self._gen)

                def __exit__(self, exc_type, exc, tb):
                    try:
                        next(self._gen)
                    except StopIteration:
                        pass

            return _Factory()

        start_scheduler(session_factory)

    try:
        yield
    finally:
        # 关闭 sandbox / scheduler
        if settings.run_scheduler:
            stop_scheduler()
        # 如 indexer/sandbox 有 close()，在此清理
