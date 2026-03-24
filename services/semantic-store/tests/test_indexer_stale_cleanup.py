from agenthub_semantic_store.ast_parser import CodeChunk
from agenthub_semantic_store.indexer import VectorIndexer


class _FakeEmbedder:
    def __init__(self):
        self.client = object()

    def get_embedding(self, text):
        return [0.1, 0.2, 0.3]


class _FakeClient:
    def __init__(self):
        self.deleted = []
        self.upserts = []
        self.query_points_called = []
        self.created = []

    def delete(self, **kwargs):
        self.deleted.append(kwargs)

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def query_points(self, **kwargs):
        self.query_points_called.append(kwargs)

        class _Result:
            points = []

        return _Result()

    def get_collections(self):
        class _Collections:
            collections = []

        return _Collections()

    def create_collection(self, **kwargs):
        self.created.append(kwargs)



def _build_indexer_with_fakes():
    indexer = VectorIndexer.__new__(VectorIndexer)
    indexer.collection_name = "agenthub_codebase"
    indexer.embedding_dim = 1024
    indexer.client = _FakeClient()
    indexer.embedder = _FakeEmbedder()
    indexer._warned_embedder = False
    indexer._warned_client = False
    indexer._client_unavailable_reason = None
    indexer._last_search_status = {
        "ok": True,
        "unavailable": False,
        "error_code": None,
        "reason": None,
        "result_count": 0,
    }
    return indexer



def test_clear_file_index_deletes_by_repo_and_file_filter():
    indexer = _build_indexer_with_fakes()

    ok = indexer.clear_file_index("repo-1", "pkg/mod.py")

    assert ok is True
    assert len(indexer.client.deleted) == 1
    call = indexer.client.deleted[0]
    assert call["collection_name"] == "agenthub_codebase"
    assert call["wait"] is True

    selector = call["points_selector"]
    must = selector.must
    values = {cond.key: cond.match.value for cond in must}
    assert values == {"repo_name": "repo-1", "file_path": "pkg/mod.py"}



def test_clear_file_index_returns_false_when_client_unavailable():
    indexer = _build_indexer_with_fakes()
    indexer.client = None

    ok = indexer.clear_file_index("repo-1", "pkg/mod.py")

    assert ok is False



def test_index_chunk_still_upserts_after_cleanup_method_added():
    indexer = _build_indexer_with_fakes()

    chunk = CodeChunk(
        name="fn",
        type="function",
        code="def fn():\n    return 1\n",
        start_line=1,
        end_line=2,
        docstring="",
    )

    indexer.index_chunk("repo-1", "pkg/mod.py", chunk)

    assert len(indexer.client.upserts) == 1
    call = indexer.client.upserts[0]
    assert call["collection_name"] == "agenthub_codebase"
    point = call["points"][0]
    assert point.payload["repo_name"] == "repo-1"
    assert point.payload["file_path"] == "pkg/mod.py"
    assert point.payload["chunk_name"] == "fn"



def test_index_chunk_sanitizes_and_truncates_sensitive_payload():
    indexer = _build_indexer_with_fakes()

    secret_line = 'api_key = "sk-this-is-a-very-secret-token-value"\n'
    doc_secret = "-----BEGIN RSA PRIVATE KEY-----\nABCDEF\n"
    chunk = CodeChunk(
        name="danger",
        type="function",
        code=secret_line + ("x" * 2600),
        start_line=1,
        end_line=2,
        docstring=doc_secret + ("y" * 1200),
    )

    indexer.index_chunk("repo-1", "pkg/secret.py", chunk)

    point = indexer.client.upserts[0]["points"][0]
    payload = point.payload

    assert len(payload["code_snippet"]) <= indexer.MAX_SNIPPET_CHARS + len("[REDACTED_SECRET]")
    assert len(payload["docstring"]) <= indexer.MAX_DOCSTRING_CHARS + len("[REDACTED_SECRET]")
    assert "[REDACTED_SECRET]" in payload["code_snippet"] or "[REDACTED_SECRET]" in payload["docstring"]
    assert "BEGIN RSA PRIVATE KEY" not in payload["docstring"]


def test_search_sets_unavailable_status_when_embedder_missing():
    indexer = _build_indexer_with_fakes()
    indexer.embedder.client = None

    results = indexer.search("hello", limit=2)

    assert results == []
    status = indexer.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "embedder_unavailable"


def test_search_sets_unavailable_status_when_client_missing():
    indexer = _build_indexer_with_fakes()
    indexer.client = None

    results = indexer.search("hello", limit=2)

    assert results == []
    status = indexer.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "vector_store_unavailable"


def test_search_sets_success_status_on_normal_empty_result():
    indexer = _build_indexer_with_fakes()

    results = indexer.search("hello", limit=2, repo_name="repo-1")

    assert results == []
    status = indexer.get_last_search_status()
    assert status["ok"] is True
    assert status["unavailable"] is False
    assert status["error_code"] is None
    assert status["result_count"] == 0
    call = indexer.client.query_points_called[0]
    must = call["query_filter"].must
    values = {cond.key: cond.match.value for cond in must}
    assert values == {"repo_name": "repo-1"}



def test_ensure_collection_creates_when_missing():
    indexer = _build_indexer_with_fakes()

    indexer._ensure_collection()

    assert len(indexer.client.created) == 1
    call = indexer.client.created[0]
    assert call["collection_name"] == "agenthub_codebase"



def test_ensure_collection_raises_on_vector_schema_mismatch_size():
    indexer = _build_indexer_with_fakes()

    class _Existing:
        name = "agenthub_codebase"

    class _Params:
        vectors = type("_V", (), {"size": 1536, "distance": "Cosine"})()

    class _Config:
        params = _Params()

    class _Info:
        config = _Config()

    indexer.client.get_collections = lambda: type("_Collections", (), {"collections": [_Existing()]})()
    indexer.client.get_collection = lambda _name: _Info()

    try:
        indexer._ensure_collection()
        raise AssertionError("expected schema mismatch error")
    except RuntimeError as exc:
        assert "vector schema mismatch" in str(exc)
        assert "expected size=1024" in str(exc)



def test_search_surfaces_collection_validation_reason_when_client_disabled():
    indexer = _build_indexer_with_fakes()
    indexer.client = None
    indexer._client_unavailable_reason = "Collection validation failed: vector schema mismatch"

    results = indexer.search("hello", limit=2)

    assert results == []
    status = indexer.get_last_search_status()
    assert status["ok"] is False
    assert status["unavailable"] is True
    assert status["error_code"] == "vector_store_unavailable"
    assert "Collection validation failed" in status["reason"]
