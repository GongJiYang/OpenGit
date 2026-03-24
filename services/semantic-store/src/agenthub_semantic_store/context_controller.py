import re
from typing import Dict, List, Tuple

from .indexer import VectorIndexer


class ContextController:
    """
    Manages the Context Window for Agents.
    'Smart Pruning' logic resides here.
    """

    SEARCH_LIMIT = 30
    TOKEN_PER_CHAR = 0.28
    STRUCTURE_TOKEN_OVERHEAD = 22
    CHUNK_TYPE_PRIORITY = {
        "function": 3,
        "class": 3,
        "method": 3,
        "interface": 2,
        "module": 2,
        "doc": 1,
    }

    def __init__(self, indexer: VectorIndexer):
        self.indexer = indexer

    @classmethod
    def _normalize_snippet(cls, text: str) -> str:
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        return normalized

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        if not text:
            return cls.STRUCTURE_TOKEN_OVERHEAD
        return int(len(text) * cls.TOKEN_PER_CHAR) + cls.STRUCTURE_TOKEN_OVERHEAD

    def _dedupe_and_rerank_hits(self, hits: List[Dict]) -> List[Dict]:
        deduped: Dict[Tuple[str, str], Dict] = {}

        for hit in hits:
            payload = hit.get("payload") or {}
            file_path = payload.get("file_path") or ""
            chunk_name = payload.get("chunk_name") or ""
            code_snippet = payload.get("code_snippet") or ""

            key = (file_path, chunk_name)
            normalized_new = self._normalize_snippet(code_snippet)
            score_new = float(hit.get("score", 0.0) or 0.0)
            existing = deduped.get(key)

            if existing is None:
                deduped[key] = hit
                continue

            existing_payload = existing.get("payload") or {}
            normalized_existing = self._normalize_snippet(existing_payload.get("code_snippet") or "")
            score_existing = float(existing.get("score", 0.0) or 0.0)

            if normalized_new == normalized_existing:
                if score_new > score_existing:
                    deduped[key] = hit
                continue

            if score_new > score_existing:
                deduped[key] = hit

        ranked = list(deduped.values())
        ranked.sort(key=self._rank_tuple, reverse=True)
        return ranked

    def _rank_tuple(self, hit: Dict) -> Tuple[float, float, int]:
        payload = hit.get("payload") or {}
        score = float(hit.get("score", 0.0) or 0.0)
        chunk_type = (payload.get("chunk_type") or "").lower()
        type_priority = self.CHUNK_TYPE_PRIORITY.get(chunk_type, 0)
        type_bonus = type_priority * 0.05
        snippet_len = len(payload.get("code_snippet") or "")
        return (score + type_bonus, score, snippet_len)

    def prune_context(self, query: str, repo_name: str, max_tokens: int = 4000) -> str:
        """
        Retrieves the most relevant code chunks for the query,
        fitting within max_tokens.
        """
        hits = self.indexer.search(query, limit=self.SEARCH_LIMIT, repo_name=repo_name)
        ranked_hits = self._dedupe_and_rerank_hits(hits)

        selected_code: List[str] = []
        current_tokens = 0

        print(f"✂️ Pruning context for query: '{query}' (Max tokens: {max_tokens})")

        for hit in ranked_hits:
            payload = hit.get("payload") or {}
            file_path = payload.get("file_path") or ""
            chunk_name = payload.get("chunk_name") or ""
            code_snippet = payload.get("code_snippet") or ""
            snippet = f"\n# File: {file_path} | {chunk_name}\n{code_snippet}\n"

            snippet_tokens = self._estimate_tokens(snippet)
            if current_tokens + snippet_tokens > max_tokens:
                continue

            selected_code.append(snippet)
            current_tokens += snippet_tokens

        final_context = "\n".join(selected_code)
        print(f"✅ Constructed Context: ~{current_tokens} tokens from {len(selected_code)} chunks.")

        if not final_context:
            return "# No relevant context found."

        return final_context
