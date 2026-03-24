import asyncio
from typing import Optional

from fastapi import FastAPI

from core import lifespan as lifespan_module
from agenthub_execution_vmm.sandbox import SubprocessSandbox
from agenthub_execution_vmm.session_store import InMemorySessionStore


class _RepoManagerStub:
    def __init__(self, storage_root):
        self.storage_root = storage_root
        self.refresh_calls = 0

    def refresh_existing_hooks(self) -> int:
        self.refresh_calls += 1
        return 2


class _IndexerStub:
    def __init__(self, *args, **kwargs):
        self.embedder = type("Embedder", (), {"client": None})()


class _SettingsStub:
    app_enable_indexer = False
    app_enable_sandbox = False
    app_allow_insecure_subprocess_sandbox = False
    run_scheduler = False
    app_session_store_backend = "memory"
    app_session_store_redis_url = None
    app_session_ttl_seconds = 1800

    def __init__(
        self,
        store_root: str,
        sandbox_provider: str = "disabled",
        security_mode: str = "strict",
        session_store_backend: str = "memory",
        session_store_redis_url: Optional[str] = None,
    ):
        self.store_root = store_root
        self.app_sandbox_provider = sandbox_provider
        self.app_security_mode = security_mode
        self.app_session_store_backend = session_store_backend
        self.app_session_store_redis_url = session_store_redis_url

    @property
    def normalized_sandbox_provider(self) -> str:
        provider = (self.app_sandbox_provider or "disabled").strip().lower()
        return provider if provider in {"disabled", "subprocess", "runner"} else "disabled"

    @property
    def normalized_security_mode(self) -> str:
        mode = (self.app_security_mode or "strict").strip().lower()
        return mode if mode in {"strict", "warn"} else "strict"

    @property
    def normalized_session_store_backend(self) -> str:
        backend = (self.app_session_store_backend or "memory").strip().lower()
        return backend if backend in {"memory", "redis"} else "memory"


def test_lifespan_refreshes_existing_repo_hooks_on_startup(monkeypatch, tmp_path):
    monkeypatch.setattr(lifespan_module, "validate_security_env", lambda: None)
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: _SettingsStub(str(tmp_path / "repos")))
    monkeypatch.setattr(lifespan_module, "RepoManager", _RepoManagerStub)
    monkeypatch.setattr(lifespan_module, "VectorIndexer", _IndexerStub)

    app = FastAPI()

    async def _run():
        async with lifespan_module.lifespan(app):
            repo_manager = app.state.repo_manager
            assert isinstance(repo_manager, _RepoManagerStub)
            assert repo_manager.refresh_calls == 1
            assert app.state.store_root == str(tmp_path / "repos")
            assert app.state.sandbox is None
            assert isinstance(app.state.session_store, InMemorySessionStore)
            assert app.state.session_manager is None

    asyncio.run(_run())


def test_lifespan_disables_subprocess_sandbox_in_strict_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(lifespan_module, "validate_security_env", lambda: None)
    monkeypatch.setattr(
        lifespan_module,
        "get_settings",
        lambda: _SettingsStub(str(tmp_path / "repos"), sandbox_provider="subprocess", security_mode="strict"),
    )
    monkeypatch.setattr(lifespan_module, "RepoManager", _RepoManagerStub)
    monkeypatch.setattr(lifespan_module, "VectorIndexer", _IndexerStub)

    app = FastAPI()

    async def _run():
        async with lifespan_module.lifespan(app):
            assert app.state.sandbox is None
            assert app.state.session_manager is None

    asyncio.run(_run())


def test_lifespan_uses_subprocess_sandbox_in_warn_mode_with_explicit_allow(monkeypatch, tmp_path):
    monkeypatch.setattr(lifespan_module, "validate_security_env", lambda: None)

    def _settings():
        s = _SettingsStub(str(tmp_path / "repos"), sandbox_provider="subprocess", security_mode="warn")
        s.app_allow_insecure_subprocess_sandbox = True
        return s

    monkeypatch.setattr(lifespan_module, "get_settings", _settings)
    monkeypatch.setattr(lifespan_module, "RepoManager", _RepoManagerStub)
    monkeypatch.setattr(lifespan_module, "VectorIndexer", _IndexerStub)

    app = FastAPI()

    async def _run():
        async with lifespan_module.lifespan(app):
            assert isinstance(app.state.sandbox, SubprocessSandbox)
            assert app.state.session_manager is not None

    asyncio.run(_run())


def test_lifespan_uses_redis_session_store_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(lifespan_module, "validate_security_env", lambda: None)

    def _settings():
        s = _SettingsStub(
            str(tmp_path / "repos"),
            sandbox_provider="runner",
            session_store_backend="redis",
            session_store_redis_url="redis://test:6379/0",
        )
        return s

    class _RedisStoreStub:
        def __init__(self, redis_url):
            self.redis_url = redis_url

    monkeypatch.setattr(lifespan_module, "get_settings", _settings)
    monkeypatch.setattr(lifespan_module, "RepoManager", _RepoManagerStub)
    monkeypatch.setattr(lifespan_module, "VectorIndexer", _IndexerStub)
    monkeypatch.setattr(lifespan_module, "RedisSessionStore", _RedisStoreStub)

    app = FastAPI()

    async def _run():
        async with lifespan_module.lifespan(app):
            assert app.state.sandbox is None
            assert app.state.session_manager is None
            assert isinstance(app.state.session_store, _RedisStoreStub)
            assert app.state.session_store.redis_url == "redis://test:6379/0"

    asyncio.run(_run())


def test_lifespan_disables_sandbox_when_provider_is_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(lifespan_module, "validate_security_env", lambda: None)
    monkeypatch.setattr(
        lifespan_module,
        "get_settings",
        lambda: _SettingsStub(str(tmp_path / "repos"), sandbox_provider="runner"),
    )
    monkeypatch.setattr(lifespan_module, "RepoManager", _RepoManagerStub)
    monkeypatch.setattr(lifespan_module, "VectorIndexer", _IndexerStub)

    app = FastAPI()

    async def _run():
        async with lifespan_module.lifespan(app):
            assert app.state.sandbox is None
            assert app.state.session_manager is None
            assert isinstance(app.state.session_store, InMemorySessionStore)

    asyncio.run(_run())
