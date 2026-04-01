"""
解决方案知识库 - 技能池复用核心
实现错误解决方案的存储、检索与复用
"""
import os
import uuid
import time
import json
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue


from .embeddings import ZhipuEmbedding


@dataclass
class SolutionRecord:
    """解决方案记录结构"""
    error_signature: str           # 错误签名（稳定语义+上下文 hash）
    error_type: str                # 错误类型：ImportError, AttributeError, etc.
    error_message: str             # 完整错误消息
    stack_trace: str               # 栈追踪摘要
    environment: str               # 环境：Python版本、依赖版本
    solution_steps: List[str]      # 最短修复步骤
    solution_code: str             # 修复代码片段（如有）
    result: str                    # "passed" / "failed"
    confidence: float              # 方案置信度 (0.0 - 1.0)
    agent_id: str                  # 贡献者 Agent ID
    timestamp: float = field(default_factory=time.time)
    usage_count: int = 0           # 复用次数
    success_count: int = 0         # 命中后执行成功次数
    failure_count: int = 0         # 命中后执行失败次数
    success_rate: Optional[float] = None
    last_feedback_at: Optional[float] = None
    last_feedback_result: str = ""


class SolutionKnowledgeBase:
    """
    解决方案知识库 - 技能池复用核心

    结构:
        - 问题摘要（错误类型 / 环境 / 栈）
        - 解决方案（最短修复步骤）
        - 结果验证（通过/失败）
        - 写入向量库（Qdrant）

    使用策略:
        新异常 → 先在 KB 检索
        如果命中相似度高 → 直接给 LLM 方案摘要 → LLM 只负责确认 + 执行
        如果无命中 → LLM 正常推理 → 推理结果写回 KB
    """
    COLLECTION_NAME = "agenthub_solutions"
    SIMILARITY_THRESHOLD = 0.85  # 相似度阈值，高于此值才复用
    EMBEDDING_DIM = 1024
    DEFAULT_QDRANT_PATH = os.path.abspath("./agenthub_data/qdrant")

    # Rerank weights to avoid stale/high-similarity-only bias
    RERANK_SEMANTIC_WEIGHT = 0.65
    RERANK_CONFIDENCE_WEIGHT = 0.20
    RERANK_ENV_WEIGHT = 0.10
    RERANK_RECENCY_WEIGHT = 0.05

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

    def __init__(self):
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
            print(f"❌ Solution KB Qdrant init failed: {e}")
            self.client = None
            self._client_unavailable_reason = f"Solution KB Qdrant init failed: {e}"

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
        self._last_store_status = {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
        }
        self._last_feedback_status = {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
        }

        if self.client:
            try:
                self._ensure_collection()
            except Exception as e:
                msg = f"Solution KB collection validation failed for '{self.COLLECTION_NAME}': {e}"
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

        if hasattr(vectors_config, "size") and hasattr(vectors_config, "distance"):
            size = getattr(vectors_config, "size", None)
            distance = getattr(vectors_config, "distance", None)
            return size, self._normalize_distance_value(distance)

        if isinstance(vectors_config, dict) and vectors_config:
            first = next(iter(vectors_config.values()))
            size = getattr(first, "size", None)
            distance = getattr(first, "distance", None)
            return size, self._normalize_distance_value(distance)

        return None, None

    def _ensure_collection(self):
        """确保 collection 存在且向量 schema 与当前配置一致"""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.COLLECTION_NAME for c in collections)

        if not exists:
            print(f"📦 Creating solution collection '{self.COLLECTION_NAME}'...")
            self.client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=VectorParams(size=self.EMBEDDING_DIM, distance=Distance.COSINE)
            )
            return

        info = self.client.get_collection(self.COLLECTION_NAME)
        expected_size = self.EMBEDDING_DIM
        expected_distance = self._normalize_distance_value(Distance.COSINE)
        actual_size, actual_distance = self._extract_single_vector_schema(getattr(info.config.params, "vectors", None))

        if actual_size is None or actual_distance is None:
            raise RuntimeError("unable to read existing collection vector schema")
        if actual_size != expected_size or actual_distance != expected_distance:
            raise RuntimeError(
                f"vector schema mismatch: expected size={expected_size},distance={expected_distance}; "
                f"got size={actual_size},distance={actual_distance}"
            )

    @staticmethod
    def _normalize_text_for_signature(text: str, max_chars: int = 300) -> str:
        if not text:
            return ""
        normalized = text.lower().strip()
        normalized = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", normalized)
        normalized = re.sub(r"\b\d+\b", "<num>", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized[:max_chars]

    def _compute_signature(self, error_type: str, error_message: str, stack_trace: str = "", environment: str = "") -> str:
        """
        计算错误签名：基于稳定语义上下文生成低碰撞签名。
        """
        first_line = error_message.split('\n')[0] if error_message else ""
        stack_head = "\n".join(stack_trace.split('\n')[:6]) if stack_trace else ""

        signature_basis = {
            "v": 2,
            "error_type": self._normalize_text_for_signature(error_type, max_chars=80),
            "error_message": self._normalize_text_for_signature(first_line, max_chars=240),
            "stack": self._normalize_text_for_signature(stack_head, max_chars=400),
            "environment": self._normalize_text_for_signature(environment, max_chars=120),
        }
        canonical = json.dumps(signature_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]

    def _build_search_text(self, error_type: str, error_message: str, stack_trace: str) -> str:
        """
        构建用于向量化的搜索文本
        提取栈追踪前几帧的关键信息
        """
        # 取栈追踪前3帧（去除行号）
        stack_lines = []
        for line in stack_trace.split('\n')[:6]:
            # 去除行号，保留文件和函数信息
            if line.strip():
                cleaned = re.sub(r':\d+', '', line)  # 去除行号
                stack_lines.append(cleaned.strip())

        stack_summary = ' '.join(stack_lines[:3])
        return f"{error_type}: {error_message[:200]}\n{stack_summary}"

    @staticmethod
    def _extract_python_minor(environment: str) -> Optional[str]:
        if not environment:
            return None
        text = environment.lower()
        patterns = [
            r"python\s*[=:]?\s*(\d+\.\d+)",
            r"py(?:thon)?[=:]?(\d+\.\d+)",
            r"\b(\d+\.\d+)\b",
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        return None

    def _environment_compatibility_score(self, query_environment: str, candidate_environment: str) -> float:
        query_env = (query_environment or "").strip().lower()
        candidate_env = (candidate_environment or "").strip().lower()
        if not query_env or not candidate_env:
            return 0.5
        if query_env == candidate_env:
            return 1.0

        q_py = self._extract_python_minor(query_env)
        c_py = self._extract_python_minor(candidate_env)
        if q_py and c_py and q_py == c_py:
            return 0.8
        if q_py and c_py and q_py.split(".")[0] == c_py.split(".")[0]:
            return 0.65
        return 0.2

    @staticmethod
    def _recency_score(timestamp: Optional[float], now_ts: Optional[float] = None) -> float:
        if not timestamp:
            return 0.5
        if now_ts is None:
            now_ts = time.time()
        age_days = max(0.0, (now_ts - float(timestamp)) / 86400.0)
        return max(0.0, min(1.0, 1.0 / (1.0 + age_days / 30.0)))

    def _composite_score(self, semantic_score: float, payload: Dict, query_environment: str, now_ts: Optional[float] = None) -> float:
        confidence = float(payload.get("confidence", 1.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))
        env_score = self._environment_compatibility_score(query_environment, payload.get("environment", ""))
        recent_score = self._recency_score(payload.get("timestamp"), now_ts=now_ts)

        return (
            self.RERANK_SEMANTIC_WEIGHT * semantic_score
            + self.RERANK_CONFIDENCE_WEIGHT * confidence
            + self.RERANK_ENV_WEIGHT * env_score
            + self.RERANK_RECENCY_WEIGHT * recent_score
        )

    def _set_search_status(self, ok: bool, unavailable: bool, error_code: Optional[str], reason: Optional[str], result_count: int = 0):
        self._last_search_status = {
            "ok": ok,
            "unavailable": unavailable,
            "error_code": error_code,
            "reason": reason,
            "result_count": result_count,
        }

    def _set_store_status(self, ok: bool, unavailable: bool, error_code: Optional[str], reason: Optional[str]):
        self._last_store_status = {
            "ok": ok,
            "unavailable": unavailable,
            "error_code": error_code,
            "reason": reason,
        }

    def _set_feedback_status(self, ok: bool, unavailable: bool, error_code: Optional[str], reason: Optional[str]):
        self._last_feedback_status = {
            "ok": ok,
            "unavailable": unavailable,
            "error_code": error_code,
            "reason": reason,
        }

    def get_last_search_status(self) -> Dict:
        return dict(getattr(self, "_last_search_status", {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
            "result_count": 0,
        }))

    def get_last_store_status(self) -> Dict:
        return dict(getattr(self, "_last_store_status", {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
        }))

    def get_last_feedback_status(self) -> Dict:
        return dict(getattr(self, "_last_feedback_status", {
            "ok": True,
            "unavailable": False,
            "error_code": None,
            "reason": None,
        }))

    def search_similar(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = "",
        limit: int = 3,
        min_confidence: float = 0.0,
        environment: str = "",
    ) -> List[Dict]:
        """
        检索相似错误的解决方案

        Args:
            error_type: 错误类型
            error_message: 完整错误消息
            stack_trace: 栈追踪信息
            limit: 返回结果数量限制
            min_confidence: 最低置信度过滤
            environment: 查询侧环境信息（用于兼容性重排）

        Returns:
            [{"score": 0.92, "solution": SolutionRecord-dict}, ...]
        """
        if not self.embedder.client:
            if not self._warned_embedder:
                print("⚠️ ZHIPUAI_API_KEY missing; solution search disabled.")
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
                print("⚠️ Qdrant client unavailable; solution search disabled.")
                self._warned_client = True
            self._set_search_status(
                ok=False,
                unavailable=True,
                error_code="vector_store_unavailable",
                reason=self._client_unavailable_reason or "Qdrant client unavailable",
            )
            return []

        try:
            search_text = self._build_search_text(error_type, error_message, stack_trace)
            vector = self.embedder.get_embedding(search_text)

            # 只检索成功的方案
            query_filter = Filter(
                must=[
                    FieldCondition(key="result", match=MatchValue(value="passed"))
                ]
            )

            # Pull extra candidates for reranking so stale/old solutions don't dominate.
            fetch_limit = max(limit * 3, 10)
            results = self.client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=vector,
                query_filter=query_filter,
                limit=fetch_limit
            ).points

            now_ts = time.time()
            candidates = []
            for hit in results:
                if hit.score < self.SIMILARITY_THRESHOLD:
                    continue
                payload = getattr(hit, "payload", None) or {}
                if payload.get("confidence", 1.0) < min_confidence:
                    continue

                rerank_score = self._composite_score(
                    semantic_score=hit.score,
                    payload=payload,
                    query_environment=environment,
                    now_ts=now_ts,
                )
                solution_id = getattr(hit, "id", None)
                if solution_id is None:
                    solution_id = payload.get("error_signature")
                candidates.append({
                    "score": hit.score,
                    "rank_score": rerank_score,
                    "solution_id": str(solution_id) if solution_id is not None else None,
                    "solution": payload,
                })

            candidates.sort(key=lambda item: (item["rank_score"], item["score"]), reverse=True)
            solutions = candidates[:limit]

            self._set_search_status(
                ok=True,
                unavailable=False,
                error_code=None,
                reason=None,
                result_count=len(solutions),
            )
            return solutions

        except Exception as e:
            print(f"❌ Solution search failed: {e}")
            self._set_search_status(
                ok=False,
                unavailable=True,
                error_code="search_failed",
                reason=str(e),
            )
            return []

    def store_solution(self, record: SolutionRecord) -> bool:
        """
        存储解决方案到知识库

        Args:
            record: SolutionRecord 实例

        Returns:
            存储是否成功
        """
        if not self.embedder.client:
            if not self._warned_embedder:
                print("⚠️ ZHIPUAI_API_KEY missing; solution storage disabled.")
                self._warned_embedder = True
            self._set_store_status(
                ok=False,
                unavailable=True,
                error_code="embedder_unavailable",
                reason="ZHIPUAI_API_KEY missing",
            )
            return False

        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; solution storage disabled.")
                self._warned_client = True
            self._set_store_status(
                ok=False,
                unavailable=True,
                error_code="vector_store_unavailable",
                reason=self._client_unavailable_reason or "Qdrant client unavailable",
            )
            return False

        try:
            # 计算签名
            record.error_signature = self._compute_signature(
                record.error_type,
                record.error_message,
                record.stack_trace,
                record.environment,
            )

            # 生成向量
            search_text = self._build_search_text(
                record.error_type,
                record.error_message,
                record.stack_trace
            )
            vector = self.embedder.get_embedding(search_text)

            # 生成唯一 ID
            point_id = str(uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"solution:{record.error_signature}:{record.agent_id}:{record.timestamp}"
            ))

            # 存入向量库
            self.client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=asdict(record)
                )]
            )

            print(f"💾 Solution stored: {record.error_type} (sig: {record.error_signature})")
            self._set_store_status(
                ok=True,
                unavailable=False,
                error_code=None,
                reason=None,
            )
            return True

        except Exception as e:
            print(f"❌ Failed to store solution: {e}")
            self._set_store_status(
                ok=False,
                unavailable=True,
                error_code="store_failed",
                reason=str(e),
            )
            return False

    def get_best_match(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = "",
        environment: str = "",
    ) -> Optional[Dict]:
        """
        获取最佳匹配方案

        Returns:
            {"score": 0.92, "solution": {...}} 或 None
        """
        results = self.search_similar(
            error_type,
            error_message,
            stack_trace,
            limit=1,
            environment=environment,
        )
        return results[0] if results else None

    def increment_usage(self, solution_id: str) -> bool:
        """
        增加方案复用计数
        """
        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; usage increment disabled.")
                self._warned_client = True
            return False

        try:
            records = self.client.retrieve(
                collection_name=self.COLLECTION_NAME,
                ids=[solution_id]
            )
        except Exception as e:
            print(f"❌ Failed to retrieve solution record for usage increment ({solution_id}): {e}")
            return False

        if not records:
            print(f"⚠️ Solution record not found for usage increment: {solution_id}")
            return False

        try:
            payload = records[0].payload or {}
            current_usage = int(payload.get("usage_count", 0) or 0)
        except (AttributeError, TypeError, ValueError) as e:
            print(f"❌ Invalid usage payload for solution ({solution_id}): {e}")
            return False

        next_usage = current_usage + 1
        try:
            self.client.set_payload(
                collection_name=self.COLLECTION_NAME,
                payload={"usage_count": next_usage},
                points=[solution_id]
            )
            return True
        except Exception as e:
            print(f"❌ Failed to increment usage for solution ({solution_id}): {e}")
            return False

    def record_feedback(self, solution_id: str, result: str) -> bool:
        normalized_result = (result or "").strip().lower()
        if normalized_result not in {"passed", "failed"}:
            self._set_feedback_status(
                ok=False,
                unavailable=False,
                error_code="invalid_feedback_result",
                reason="result must be 'passed' or 'failed'",
            )
            return False

        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; feedback recording disabled.")
                self._warned_client = True
            self._set_feedback_status(
                ok=False,
                unavailable=True,
                error_code="vector_store_unavailable",
                reason=self._client_unavailable_reason or "Qdrant client unavailable",
            )
            return False

        try:
            records = self.client.retrieve(
                collection_name=self.COLLECTION_NAME,
                ids=[solution_id]
            )
        except Exception as e:
            print(f"❌ Failed to retrieve solution record for feedback ({solution_id}): {e}")
            self._set_feedback_status(
                ok=False,
                unavailable=True,
                error_code="feedback_retrieve_failed",
                reason=str(e),
            )
            return False

        if not records:
            print(f"⚠️ Solution record not found for feedback: {solution_id}")
            self._set_feedback_status(
                ok=False,
                unavailable=False,
                error_code="solution_not_found",
                reason="solution record not found",
            )
            return False

        try:
            payload = records[0].payload or {}
            success_count = int(payload.get("success_count", 0) or 0)
            failure_count = int(payload.get("failure_count", 0) or 0)
        except (AttributeError, TypeError, ValueError) as e:
            print(f"❌ Invalid feedback payload for solution ({solution_id}): {e}")
            self._set_feedback_status(
                ok=False,
                unavailable=False,
                error_code="invalid_feedback_payload",
                reason=str(e),
            )
            return False

        if normalized_result == "passed":
            success_count += 1
        else:
            failure_count += 1

        total = success_count + failure_count
        success_rate = (success_count / total) if total else None

        try:
            self.client.set_payload(
                collection_name=self.COLLECTION_NAME,
                payload={
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "success_rate": success_rate,
                    "last_feedback_at": time.time(),
                    "last_feedback_result": normalized_result,
                },
                points=[solution_id]
            )
            self._set_feedback_status(
                ok=True,
                unavailable=False,
                error_code=None,
                reason=None,
            )
            return True
        except Exception as e:
            print(f"❌ Failed to record feedback for solution ({solution_id}): {e}")
            self._set_feedback_status(
                ok=False,
                unavailable=True,
                error_code="feedback_store_failed",
                reason=str(e),
            )
            return False

    def get_stats(self) -> Dict:
        """
        获取知识库统计信息
        """
        if not self.client:
            return {
                "total_solutions": 0,
                "collection_name": self.COLLECTION_NAME,
                "ok": False,
                "unavailable": True,
                "error_code": "vector_store_unavailable",
                "reason": self._client_unavailable_reason or "Qdrant client unavailable",
            }

        try:
            info = self.client.get_collection(self.COLLECTION_NAME)
            points_count = getattr(info, "points_count", None)
            if points_count is None:
                raise RuntimeError("collection stats missing points_count")
            return {
                "total_solutions": int(points_count),
                "collection_name": self.COLLECTION_NAME,
                "ok": True,
                "unavailable": False,
                "error_code": None,
                "reason": None,
            }
        except Exception as e:
            msg = f"failed to read solution stats: {e}"
            print(f"❌ Solution KB stats failed: {e}")
            return {
                "total_solutions": 0,
                "collection_name": self.COLLECTION_NAME,
                "ok": False,
                "unavailable": True,
                "error_code": "stats_failed",
                "reason": msg,
            }


if __name__ == "__main__":
    # 测试
    kb = SolutionKnowledgeBase()
    print(f"Solution KB initialized: {kb.get_stats()}")
