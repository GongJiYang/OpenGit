import os
import re
import uuid
import time
from pathlib import Path
from typing import List, Dict, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from .ast_parser import CodeChunk
from .embeddings import ZhipuEmbedding

class VectorIndexer:
    """
    Real Vector Database using Qdrant (or compatible API).
    """

    DEFAULT_QDRANT_PATH = os.path.abspath("./agenthub_data/qdrant")
    MAX_SNIPPET_CHARS = int(os.getenv("SEMANTIC_SNIPPET_MAX_CHARS", "2000"))
    MAX_DOCSTRING_CHARS = int(os.getenv("SEMANTIC_DOCSTRING_MAX_CHARS", "600"))
    SECRET_PATTERNS = [
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*[\"'][^\"'\n]{8,}[\"']"),
        re.compile(r"(?i)-----BEGIN (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ]

    @classmethod
    def _resolve_qdrant_target(cls) -> str:
        raw = (os.getenv("QDRANT_URL") or "").strip()
        if raw and raw != ":memory:":
            return raw
        override = (os.getenv("QDRANT_PATH") or "").strip()
        if override:
            return os.path.abspath(override)
        return cls.DEFAULT_QDRANT_PATH

    @staticmethod
    def _should_warn_memory_fallback() -> bool:
        raw = os.getenv("QDRANT_URL")
        return raw is not None and raw.strip() in {"", ":memory:"}

    def __init__(self, collection_name: str = "agenthub_codebase", embedding_dim: int = 1024):
        # Note: Zhipu 'embedding-2' is 1024 dim usually, whereas OpenAI is 1536.
        # We default to 1024 now.
        self.collection_name = collection_name
        self.embedding_dim = embedding_dim

        # Connect to Qdrant
        target = self._resolve_qdrant_target()
        api_key = os.getenv("QDRANT_API_KEY")
        self._client_unavailable_reason: Optional[str] = None

        try:
            if target.startswith(("http://", "https://")):
                self.client = QdrantClient(url=target, api_key=api_key)
            else:
                if self._should_warn_memory_fallback():
                    print(f"⚠️ QDRANT_URL is set to in-memory mode; falling back to persistent path: {target}")
                Path(target).mkdir(parents=True, exist_ok=True)
                self.client = QdrantClient(path=target)
        except Exception as e:
            print(f"❌ Qdrant init failed: {e}")
            self.client = None
            self._client_unavailable_reason = f"Qdrant init failed: {e}"

        self.embedder = ZhipuEmbedding()
        self._warned_embedder = False
        self._warned_client = False
        self._last_search_status = {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
            "result_count": 0,
        }

        # Ensure collection exists and schema matches current embedding config
        if self.client:
            try:
                self._ensure_collection()
            except Exception as e:
                msg = f"Collection validation failed for '{self.collection_name}': {e}"
                print(f"❌ {msg}")
                self.client = None
                self._client_unavailable_reason = msg

    @staticmethod
    def _normalize_distance_value(distance_value) -> Optional[str]:
        if distance_value is None:
            return None
        if isinstance(distance_value, str):
            return distance_value.upper()
        name = getattr(distance_value, "name", None)
        if isinstance(name, str):
            return name.upper()
        value = getattr(distance_value, "value", None)
        if isinstance(value, str):
            return value.upper()
        return str(distance_value).upper()

    def _extract_single_vector_schema(self, vectors_config) -> (Optional[int], Optional[str]):
        size = None
        distance = None

        # Single-vector shape: VectorParams-like object
        if hasattr(vectors_config, "size") and hasattr(vectors_config, "distance"):
            size = getattr(vectors_config, "size", None)
            distance = getattr(vectors_config, "distance", None)
            return size, self._normalize_distance_value(distance)

        # Named-vectors shape: dict-like mapping to VectorParams
        if isinstance(vectors_config, dict) and vectors_config:
            first = next(iter(vectors_config.values()))
            size = getattr(first, "size", None)
            distance = getattr(first, "distance", None)
            return size, self._normalize_distance_value(distance)

        return None, None

    def _ensure_collection(self):
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)

        if not exists:
            print(f"📦 Creating collection '{self.collection_name}'...")
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE)
            )
            return

        info = self.client.get_collection(self.collection_name)
        expected_size = self.embedding_dim
        expected_distance = self._normalize_distance_value(Distance.COSINE)
        actual_size, actual_distance = self._extract_single_vector_schema(getattr(info.config.params, "vectors", None))

        if actual_size is None or actual_distance is None:
            raise RuntimeError("unable to read existing collection vector schema")
        if actual_size != expected_size or actual_distance != expected_distance:
            raise RuntimeError(
                f"vector schema mismatch: expected size={expected_size},distance={expected_distance}; "
                f"got size={actual_size},distance={actual_distance}"
            )

    @classmethod
    def _sanitize_text(cls, text: str, max_chars: int) -> str:
        if not text:
            return ""
        trimmed = text[:max_chars]
        for pattern in cls.SECRET_PATTERNS:
            trimmed = pattern.sub("[REDACTED_SECRET]", trimmed)
        return trimmed

    def clear_file_index(self, repo_name: str, file_path: str) -> bool:
        """
        Delete all indexed chunks for a specific repo file.
        """
        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; semantic index cleanup disabled.")
                self._warned_client = True
            return False

        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="repo_name", match=MatchValue(value=repo_name)),
                        FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                    ]
                ),
                wait=True,
            )
            return True
        except Exception as e:
            print(f"❌ Failed to clear index for {repo_name}:{file_path}: {e}")
            return False

    def index_chunk(self, repo_name: str, file_path: str, chunk: CodeChunk):
        """
        Index a single code chunk.
        """
        if not self.embedder.client:
            if not self._warned_embedder:
                print("⚠️ ZHIPUAI_API_KEY missing; semantic indexing disabled.")
                self._warned_embedder = True
            return
        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; semantic indexing disabled.")
                self._warned_client = True
            return

        try:
            # Generate ID
            point_id = str(uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{repo_name}:{file_path}:{chunk.name}:{chunk.start_line}:{chunk.end_line}"
            ))

            # Embed
            vector = self.embedder.get_embedding(chunk.code)

            payload = {
                "repo_name": repo_name,
                "file_path": file_path,
                "chunk_name": chunk.name,
                "chunk_type": chunk.type,
                "code_snippet": self._sanitize_text(chunk.code, self.MAX_SNIPPET_CHARS),
                "docstring": self._sanitize_text(chunk.docstring, self.MAX_DOCSTRING_CHARS),
                "timestamp": time.time()
            }

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload
                    )
                ]
            )
            print(f"💽 Indexed: {chunk.name} ({chunk.type})")

        except Exception as e:
            print(f"❌ Failed to index {chunk.name}: {e}")

    def _set_search_status(self, ok: bool, unavailable: bool, error_code: Optional[str], reason: Optional[str], result_count: int = 0):
        self._last_search_status = {
            "ok": ok,
            "unavailable": unavailable,
            "error_code": error_code,
            "reason": reason,
            "result_count": result_count,
        }

    def get_last_search_status(self) -> Dict:
        return dict(getattr(self, "_last_search_status", {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
            "result_count": 0,
        }))

    def search(self, query: str, limit: int = 5, repo_name: Optional[str] = None) -> List[Dict]:
        """
        Semantic search.
        """
        if not self.embedder.client:
            if not self._warned_embedder:
                print("⚠️ ZHIPUAI_API_KEY missing; semantic search disabled.")
                self._warned_embedder = True
            self._set_search_status(
                ok=False,
                unavailable=True,
                error_code="embedder_unavailable",
                reason="ZHIPUAI_API_KEY missing",
            )
            return []
        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; semantic search disabled.")
                self._warned_client = True
            self._set_search_status(
                ok=False,
                unavailable=True,
                error_code="vector_store_unavailable",
                reason=self._client_unavailable_reason or "Qdrant client unavailable",
            )
            return []

        try:
            vector = self.embedder.get_embedding(query)

            # Filter by repo_name if provided
            query_filter = None
            if repo_name:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="repo_name",
                            match=MatchValue(value=repo_name)
                        )
                    ]
                )

            # Use query_points which is lower level but robust across versions for Local/Remote
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=limit
            ).points

            start_results = []
            for hit in results:
                start_results.append({
                    "score": hit.score,
                    "payload": hit.payload
                })
            self._set_search_status(
                ok=True,
                unavailable=False,
                error_code=None,
                reason=None,
                result_count=len(start_results),
            )
            return start_results

        except Exception as e:
            print(f"❌ Search failed: {e}")
            self._set_search_status(
                ok=False,
                unavailable=True,
                error_code="search_failed",
                reason=str(e),
            )
            return []

if __name__ == "__main__":
    # Test if run directly (requires env vars)
    try:
        idx = VectorIndexer()
        print(f"Indexer initialized. Collection: {idx.collection_name}")
    except Exception as e:
        print(f"Indexer init failed: {e}")
