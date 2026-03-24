from types import SimpleNamespace
from unittest.mock import patch


class _FakeSession:
    def __init__(self, binding=None, agent=None):
        self._binding = binding
        self._agent = agent

    def get(self, model, key):
        return self._agent

    def exec(self, statement):
        class _Result:
            def __init__(self, item):
                self._item = item

            def first(self):
                return self._item

        return _Result(self._binding)


class _FakeMemoryService:
    def __init__(self, memories):
        self.memories = memories
        self.calls = []

    def get_memories(self, agent_id, query=None, limit=5, role=None):
        self.calls.append((agent_id, query, role))
        return self.memories



def test_role_prompt_rejects_cross_agent_access_for_agent_principal(client, auth_headers):
    principal = SimpleNamespace(id="agent-self", kind="agent", status="claimed")
    client.app.dependency_overrides.clear()

    from app_factory import get_auth_session, require_active_identity

    client.app.dependency_overrides[require_active_identity] = lambda: principal
    client.app.dependency_overrides[get_auth_session] = lambda: iter([_FakeSession()])

    res = client.get(
        "/roles/architect/prompt",
        params={"agent_id": "agent-other"},
        headers=auth_headers,
    )

    assert res.status_code == 403
    assert "cannot access other agent memories" in res.json()["detail"]



def test_role_prompt_allows_owner_user_and_injects_memories(client, auth_headers):
    principal = SimpleNamespace(id="user-1", kind="user")
    binding = SimpleNamespace(user_id="user-1")
    agent = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
    fake_mem = _FakeMemoryService(memories=[{"content": "经验A"}])

    from app_factory import get_auth_session, require_active_identity

    client.app.dependency_overrides[require_active_identity] = lambda: principal
    client.app.dependency_overrides[get_auth_session] = lambda: _FakeSession(binding=binding, agent=agent)

    with patch("app_factory.get_memory_service", return_value=fake_mem):
        res = client.get(
            "/roles/architect/prompt",
            params={"agent_id": "11111111-1111-1111-1111-111111111111", "query": "blackbox"},
            headers=auth_headers,
        )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["role"] == "architect"
    assert "RELEVANT HISTORICAL EXPERIENCE" in data["prompt"]
    assert "经验A" in data["prompt"]
    assert fake_mem.calls == [("11111111-1111-1111-1111-111111111111", "blackbox", "architect")]



def test_role_prompt_rejects_user_without_binding_ownership(client, auth_headers):
    principal = SimpleNamespace(id="user-1", kind="user")
    binding = SimpleNamespace(user_id="user-2")
    agent = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")

    from app_factory import get_auth_session, require_active_identity

    client.app.dependency_overrides[require_active_identity] = lambda: principal
    client.app.dependency_overrides[get_auth_session] = lambda: _FakeSession(binding=binding, agent=agent)

    res = client.get(
        "/roles/architect/prompt",
        params={"agent_id": "11111111-1111-1111-1111-111111111111"},
        headers=auth_headers,
    )

    assert res.status_code == 403
    assert "cannot access other agent memories" in res.json()["detail"]
