from types import SimpleNamespace

from agenthub_semantic_store.solution_kb import SolutionKnowledgeBase, SolutionRecord


class _FakeEmbedder:
    def __init__(self, enabled=True):
        self.client = object() if enabled else None

    def get_embedding(self, text):
        return [0.1, 0.2, 0.3]


class _FakeQueryClient:
    def __init__(self):
        self.created = []

    def get_collections(self):
        class _Collections:
            collections = []

        return _Collections()

    def create_collection(self, **kwargs):
        self.created.append(kwargs)

    def query_points(self, **kwargs):
        return SimpleNamespace(points=[])


class _Hit:
    def __init__(self, score, payload):
        self.score = score
        self.payload = payload


class _RerankQueryClient:
    def __init__(self, hits):
        self._hits = hits

    def query_points(self, **kwargs):
        return SimpleNamespace(points=self._hits)


class _FailingQueryClient:
    def query_points(self, **kwargs):
        raise RuntimeError("qdrant down")


class _UsageClient:
    def __init__(self, records=None, retrieve_error=None, set_payload_error=None):
        self.records = records if records is not None else []
        self.retrieve_error = retrieve_error
        self.set_payload_error = set_payload_error
        self.set_payload_calls = []

    def retrieve(self, **kwargs):
        if self.retrieve_error:
            raise self.retrieve_error
        return self.records

    def set_payload(self, **kwargs):
        if self.set_payload_error:
            raise self.set_payload_error
        self.set_payload_calls.append(kwargs)


class _StatsClient:
    def __init__(self, points_count=0, get_collection_error=None, missing_points_count=False):
        self.points_count = points_count
        self.get_collection_error = get_collection_error
        self.missing_points_count = missing_points_count

    def get_collection(self, _name):
        if self.get_collection_error:
            raise self.get_collection_error
        if self.missing_points_count:
            return SimpleNamespace()
        return SimpleNamespace(points_count=self.points_count)


class _FakeStoreClient:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


class _FailingStoreClient:
    def upsert(self, **kwargs):
        raise RuntimeError("write failed")



def _build_kb(embedder_enabled=True, client=None):
    kb = SolutionKnowledgeBase.__new__(SolutionKnowledgeBase)
    kb.client = client
    kb.embedder = _FakeEmbedder(enabled=embedder_enabled)
    kb._warned_embedder = False
    kb._warned_client = False
    kb._client_unavailable_reason = None
    kb._last_search_status = {
        "ok": True,
        "unavailable": False,
        "error_code": None,
        "reason": None,
        "result_count": 0,
    }
    kb._last_store_status = {
        "ok": True,
        "unavailable": False,
        "error_code": None,
        "reason": None,
    }
    return kb



def _record():
    return SolutionRecord(
        error_signature="",
        error_type="ImportError",
        error_message="No module named x",
        stack_trace="",
        environment="py3.11",
        solution_steps=["pip install x"],
        solution_code="pip install x",
        result="passed",
        confidence=0.9,
        agent_id="agent-1",
    )



def test_search_status_marks_embedder_unavailable():
    kb = _build_kb(embedder_enabled=False, client=_FakeQueryClient())

    results = kb.search_similar("ImportError", "No module")

    assert results == []
    status = kb.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "embedder_unavailable"



def test_search_status_marks_client_unavailable():
    kb = _build_kb(embedder_enabled=True, client=None)

    results = kb.search_similar("ImportError", "No module")

    assert results == []
    status = kb.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "vector_store_unavailable"



def test_search_status_marks_query_failure():
    kb = _build_kb(embedder_enabled=True, client=_FailingQueryClient())

    results = kb.search_similar("ImportError", "No module")

    assert results == []
    status = kb.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "search_failed"
    assert "qdrant down" in status["reason"]



def test_search_status_success_even_when_no_match():
    kb = _build_kb(embedder_enabled=True, client=_FakeQueryClient())

    results = kb.search_similar("ImportError", "No module")

    assert results == []
    status = kb.get_last_search_status()
    assert status["ok"] is True
    assert status["unavailable"] is False
    assert status["error_code"] is None
    assert status["result_count"] == 0



def test_store_status_marks_embedder_unavailable():
    kb = _build_kb(embedder_enabled=False, client=_FakeStoreClient())

    ok = kb.store_solution(_record())

    assert ok is False
    status = kb.get_last_store_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "embedder_unavailable"



