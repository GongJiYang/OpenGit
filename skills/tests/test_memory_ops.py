from skills.library import memory_ops


class _FakeMemoryService:
    def __init__(self, add_result, memories=None):
        self._add_result = add_result
        self._memories = memories or []
        self.calls = []

    def add_memory(self, agent_id, content, metadata=None, role=None):
        self.calls.append(("add", agent_id, content, metadata, role))
        return self._add_result

    def get_memories(self, agent_id, query, role=None):
        self.calls.append(("search", agent_id, query, role))
        return self._memories


def test_persistent_memory_add_returns_error_when_storage_fails(monkeypatch):
    fake = _FakeMemoryService(add_result=None)
    monkeypatch.setattr(memory_ops, "get_memory_service", lambda: fake)

    skill = memory_ops.PersistentMemorySkill()
    result = skill.execute(
        action="add",
        content="remember this",
        agent_id="agent-1",
        role="architect",
        metadata={"k": "v"},
    )

    assert result["status"] == "error"
    assert result["message"] == "Memory save failed"
    assert result["result"] is None
    assert fake.calls[0] == ("add", "agent-1", "remember this", {"k": "v"}, "architect")


def test_persistent_memory_add_returns_success_when_storage_succeeds(monkeypatch):
    stored = [{"id": "m1"}]
    fake = _FakeMemoryService(add_result=stored)
    monkeypatch.setattr(memory_ops, "get_memory_service", lambda: fake)

    skill = memory_ops.PersistentMemorySkill()
    result = skill.execute(
        action="add",
        content="remember this",
        agent_id="agent-1",
        role="reviewer",
        metadata={"k": "v"},
    )

    assert result["status"] == "success"
    assert result["message"] == "Memory saved"
    assert result["result"] == stored
    assert fake.calls[0] == ("add", "agent-1", "remember this", {"k": "v"}, "reviewer")



def test_persistent_memory_search_forwards_role_namespace(monkeypatch):
    fake = _FakeMemoryService(add_result=None, memories=[{"content": "x"}])
    monkeypatch.setattr(memory_ops, "get_memory_service", lambda: fake)

    skill = memory_ops.PersistentMemorySkill()
    result = skill.execute(
        action="search",
        content="query",
        agent_id="agent-1",
        role="tester",
    )

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["memories"] == [{"content": "x"}]
    assert fake.calls[0] == ("search", "agent-1", "query", "tester")
