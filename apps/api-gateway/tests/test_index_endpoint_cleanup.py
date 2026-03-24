import subprocess
from unittest.mock import patch


def test_search_endpoint_requires_authenticated_identity(client):
    res = client.get("/api/v1/search", params={"query": "foo"})
    assert res.status_code == 401


def test_search_endpoint_strict_mode_returns_503_when_indexer_disabled(client, auth_headers):
    client.app.state.indexer = None

    res = client.get(
        "/api/v1/search",
        params={"query": "foo", "repo_name": "repo-index.git", "strict": True},
        headers=auth_headers,
    )

    assert res.status_code == 503
    assert res.json()["detail"] == "Semantic search is disabled"


def test_search_endpoint_strict_mode_returns_unavailable_reason_when_backend_fails(client, auth_headers):
    class _FailingIndexer:
        def search(self, query, limit=5, repo_name=None):
            assert repo_name == "repo-index.git"
            return []

        def get_last_search_status(self):
            return {
                "ok": False,
                "unavailable": True,
                "error_code": "vector_store_unavailable",
                "reason": "Qdrant client unavailable",
                "result_count": 0,
            }

    client.app.state.indexer = _FailingIndexer()

    res = client.get(
        "/api/v1/search",
        params={"query": "foo", "repo_name": "repo-index.git", "strict": True},
        headers=auth_headers,
    )

    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["message"] == "Semantic search backend unavailable"
    assert detail["error_code"] == "vector_store_unavailable"
    assert detail["reason"] == "Qdrant client unavailable"



def test_index_endpoint_reads_repo_head_and_clears_stale_vectors(client, auth_headers):
    calls = []

    class _FakeParser:
        def parse(self, content):
            assert content == "from-head\n"
            return ["chunk-1", "chunk-2"]

    class _FakeIndexer:
        def clear_file_index(self, repo_name, file_path):
            calls.append(("clear", repo_name, file_path))
            return True

        def index_chunk(self, repo_name, file_path, chunk):
            calls.append(("index", repo_name, file_path, chunk))

    def _fake_check_output(cmd, cwd=None, stderr=None):
        assert cmd == ["git", "show", "HEAD:pkg/mod.py"]
        return b"from-head\n"

    client.app.state.parser = _FakeParser()
    client.app.state.indexer = _FakeIndexer()

    with patch("app_factory.os.path.exists", return_value=True), patch(
        "app_factory.subprocess.check_output", side_effect=_fake_check_output
    ):
        res = client.post(
            "/api/v1/index",
            params={"repo_name": "repo-index.git", "file_path": "pkg/mod.py"},
            headers=auth_headers,
        )

    assert res.status_code == 200, res.text
    assert res.json() == {"indexed_chunks": 2}
    assert calls == [
        ("clear", "repo-index.git", "pkg/mod.py"),
        ("index", "repo-index.git", "pkg/mod.py", "chunk-1"),
        ("index", "repo-index.git", "pkg/mod.py", "chunk-2"),
    ]


def test_index_endpoint_rejects_missing_head_file(client, auth_headers):
    class _FakeParser:
        def parse(self, content):
            return []

    class _FakeIndexer:
        def clear_file_index(self, repo_name, file_path):
            return True

        def index_chunk(self, repo_name, file_path, chunk):
            raise AssertionError("should not index when file is missing")

    def _fake_check_output(cmd, cwd=None, stderr=None):
        raise subprocess.CalledProcessError(returncode=128, cmd=cmd, stderr=b"fatal")

    client.app.state.parser = _FakeParser()
    client.app.state.indexer = _FakeIndexer()

    with patch("app_factory.os.path.exists", return_value=True), patch(
        "app_factory.subprocess.check_output", side_effect=_fake_check_output
    ):
        res = client.post(
            "/api/v1/index",
            params={"repo_name": "repo-index.git", "file_path": "pkg/missing.py"},
            headers=auth_headers,
        )

    assert res.status_code == 404
    assert res.json()["detail"] == "File not found in repository HEAD"
