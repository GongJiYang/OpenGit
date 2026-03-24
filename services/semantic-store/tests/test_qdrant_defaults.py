import os

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("zhipuai")

from agenthub_semantic_store.indexer import VectorIndexer  # noqa: E402
from agenthub_semantic_store.solution_kb import SolutionKnowledgeBase  # noqa: E402


def test_vector_indexer_default_qdrant_target_is_persistent_path(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_PATH", raising=False)

    assert VectorIndexer._resolve_qdrant_target() == os.path.abspath("./agenthub_data/qdrant")


def test_solution_kb_default_qdrant_target_is_persistent_path(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_PATH", raising=False)

    assert SolutionKnowledgeBase._resolve_qdrant_target() == os.path.abspath("./agenthub_data/qdrant")


def test_qdrant_url_memory_falls_back_to_persistent_path(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.delenv("QDRANT_PATH", raising=False)

    assert VectorIndexer._resolve_qdrant_target() == os.path.abspath("./agenthub_data/qdrant")
    assert SolutionKnowledgeBase._resolve_qdrant_target() == os.path.abspath("./agenthub_data/qdrant")


def test_qdrant_path_override_takes_precedence_over_default(monkeypatch, tmp_path):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    custom_path = tmp_path / "custom-qdrant"
    monkeypatch.setenv("QDRANT_PATH", str(custom_path))

    assert VectorIndexer._resolve_qdrant_target() == os.path.abspath(str(custom_path))
    assert SolutionKnowledgeBase._resolve_qdrant_target() == os.path.abspath(str(custom_path))


def test_remote_qdrant_url_is_preserved(monkeypatch):
    remote_url = "http://qdrant:6333"
    monkeypatch.setenv("QDRANT_URL", remote_url)
    monkeypatch.delenv("QDRANT_PATH", raising=False)

    assert VectorIndexer._resolve_qdrant_target() == remote_url
    assert SolutionKnowledgeBase._resolve_qdrant_target() == remote_url


def test_memory_fallback_warning_detection(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    assert VectorIndexer._should_warn_memory_fallback() is True
    assert SolutionKnowledgeBase._should_warn_memory_fallback() is True

    monkeypatch.setenv("QDRANT_URL", "")
    assert VectorIndexer._should_warn_memory_fallback() is True
    assert SolutionKnowledgeBase._should_warn_memory_fallback() is True

    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert VectorIndexer._should_warn_memory_fallback() is False
    assert SolutionKnowledgeBase._should_warn_memory_fallback() is False
