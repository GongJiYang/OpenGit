import sqlite3

from agent_auth.services import memory_service as memory_service_module
from agent_auth.services.memory_service import MemoryService


class _DummyReadyMemoryService(MemoryService):
    def _ensure_ready(self) -> bool:
        return True


class _FakeMemoryBackend:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.delete_calls = []
        self.delete_memory_calls = []
        self._next_id = 1
        self.search_results = {}

    def add(self, content, agent_id, metadata):
        self.add_calls.append((content, agent_id, metadata))
        memory_id = f"m-{self._next_id}"
        self._next_id += 1
        return {"id": memory_id}

    def search(self, query, agent_id, limit):
        self.search_calls.append((query, agent_id, limit))
        return list(self.search_results.get(agent_id, [{"content": "hit"}]))

    def delete(self, memory_id):
        self.delete_memory_calls.append(memory_id)

    def delete_all(self, agent_id):
        self.delete_calls.append(agent_id)



def _build_service_with_backend(tmp_path):
    service = _DummyReadyMemoryService.__new__(_DummyReadyMemoryService)
    service.memory = _FakeMemoryBackend()
    service.enabled = True
    service.disabled_reason = None
    service.collection_name = "agent_memories"
    service.history_db_path = str(tmp_path / "history.db")
    service.runtime_root = str(tmp_path / "runtime")
    service.local_qdrant_root = str(tmp_path / "qdrant")
    service.runtime_dir = str(tmp_path / "runtime" / "default")
    service.local_qdrant_path = str(tmp_path / "qdrant" / "default")
    service.governance_db_path = str(tmp_path / "runtime" / "default" / "governance.db")
    service._active_cache_key = None
    return service



def test_memory_service_namespaces_memories_by_agent_and_role(tmp_path):
    service = _build_service_with_backend(tmp_path)

    service.add_memory("agent-1", "hello", metadata={"k": "v"}, role="Architect")
    service.get_memories("agent-1", query="hello", role="Architect")
    service.delete_all_memories("agent-1", role="Architect")

    assert service.memory.add_calls[0][1] == "agent-1::role::architect"
    assert service.memory.search_calls[0][1] == "agent-1::role::architect"
    assert service.memory.delete_calls[0] == "agent-1::role::architect"



def test_memory_service_role_default_when_missing_or_invalid(tmp_path):
    service = _build_service_with_backend(tmp_path)

    service.add_memory("agent-1", "a", role=None)
    service.add_memory("agent-1", "b", role="@@@")

    add_ids = [call[1] for call in service.memory.add_calls]
    assert add_ids == ["agent-1::role::default", "agent-1::role::default"]



def test_memory_service_deduplicates_same_memory_with_volatile_labels_removed(tmp_path):
    service = _build_service_with_backend(tmp_path)

    metadata = {"kind": "success_case", "skill_name": "search_solution"}
    content_a = '{"template":"skill_success_case_v1","skill":"search_solution","args":{"q":"x"},"result_summary":{"ok":true,"message":"ok"},"labels":{"trace_id":"t-1","duration_ms":12}}'
    content_b = '{"template":"skill_success_case_v1","skill":"search_solution","args":{"q":"x"},"result_summary":{"ok":true,"message":"ok"},"labels":{"trace_id":"t-2","duration_ms":88}}'

    first = service.add_memory("agent-1", content_a, metadata=metadata, role="architect")
    second = service.add_memory("agent-1", content_b, metadata=metadata, role="architect")

    assert first == {"id": "m-1"}
    assert second == {"status": "deduplicated", "fingerprint": service._memory_fingerprint(content_a, {**metadata, "memory_role": "architect", "memory_scope": "private"})}
    assert len(service.memory.add_calls) == 1



def test_memory_service_prunes_governance_rows_to_namespace_limit(tmp_path, monkeypatch):
    service = _build_service_with_backend(tmp_path)
    monkeypatch.setenv("MEM0_NAMESPACE_LIMIT", "2")

    service.add_memory("agent-1", "a", metadata={"kind": "success_case"}, role="architect")
    service.add_memory("agent-1", "b", metadata={"kind": "success_case"}, role="architect")
    service.add_memory("agent-1", "c", metadata={"kind": "success_case"}, role="architect")

    conn = sqlite3.connect(service.governance_db_path)
    try:
        rows = conn.execute(
            "SELECT content FROM memory_governance WHERE agent_id = ? ORDER BY created_at ASC",
            ("agent-1::role::architect",),
        ).fetchall()
    finally:
        conn.close()

    assert [row[0] for row in rows] == ["b", "c"]
    assert service.memory.delete_memory_calls == ["m-1"]



