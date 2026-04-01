from skills.library import solution_ops


class _FakeKB:
    init_count = 0

    def __init__(self):
        type(self).init_count += 1
        self._items = []
        self.incremented = []

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
                    "solution_id": record.error_signature,
                    "solution": {
                        "error_type": record.error_type,
                        "error_message": record.error_message,
                        "solution_steps": record.solution_steps,
                        "solution_code": record.solution_code,
                        "environment": record.environment,
                        "confidence": record.confidence,
                        "usage_count": record.usage_count,
                        "success_count": getattr(record, "success_count", 0),
                        "failure_count": getattr(record, "failure_count", 0),
                        "success_rate": getattr(record, "success_rate", None),
                        "agent_id": record.agent_id,
                    },
                }
        return None

    def increment_usage(self, solution_id):
        for record in self._items:
            if record.error_signature == solution_id:
                record.usage_count += 1
                self.incremented.append(solution_id)
                return True
        return False

    def record_feedback(self, solution_id, result):
        for record in self._items:
            if record.error_signature == solution_id:
                if result == "passed":
                    record.success_count = getattr(record, "success_count", 0) + 1
                else:
                    record.failure_count = getattr(record, "failure_count", 0) + 1
                total = getattr(record, "success_count", 0) + getattr(record, "failure_count", 0)
                record.success_rate = (record.success_count / total) if total else None
                return True
        return False

    def get_last_feedback_status(self):
        return {"ok": True, "unavailable": False, "error_code": None, "reason": None}

    def get_stats(self):
        return {"total_solutions": len(self._items)}


def test_solution_skills_share_single_kb_instance(monkeypatch):
    monkeypatch.setattr(solution_ops, "_shared_solution_kb", None)
    _FakeKB.init_count = 0
    monkeypatch.setattr(solution_ops, "SolutionKnowledgeBase", _FakeKB)

    search = solution_ops.SearchSolutionSkill()
    store = solution_ops.StoreSolutionSkill()
    feedback = solution_ops.FeedbackSolutionSkill()
    batch = solution_ops.BatchSearchSolutionSkill()
    stats = solution_ops.GetSolutionStatsSkill()

    assert _FakeKB.init_count == 1
    assert search.kb is store.kb is feedback.kb is batch.kb is stats.kb
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
    assert looked_up["solution"]["solution_id"] == "ImportError:0"
    assert looked_up["solution"]["solution_steps"] == ["pip install x"]
    assert looked_up["solution"]["agent_id"] == "agent-1"
    assert looked_up["solution"]["usage_count"] == 1
    assert search.kb.incremented == ["ImportError:0"]

    feedback = solution_ops.FeedbackSolutionSkill()
    feedback_result = feedback.execute(solution_id="ImportError:0", result="passed")

    assert feedback_result["updated"] is True
    assert search.kb._items[0].success_count == 1
    assert search.kb._items[0].failure_count == 0
    assert search.kb._items[0].success_rate == 1.0
