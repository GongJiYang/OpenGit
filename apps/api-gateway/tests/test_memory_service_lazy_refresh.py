from agent_auth.services import memory_service as memory_service_module
from agent_auth.services.memory_service import MemoryService


class _FakeMemoryClient:
    pass



def test_memory_service_lazy_init_honors_post_import_env(monkeypatch):
    service = MemoryService()

    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    assert service.initialize(force_refresh=True) is False
    assert service.enabled is False
    assert service.disabled_reason == "ZHIPUAI_API_KEY is not configured"

    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")

    captured = {}

    def _fake_from_config(config):
        captured["config"] = config
        return _FakeMemoryClient()

    monkeypatch.setattr(memory_service_module, "Memory", type("_M", (), {"from_config": staticmethod(_fake_from_config)}))
    monkeypatch.setattr(service, "_register_custom_providers", lambda: None)

    assert service.initialize(force_refresh=True) is True
    assert service.enabled is True
    assert isinstance(service.memory, _FakeMemoryClient)
    assert captured["config"]["llm"]["config"]["api_key"] == "test-key"



def test_memory_service_refresh_rebuilds_runtime_paths_and_memory(monkeypatch):
    service = MemoryService()

    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")

    created = []

    class _MemFactory:
        @staticmethod
        def from_config(config):
            created.append(config)
            return _FakeMemoryClient()

    monkeypatch.setattr(memory_service_module, "Memory", _MemFactory)
    monkeypatch.setattr(service, "_register_custom_providers", lambda: None)

    monkeypatch.setenv("MEM0_DIR", "/tmp/mem0-runtime-a")
    assert service.initialize(force_refresh=True) is True
    first_runtime_dir = service.runtime_dir

    monkeypatch.setenv("MEM0_DIR", "/tmp/mem0-runtime-b")
    assert service.refresh() is True

    assert len(created) == 2
    assert first_runtime_dir != service.runtime_dir
    assert service.runtime_dir.startswith("/tmp/mem0-runtime-b")



def test_get_memory_service_returns_singleton_instance():
    original_singleton = memory_service_module._memory_service_singleton
    try:
        memory_service_module._memory_service_singleton = None
        a = memory_service_module.get_memory_service()
        b = memory_service_module.get_memory_service()
        assert a is b
    finally:
        memory_service_module._memory_service_singleton = original_singleton



def test_memory_cache_key_changes_when_model_or_provider_config_changes():
    service = MemoryService()
    monkey_config_a = {
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "agent_memories", "path": "/tmp/q1"}},
        "llm": {"provider": "langchain", "config": {"api_key": "k1", "model": "glm-4-flash"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "k1", "model": "embedding-2"}},
    }
    monkey_config_b = {
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "agent_memories", "path": "/tmp/q1"}},
        "llm": {"provider": "langchain", "config": {"api_key": "k1", "model": "glm-4.5"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "k1", "model": "embedding-2"}},
    }
    monkey_config_c = {
        "vector_store": {"provider": "milvus", "config": {"collection_name": "agent_memories", "uri": "tcp://milvus"}},
        "llm": {"provider": "langchain", "config": {"api_key": "k1", "model": "glm-4-flash"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "k1", "model": "embedding-2"}},
    }

    key_a = service._cache_key(monkey_config_a)
    key_b = service._cache_key(monkey_config_b)
    key_c = service._cache_key(monkey_config_c)

    assert key_a != key_b
    assert key_a != key_c



def test_memory_cache_key_uses_api_key_fingerprint_without_plaintext_leak():
    service = MemoryService()
    config = {
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "agent_memories", "path": "/tmp/q1"}},
        "llm": {"provider": "langchain", "config": {"api_key": "plain-secret-key", "model": "glm-4-flash"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "plain-secret-key", "model": "embedding-2"}},
    }

    key = service._cache_key(config)

    assert isinstance(key, str)
    assert len(key) == 64
    assert "plain-secret-key" not in key



def test_memory_cache_key_changes_when_api_key_changes():
    service = MemoryService()
    config_a = {
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "agent_memories", "path": "/tmp/q1"}},
        "llm": {"provider": "langchain", "config": {"api_key": "k1", "model": "glm-4-flash"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "k1", "model": "embedding-2"}},
    }
    config_b = {
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "agent_memories", "path": "/tmp/q1"}},
        "llm": {"provider": "langchain", "config": {"api_key": "k2", "model": "glm-4-flash"}},
        "embedder": {"provider": "langchain", "config": {"api_key": "k2", "model": "embedding-2"}},
    }

    assert service._cache_key(config_a) != service._cache_key(config_b)
