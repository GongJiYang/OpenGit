from skills.library import solution_ops


class _FakeKB:
    init_count = 0

    def __init__(self):
        type(self).init_count += 1
        self._items = []

    def store_solution(self, record):
        signature = f"{record.error_type}:{len(self._items)}"
        record.error_signature = signature
        self._items.append(record)
        return True

    def get_best_match(self, error_type, error_message, stack_trace="", environment=""):
        for record in self._items:
            if record.error_type == error_type and record.error_message == error_message:
                return {
                    "score": 1.0,
                    "solution": {
                        "error_type": record.error_type,
                        "error_message": record.error_message,
                        "solution_steps": record.solution_steps,
                        "solution_code": record.solution_code,
                        "environment": record.environment,
                        "confidence": record.confidence,
                        "usage_count": record.usage_count,
                        "agent_id": record.agent_id,
                    },
                }
        return None

    def get_stats(self):
        return {"total_solutions": len(self._items)}


def test_solution_skills_share_single_kb_instance(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", None)
    _FakeKB.init_count = 0
    monkeypatch.setattr(solution_ops, "SolutionKnowledgeBase", _FakeKB)

    search = solution_ops.SearchSolutionSkill()
    store = solution_ops.StoreSolutionSkill()
    batch = solution_ops.BatchSearchSolutionSkill()
    stats = solution_ops.GetSolutionStatsSkill()

    assert _FakeKB.init_count == 1
    assert search.kb is store.kb is batch.kb is stats.kb
    assert batch.search_skill.kb is batch.kb


def test_store_then_search_uses_shared_kb_state(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", None)
    _FakeKB.init_count = 0
    monkeypatch.setattr(solution_ops, "SolutionKnowledgeBase", _FakeKB)

    store = solution_ops.StoreSolutionSkill()
    search = solution_ops.SearchSolutionSkill()

    stored = store.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
        environment="py3.11",
        solution_steps=["pip install x"],
        solution_code="pip install x",
        result="passed",
        agent_id="agent-1",
        confidence=0.9,
    )

    looked_up = search.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
    )

    assert stored["stored"] is True
    assert looked_up["found"] is True
    assert looked_up["solution"]["solution_steps"] == ["pip install x"]
    assert looked_up["solution"]["agent_id"] == "agent-1"
