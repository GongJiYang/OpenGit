"""
解决方案知识库 - 技能池复用核心
实现错误解决方案的存储、检索与复用
"""
import os
import uuid
import time
import hashlib
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from .embeddings import ZhipuEmbedding


@dataclass
class SolutionRecord:
    """解决方案记录结构"""
    error_signature: str           # 错误签名（类型+关键信息hash）
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

    def __init__(self):
        url = os.getenv("QDRANT_URL", ":memory:")
        api_key = os.getenv("QDRANT_API_KEY")

        try:
            if url == ":memory:":
                self.client = QdrantClient(location=":memory:")
            else:
                self.client = QdrantClient(url=url, api_key=api_key)
        except Exception as e:
            print(f"❌ Solution KB Qdrant init failed: {e}")
            self.client = None

        self.embedder = ZhipuEmbedding()
        self._warned_embedder = False
        self._warned_client = False

        if self.client:
            self._ensure_collection()

    def _ensure_collection(self):
        """确保 collection 存在"""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.COLLECTION_NAME for c in collections)

        if not exists:
            print(f"📦 Creating solution collection '{self.COLLECTION_NAME}'...")
            self.client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=VectorParams(size=self.EMBEDDING_DIM, distance=Distance.COSINE)
            )

    def _compute_signature(self, error_type: str, error_message: str) -> str:
        """
        计算错误签名
        提取关键信息，去除行号等变量
        """
        # 取错误消息第一行（去除具体行号等噪音）
        key_info = f"{error_type}:{error_message.split('\n')[0][:100]}"
        return hashlib.md5(key_info.encode()).hexdigest()[:12]

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
                import re
                cleaned = re.sub(r':\d+', '', line)  # 去除行号
                stack_lines.append(cleaned.strip())

        stack_summary = ' '.join(stack_lines[:3])
        return f"{error_type}: {error_message[:200]}\n{stack_summary}"

    def search_similar(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = "",
        limit: int = 3,
        min_confidence: float = 0.0
    ) -> List[Dict]:
        """
        检索相似错误的解决方案

        Args:
            error_type: 错误类型
            error_message: 完整错误消息
            stack_trace: 栈追踪信息
            limit: 返回结果数量限制
            min_confidence: 最低置信度过滤

        Returns:
            [{"score": 0.92, "solution": SolutionRecord-dict}, ...]
        """
        if not self.embedder.client:
            if not self._warned_embedder:
                print("⚠️ ZHIPUAI_API_KEY missing; solution search disabled.")
                self._warned_embedder = True
            return []

        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; solution search disabled.")
                self._warned_client = True
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

            results = self.client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=vector,
                query_filter=query_filter,
                limit=limit
            ).points

            solutions = []
            for hit in results:
                if hit.score >= self.SIMILARITY_THRESHOLD:
                    payload = hit.payload
                    # 置信度过滤
                    if payload.get("confidence", 1.0) >= min_confidence:
                        solutions.append({
                            "score": hit.score,
                            "solution": payload
                        })

            return solutions

        except Exception as e:
            print(f"❌ Solution search failed: {e}")
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
            return False

        if not self.client:
            if not self._warned_client:
                print("⚠️ Qdrant client unavailable; solution storage disabled.")
                self._warned_client = True
            return False

        try:
            # 计算签名
            record.error_signature = self._compute_signature(record.error_type, record.error_message)

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
            return True

        except Exception as e:
            print(f"❌ Failed to store solution: {e}")
            return False

    def get_best_match(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = ""
    ) -> Optional[Dict]:
        """
        获取最佳匹配方案

        Returns:
            {"score": 0.92, "solution": {...}} 或 None
        """
        results = self.search_similar(error_type, error_message, stack_trace, limit=1)
        return results[0] if results else None

    def increment_usage(self, solution_id: str) -> bool:
        """
        增加方案复用计数
        """
        try:
            # 获取当前记录
            record = self.client.retrieve(
                collection_name=self.COLLECTION_NAME,
                ids=[solution_id]
            )[0]

            # 更新使用计数
            payload = record.payload
            payload["usage_count"] = payload.get("usage_count", 0) + 1

            self.client.set_payload(
                collection_name=self.COLLECTION_NAME,
                payload={"usage_count": payload["usage_count"]},
                points=[solution_id]
            )
            return True
        except Exception as e:
            print(f"⚠️ Failed to increment usage: {e}")
            return False

    def get_stats(self) -> Dict:
        """
        获取知识库统计信息
        """
        try:
            info = self.client.get_collection(self.COLLECTION_NAME)
            return {
                "total_solutions": info.points_count,
                "collection_name": self.COLLECTION_NAME
            }
        except:
            return {"total_solutions": 0, "collection_name": self.COLLECTION_NAME}


if __name__ == "__main__":
    # 测试
    kb = SolutionKnowledgeBase()
    print(f"Solution KB initialized: {kb.get_stats()}")
