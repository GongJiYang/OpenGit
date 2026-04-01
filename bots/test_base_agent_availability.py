from bots import base_agent as base_agent_module
from bots.base_agent import BaseAgent


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _TestAgent(BaseAgent):
    def __init__(self):
        self.agent_id = "agent-1"
        self.role = "tester"
        self.model_name = "gpt-4-turbo"
        self._logs = []

    def log(self, msg: str, emoji: str = "🤖"):
        self._logs.append((emoji, msg))



def test_resolve_error_distinguishes_kb_unavailable_from_no_hit(monkeypatch):
    agent = _TestAgent()

    monkeypatch.setattr(
        agent,
        "use_skill",
        lambda skill_name, **kwargs: {
            "found": False,
            "availability": {
                "ok": False,
                "unavailable": True,
                "error_code": "vector_store_unavailable",
                "reason": "Qdrant client unavailable",
            },
        },
    )

    result = agent.resolve_error("ImportError", "No module named x")

    assert result["source"] == "kb_unavailable_fallback"
    assert result["action"] == "reason_and_solve"
    assert result["solution_id"] is None
    assert result["availability"]["error_code"] == "vector_store_unavailable"
    assert agent._logs[-1] == ("⚠️", "KB 不可用，降级到 LLM 推理: Qdrant client unavailable")



def test_resolve_error_keeps_no_hit_path_distinct(monkeypatch):
    agent = _TestAgent()

    monkeypatch.setattr(
        agent,
        "use_skill",
        lambda skill_name, **kwargs: {
            "found": False,
            "availability": {
                "ok": True,
                "unavailable": False,
                "error_code": None,
                "reason": None,
            },
        },
    )

    result = agent.resolve_error("ImportError", "No module named x")

    assert result["source"] == "llm_inference"
    assert result["solution_id"] is None
    assert result["availability"]["ok"] is True
    assert agent._logs[-1] == ("🧠", "KB 未命中，启动 LLM 推理...")



def test_store_solution_logs_backend_unavailable_reason(monkeypatch):
    agent = _TestAgent()

    monkeypatch.setattr(
        agent,
        "use_skill",
        lambda skill_name, **kwargs: {
            "stored": False,
            "signature": None,
            "availability": {
                "ok": False,
                "unavailable": True,
                "error_code": "vector_store_unavailable",
                "reason": "Qdrant client unavailable",
            },
        },
    )

    stored = agent.store_solution(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
        solution_steps=["pip install x"],
    )

    assert stored is False
    assert agent._logs[-1] == ("⚠️", "解决方案未写入 KB（后端不可用）: Qdrant client unavailable")



def test_record_solution_feedback_logs_backend_unavailable_reason(monkeypatch):
    agent = _TestAgent()

    monkeypatch.setattr(
        agent,
        "use_skill",
        lambda skill_name, **kwargs: {
            "updated": False,
            "availability": {
                "ok": False,
                "unavailable": True,
                "error_code": "vector_store_unavailable",
                "reason": "Qdrant client unavailable",
            },
        },
    )

    updated = agent.record_solution_feedback("sol-1", "passed")

    assert updated is False
    assert agent._logs[-1] == ("⚠️", "命中方案反馈未写入 KB（后端不可用）: Qdrant client unavailable")



def test_search_code_logs_backend_unavailable_reason_and_returns_empty(monkeypatch):
    agent = _TestAgent()

    def _fake_get(url, params):
        assert params == {"query": "foo", "strict": True}
        return _FakeResponse(503, {
            "detail": {
                "message": "Semantic search backend unavailable",
                "error_code": "vector_store_unavailable",
                "reason": "Qdrant client unavailable",
            }
        })

    monkeypatch.setattr(base_agent_module.requests, "get", _fake_get)

    result = agent.search_code("foo")

    assert result == []
    assert agent._logs[-1] == ("⚠️", "语义搜索不可用，降级为空结果: Qdrant client unavailable")



def test_search_code_returns_results_when_search_succeeds(monkeypatch):
    agent = _TestAgent()

    def _fake_get(url, params):
        assert params == {"query": "foo", "strict": True}
        return _FakeResponse(200, [{"chunk_name": "x", "code_snippet": "y", "score": 0.9}])

    monkeypatch.setattr(base_agent_module.requests, "get", _fake_get)

    result = agent.search_code("foo")

    assert result == [{"chunk_name": "x", "code_snippet": "y", "score": 0.9}]
    assert agent._logs == []
