from agenthub_semantic_store.context_controller import ContextController


class _FakeIndexer:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, limit=20, repo_name=None):
        self.calls.append((query, limit, repo_name))
        return list(self.hits)



def test_prune_context_dedups_duplicate_chunks_and_prefers_higher_score():
    hits = [
        {
            "score": 0.7,
            "payload": {
                "file_path": "pkg/a.py",
                "chunk_name": "fn",
                "chunk_type": "function",
                "code_snippet": "def fn():\n    return 1\n",
            },
        },
        {
            "score": 0.95,
            "payload": {
                "file_path": "pkg/a.py",
                "chunk_name": "fn",
                "chunk_type": "function",
                "code_snippet": "def fn():\n    return 2\n",
            },
        },
    ]
    controller = ContextController(_FakeIndexer(hits))

    out = controller.prune_context("fn", "repo-1", max_tokens=200)

    assert out.count("# File: pkg/a.py | fn") == 1
    assert "return 2" in out



def test_prune_context_reranks_by_score_and_type_priority():
    hits = [
        {
            "score": 0.91,
            "payload": {
                "file_path": "pkg/readme.md",
                "chunk_name": "notes",
                "chunk_type": "doc",
                "code_snippet": "some docs",
            },
        },
        {
            "score": 0.89,
            "payload": {
                "file_path": "pkg/core.py",
                "chunk_name": "run",
                "chunk_type": "function",
                "code_snippet": "def run():\n    pass\n",
            },
        },
    ]
    controller = ContextController(_FakeIndexer(hits))

    out = controller.prune_context("run", "repo-1", max_tokens=200)

    first_marker = out.splitlines()[1]
    assert "pkg/core.py" in first_marker



def test_prune_context_token_budget_respects_estimate_and_stops():
    big = "x" * 900
    small = "y" * 40
    hits = [
        {
            "score": 0.9,
            "payload": {
                "file_path": "pkg/big.py",
                "chunk_name": "big",
                "chunk_type": "function",
                "code_snippet": big,
            },
        },
        {
            "score": 0.85,
            "payload": {
                "file_path": "pkg/small.py",
                "chunk_name": "small",
                "chunk_type": "function",
                "code_snippet": small,
            },
        },
    ]
    controller = ContextController(_FakeIndexer(hits))

    out = controller.prune_context("x", "repo-1", max_tokens=80)

    assert "pkg/big.py" not in out
    assert "pkg/small.py" in out



def test_prune_context_returns_default_when_no_hits():
    controller = ContextController(_FakeIndexer([]))

    out = controller.prune_context("x", "repo-1")

    assert out == "# No relevant context found."
