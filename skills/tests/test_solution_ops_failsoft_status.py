from skills.library import solution_ops


class _UnavailableSearchKB:
    def get_best_match(self, error_type, error_message, stack_trace="", environment=""):
        return None

    def get_last_search_status(self):
        return {
            "ok": False,
            "unavailable": True,
            "error_code": "vector_store_unavailable",
            "reason": "Qdrant client unavailable",
            "result_count": 0,
        }


class _NoHitSearchKB:
    def get_best_match(self, error_type, error_message, stack_trace="", environment=""):
        return None

    def get_last_search_status(self):
        return {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
            "result_count": 0,
        }


class _UnavailableStoreKB:
    def store_solution(self, record):
        return False

    def get_last_store_status(self):
        return {
            "ok": False,
            "unavailable": True,
            "error_code": "vector_store_unavailable",
            "reason": "Qdrant client unavailable",
        }



def test_search_solution_returns_availability_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", _UnavailableSearchKB())

    skill = solution_ops.SearchSolutionSkill()
    result = skill.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
    )

    assert result["found"] is False
    assert result["availability"]["unavailable"] is True
    assert result["availability"]["error_code"] == "vector_store_unavailable"



def test_search_solution_keeps_no_hit_distinct_from_unavailable(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", _NoHitSearchKB())

    skill = solution_ops.SearchSolutionSkill()
    result = skill.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
    )

    assert result["found"] is False
    assert result["availability"]["ok"] is True
    assert result["availability"]["unavailable"] is False



def test_store_solution_returns_availability_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", _UnavailableStoreKB())

    skill = solution_ops.StoreSolutionSkill()
    result = skill.execute(
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

    assert result["stored"] is False
    assert result["signature"] is None
    assert result["availability"]["unavailable"] is True
    assert result["availability"]["error_code"] == "vector_store_unavailable"
