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
    def __init__(self):
        self.increment_calls = []

    def get_best_match(self, error_type, error_message, stack_trace="", environment=""):
        return None

    def increment_usage(self, solution_id):
        self.increment_calls.append(solution_id)
        return True

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


class _UnavailableFeedbackKB:
    def record_feedback(self, solution_id, result):
        return False

    def get_last_feedback_status(self):
        return {
            "ok": False,
            "unavailable": True,
            "error_code": "vector_store_unavailable",
            "reason": "Qdrant client unavailable",
        }


class _HitButIncrementFailsKB:
    def get_best_match(self, error_type, error_message, stack_trace="", environment=""):
        return {
            "score": 0.95,
            "solution_id": "sol-1",
            "solution": {
                "error_type": error_type,
                "error_message": error_message,
                "solution_steps": ["apply fix"],
                "solution_code": "",
                "environment": environment,
                "confidence": 0.9,
                "usage_count": 2,
                "success_count": 3,
                "failure_count": 1,
                "success_rate": 0.75,
                "agent_id": "agent-1",
            },
        }

    def increment_usage(self, solution_id):
        raise RuntimeError("qdrant transient error")

    def get_last_search_status(self):
        return {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
            "result_count": 1,
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
    kb = _NoHitSearchKB()
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", kb)

    skill = solution_ops.SearchSolutionSkill()
    result = skill.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
    )

    assert result["found"] is False
    assert result["availability"]["ok"] is True
    assert result["availability"]["unavailable"] is False
    assert kb.increment_calls == []



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



def test_search_solution_tolerates_increment_usage_failure(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", _HitButIncrementFailsKB())

    skill = solution_ops.SearchSolutionSkill()
    result = skill.execute(
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
        environment="py3.11",
    )

    assert result["found"] is True
    assert result["similarity"] == 0.95
    assert result["solution"]["solution_id"] == "sol-1"
    assert result["solution"]["usage_count"] == 2
    assert result["solution"]["success_count"] == 3
    assert result["solution"]["failure_count"] == 1
    assert result["solution"]["success_rate"] == 0.75
    assert result["availability"]["ok"] is True



def test_feedback_solution_returns_availability_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", _UnavailableFeedbackKB())

    skill = solution_ops.FeedbackSolutionSkill()
    result = skill.execute(solution_id="sol-1", result="passed")

    assert result["updated"] is False
    assert result["availability"]["unavailable"] is True
    assert result["availability"]["error_code"] == "vector_store_unavailable"