def test_store_status_marks_client_unavailable():
    kb = _build_kb(embedder_enabled=True, client=None)

    ok = kb.store_solution(_record())

    assert ok is False
    status = kb.get_last_store_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "vector_store_unavailable"



def test_store_status_marks_write_failure():
    kb = _build_kb(embedder_enabled=True, client=_FailingStoreClient())

    ok = kb.store_solution(_record())

    assert ok is False
    status = kb.get_last_store_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "store_failed"
    assert "write failed" in status["reason"]



def test_store_status_success_on_write():
    kb = _build_kb(embedder_enabled=True, client=_FakeStoreClient())

    ok = kb.store_solution(_record())

    assert ok is True
    status = kb.get_last_store_status()
    assert status["ok"] is True
    assert status["unavailable"] is False
    assert status["error_code"] is None



def test_ensure_collection_creates_when_missing():
    kb = _build_kb(embedder_enabled=True, client=_FakeQueryClient())

    kb._ensure_collection()

    assert len(kb.client.created) == 1
    call = kb.client.created[0]
    assert call["collection_name"] == kb.COLLECTION_NAME



def test_ensure_collection_raises_on_vector_schema_mismatch_distance():
    kb = _build_kb(embedder_enabled=True, client=_FakeQueryClient())

    class _Existing:
        name = kb.COLLECTION_NAME

    class _Params:
        vectors = type("_V", (), {"size": kb.EMBEDDING_DIM, "distance": "Dot"})()

    class _Config:
        params = _Params()

    class _Info:
        config = _Config()

    kb.client.get_collections = lambda: type("_Collections", (), {"collections": [_Existing()]})()
    kb.client.get_collection = lambda _name: _Info()

    try:
        kb._ensure_collection()
        raise AssertionError("expected schema mismatch error")
    except RuntimeError as exc:
        assert "vector schema mismatch" in str(exc)
        assert "distance=COSINE" in str(exc)



def test_search_status_uses_collection_validation_reason_when_unavailable():
    kb = _build_kb(embedder_enabled=True, client=None)
    kb._client_unavailable_reason = "Solution KB collection validation failed: vector schema mismatch"

    results = kb.search_similar("ImportError", "No module")

    assert results == []
    status = kb.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "vector_store_unavailable"
    assert "collection validation failed" in status["reason"].lower()



def test_signature_is_stable_for_numeric_and_address_noise():
    kb = _build_kb(embedder_enabled=True, client=None)

    s1 = kb._compute_signature(
        "TypeError",
        "Invalid id 123 at address 0xABCDEF",
        "Traceback\nline 42 in file.py",
        "python=3.11",
    )
    s2 = kb._compute_signature(
        "TypeError",
        "Invalid id 456 at address 0x123456",
        "Traceback\nline 99 in file.py",
        "python=3.11",
    )

    assert s1 == s2



def test_signature_changes_when_semantics_change():
    kb = _build_kb(embedder_enabled=True, client=None)

    s1 = kb._compute_signature(
        "ImportError",
        "No module named alpha",
        "Traceback\nline 1 in app.py",
        "python=3.11",
    )
    s2 = kb._compute_signature(
        "ImportError",
        "No module named beta",
        "Traceback\nline 1 in app.py",
        "python=3.11",
    )

    assert s1 != s2



def test_store_solution_uses_extended_sha256_signature():
    kb = _build_kb(embedder_enabled=True, client=_FakeStoreClient())
    record = _record()

    ok = kb.store_solution(record)

    assert ok is True
    assert len(record.error_signature) == 20
    assert all(ch in "0123456789abcdef" for ch in record.error_signature)



def test_search_similar_reranks_by_environment_and_recency():
    now = 2000000000.0
    old_hit = _Hit(
        score=0.99,
        payload={
            "error_type": "ImportError",
            "error_message": "No module named x",
            "solution_steps": ["pip install legacy"],
            "solution_code": "pip install legacy",
            "environment": "python=3.9",
            "confidence": 0.8,
            "timestamp": now - 86400 * 800,
            "result": "passed",
        },
    )
    new_hit = _Hit(
        score=0.95,
        payload={
            "error_type": "ImportError",
            "error_message": "No module named x",
            "solution_steps": ["pip install modern"],
            "solution_code": "pip install modern",
            "environment": "python=3.11",
            "confidence": 0.95,
            "timestamp": now - 86400 * 2,
            "result": "passed",
        },
    )
    kb = _build_kb(embedder_enabled=True, client=_RerankQueryClient([old_hit, new_hit]))

    original_time = __import__("agenthub_semantic_store.solution_kb", fromlist=["time"]).time.time
    try:
        __import__("agenthub_semantic_store.solution_kb", fromlist=["time"]).time.time = lambda: now
        results = kb.search_similar(
            "ImportError",
            "No module named x",
            environment="python=3.11",
            limit=2,
        )
    finally:
        __import__("agenthub_semantic_store.solution_kb", fromlist=["time"]).time.time = original_time

    assert len(results) == 2
    assert results[0]["solution"]["solution_steps"] == ["pip install modern"]
    assert results[0]["rank_score"] > results[1]["rank_score"]