def test_memory_service_delete_all_clears_governance_namespace(tmp_path):
    service = _build_service_with_backend(tmp_path)
    service.add_memory("agent-1", "a", metadata={"kind": "success_case"}, role="architect")
    service.add_memory("agent-1", "b", metadata={"kind": "success_case"}, role="architect")

    service.delete_all_memories("agent-1", role="architect")

    conn = sqlite3.connect(service.governance_db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_governance WHERE agent_id = ?",
            ("agent-1::role::architect",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert service.memory.delete_calls == ["agent-1::role::architect"]
    assert count == 0



def test_memory_service_prunes_expired_rows_by_ttl(tmp_path, monkeypatch):
    service = _build_service_with_backend(tmp_path)
    monkeypatch.setenv("MEM0_MEMORY_TTL_SECONDS", "10")

    now = 1_000.0
    monkeypatch.setattr(memory_service_module.time, "time", lambda: now)
    service.add_memory("agent-1", "old", metadata={"kind": "success_case"}, role="architect")

    monkeypatch.setattr(memory_service_module.time, "time", lambda: now + 11)
    service.add_memory("agent-1", "new", metadata={"kind": "success_case"}, role="architect")

    conn = sqlite3.connect(service.governance_db_path)
    try:
        rows = conn.execute(
            "SELECT content FROM memory_governance WHERE agent_id = ? ORDER BY created_at ASC",
            ("agent-1::role::architect",),
        ).fetchall()
    finally:
        conn.close()

    assert [row[0] for row in rows] == ["new"]
    assert service.memory.delete_memory_calls == ["m-1"]



def test_memory_service_shared_scope_uses_reserved_namespace(tmp_path):
    service = _build_service_with_backend(tmp_path)

    service.add_memory("agent-1", "hello", metadata={"k": "v"}, role="Architect", scope="shared")
    service.get_memories("agent-1", query="hello", role="Architect", scope="shared")
    service.delete_all_memories("agent-1", role="Architect", scope="shared")

    assert service.memory.add_calls[0][1] == "__shared__::role::architect"
    assert service.memory.search_calls[0][1] == "__shared__::role::architect"
    assert service.memory.delete_calls[0] == "__shared__::role::architect"
    assert service.memory.add_calls[0][2]["memory_scope"] == "shared"



def test_memory_service_combined_reads_private_then_shared(tmp_path):
    service = _build_service_with_backend(tmp_path)
    service.memory.search_results = {
        "agent-1::role::architect": [{"content": "private-hit"}],
        "__shared__::role::architect": [{"content": "shared-hit"}],
    }

    results = service.get_memories("agent-1", query="hello", role="Architect", scope="combined")

    assert service.memory.search_calls == [
        ("hello", "agent-1::role::architect", 5),
        ("hello", "__shared__::role::architect", 5),
    ]
    assert results == [{"content": "private-hit"}, {"content": "shared-hit"}]



def test_memory_service_combined_reads_deduplicate_duplicate_hits(tmp_path):
    service = _build_service_with_backend(tmp_path)
    duplicate = {"content": "same"}
    service.memory.search_results = {
        "agent-1::role::architect": [duplicate],
        "__shared__::role::architect": [duplicate, {"content": "other"}],
    }

    results = service.get_memories("agent-1", query="hello", role="Architect", scope="combined")

    assert results == [{"content": "same"}, {"content": "other"}]



def test_memory_service_delete_all_shared_only_clears_shared_namespace(tmp_path):
    service = _build_service_with_backend(tmp_path)
    service.add_memory("agent-1", "private", metadata={"kind": "success_case"}, role="architect")
    service.add_memory("agent-1", "shared", metadata={"kind": "success_case"}, role="architect", scope="shared")

    service.delete_all_memories("agent-1", role="architect", scope="shared")

    conn = sqlite3.connect(service.governance_db_path)
    try:
        private_count = conn.execute(
            "SELECT COUNT(*) FROM memory_governance WHERE agent_id = ?",
            ("agent-1::role::architect",),
        ).fetchone()[0]
        shared_count = conn.execute(
            "SELECT COUNT(*) FROM memory_governance WHERE agent_id = ?",
            ("__shared__::role::architect",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert service.memory.delete_calls == ["__shared__::role::architect"]
    assert private_count == 1
    assert shared_count == 0
