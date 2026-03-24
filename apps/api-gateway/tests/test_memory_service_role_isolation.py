from agent_auth.services.memory_service import MemoryService


class _DummyReadyMemoryService(MemoryService):
    def _ensure_ready(self) -> bool:
        return True


class _FakeMemoryBackend:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.delete_calls = []

    def add(self, content, agent_id, metadata):
        self.add_calls.append((content, agent_id, metadata))
        return {"id": "m-1"}

    def search(self, query, agent_id, limit):
        self.search_calls.append((query, agent_id, limit))
        return [{"content": "hit"}]

    def delete_all(self, agent_id):
        self.delete_calls.append(agent_id)



def _build_service_with_backend():
    service = _DummyReadyMemoryService.__new__(_DummyReadyMemoryService)
    service.memory = _FakeMemoryBackend()
    service.enabled = True
    service.disabled_reason = None
    service.collection_name = "agent_memories"
    service.history_db_path = "/tmp/history.db"
    service.runtime_root = "/tmp/runtime"
    service.local_qdrant_root = "/tmp/qdrant"
    service.runtime_dir = "/tmp/runtime/default"
    service.local_qdrant_path = "/tmp/qdrant/default"
    service._active_cache_key = None
    return service



def test_memory_service_namespaces_memories_by_agent_and_role():
    service = _build_service_with_backend()

    service.add_memory("agent-1", "hello", metadata={"k": "v"}, role="Architect")
    service.get_memories("agent-1", query="hello", role="Architect")
    service.delete_all_memories("agent-1", role="Architect")

    assert service.memory.add_calls[0][1] == "agent-1::role::architect"
    assert service.memory.search_calls[0][1] == "agent-1::role::architect"
    assert service.memory.delete_calls[0] == "agent-1::role::architect"



def test_memory_service_role_default_when_missing_or_invalid():
    service = _build_service_with_backend()

    service.add_memory("agent-1", "a", role=None)
    service.add_memory("agent-1", "b", role="@@@")

    add_ids = [call[1] for call in service.memory.add_calls]
    assert add_ids == ["agent-1::role::default", "agent-1::role::default"]