def test_get_best_match_forwards_environment():
    kb = _build_kb(embedder_enabled=True, client=None)

    captured = {}

    def _fake_search(error_type, error_message, stack_trace="", limit=3, min_confidence=0.0, environment=""):
        captured["environment"] = environment
        return [{"score": 0.9, "rank_score": 0.9, "solution": {"solution_steps": ["x"]}}]

    kb.search_similar = _fake_search

    best = kb.get_best_match("ImportError", "No module", environment="python=3.11")

    assert best is not None
    assert captured["environment"] == "python=3.11"



def test_increment_usage_returns_false_when_client_unavailable():
    kb = _build_kb(embedder_enabled=True, client=None)

    ok = kb.increment_usage("sol-1")

    assert ok is False



def test_increment_usage_returns_false_when_solution_missing():
    kb = _build_kb(embedder_enabled=True, client=_UsageClient(records=[]))

    ok = kb.increment_usage("sol-1")

    assert ok is False



def test_increment_usage_returns_false_when_retrieve_fails():
    kb = _build_kb(embedder_enabled=True, client=_UsageClient(retrieve_error=RuntimeError("read failed")))

    ok = kb.increment_usage("sol-1")

    assert ok is False



def test_increment_usage_returns_false_when_set_payload_fails():
    record = SimpleNamespace(payload={"usage_count": 2})
    kb = _build_kb(
        embedder_enabled=True,
        client=_UsageClient(records=[record], set_payload_error=RuntimeError("write failed")),
    )

    ok = kb.increment_usage("sol-1")

    assert ok is False



def test_increment_usage_updates_usage_count_on_success():
    record = SimpleNamespace(payload={"usage_count": 2})
    client = _UsageClient(records=[record])
    kb = _build_kb(embedder_enabled=True, client=client)

    ok = kb.increment_usage("sol-1")

    assert ok is True
    assert len(client.set_payload_calls) == 1
    call = client.set_payload_calls[0]
    assert call["collection_name"] == kb.COLLECTION_NAME
    assert call["points"] == ["sol-1"]
    assert call["payload"]["usage_count"] == 3



def test_get_stats_returns_unavailable_status_when_client_missing():
    kb = _build_kb(embedder_enabled=True, client=None)
    kb._client_unavailable_reason = "Qdrant init failed"

    stats = kb.get_stats()

    assert stats["total_solutions"] == 0
    assert stats["collection_name"] == kb.COLLECTION_NAME
    assert stats["ok"] is False
    assert stats["unavailable"] is True
    assert stats["error_code"] == "vector_store_unavailable"
    assert "Qdrant init failed" in stats["reason"]



def test_get_stats_returns_failed_status_with_reason_when_query_errors():
    kb = _build_kb(embedder_enabled=True, client=_StatsClient(get_collection_error=RuntimeError("qdrant down")))

    stats = kb.get_stats()

    assert stats["total_solutions"] == 0
    assert stats["collection_name"] == kb.COLLECTION_NAME
    assert stats["ok"] is False
    assert stats["unavailable"] is True
    assert stats["error_code"] == "stats_failed"
    assert "qdrant down" in stats["reason"]



def test_get_stats_returns_failed_status_when_points_count_missing():
    kb = _build_kb(embedder_enabled=True, client=_StatsClient(missing_points_count=True))

    stats = kb.get_stats()

    assert stats["ok"] is False
    assert stats["unavailable"] is True
    assert stats["error_code"] == "stats_failed"
    assert "points_count" in stats["reason"]



def test_get_stats_returns_ok_status_on_success():
    kb = _build_kb(embedder_enabled=True, client=_StatsClient(points_count=7))

    stats = kb.get_stats()

    assert stats["total_solutions"] == 7
    assert stats["collection_name"] == kb.COLLECTION_NAME
    assert stats["ok"] is True
    assert stats["unavailable"] is False
    assert stats["error_code"] is None
    assert stats["reason"] is None
