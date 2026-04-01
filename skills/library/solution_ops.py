"""
解决方案操作技能
提供错误解决方案的检索和存储能力
"""
import os
import sys
from typing import List, Optional

from pydantic import BaseModel, Field

from skills.base import Skill



def _load_solution_kb():
    """Load semantic-store solution KB with monorepo path fallback."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/semantic-store/src"))

    from agenthub_semantic_store.solution_kb import SolutionKnowledgeBase, SolutionRecord

    return SolutionKnowledgeBase, SolutionRecord


SolutionKnowledgeBase, SolutionRecord = _load_solution_kb()


_shared_solution_kb: Optional[object] = None


def _get_shared_solution_kb():
    global _shared_solution_kb
    if _shared_solution_kb is None:
        _shared_solution_kb = SolutionKnowledgeBase()
    return _shared_solution_kb


class SearchSolutionInput(BaseModel):
    """检索解决方案的输入参数"""
    error_type: str = Field(..., description="错误类型，如 TypeError, ImportError, AttributeError")
    error_message: str = Field(..., description="完整的错误消息文本")
    stack_trace: str = Field("", description="栈追踪信息（可选但推荐）")
    environment: str = Field("", description="当前运行环境信息（可选，用于兼容性重排）")


class StoreSolutionInput(BaseModel):
    """存储解决方案的输入参数"""
    error_type: str = Field(..., description="错误类型")
    error_message: str = Field(..., description="完整错误消息")
    stack_trace: str = Field("", description="栈追踪信息")
    environment: str = Field("", description="运行环境信息（Python版本、依赖等）")
    solution_steps: List[str] = Field(..., description="修复步骤列表")
    solution_code: str = Field("", description="修复代码片段（如有）")
    result: str = Field(..., description="结果状态：'passed' 或 'failed'")
    agent_id: str = Field(..., description="贡献者 Agent ID")
    confidence: float = Field(1.0, description="方案置信度 0.0-1.0")


class FeedbackSolutionInput(BaseModel):
    """记录命中方案执行反馈的输入参数"""
    solution_id: str = Field(..., description="命中方案的唯一 ID")
    result: str = Field(..., description="执行结果：'passed' 或 'failed'")


class SearchSolutionSkill(Skill):
    """
    检索相似解决方案技能

    在知识库中检索与当前错误相似的已解决案例
    """
    name = "search_solution"
    description = "在知识库中检索相似错误的解决方案，用于快速复用已验证的修复方案"
    input_schema = SearchSolutionInput

    def __init__(self):
        self.kb = _get_shared_solution_kb()

    def execute(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = "",
        environment: str = "",
    ) -> dict:
        """
        执行检索

        Returns:
            {
                "found": bool,
                "similarity": float,  # 相似度分数
                "solution": {         # 最佳匹配方案（如果找到）
                    "error_type": str,
                    "solution_steps": list,
                    "solution_code": str,
                    ...
                }
            }
        """
        match = self.kb.get_best_match(error_type, error_message, stack_trace, environment=environment)
        search_status_getter = getattr(self.kb, "get_last_search_status", None)
        search_status = search_status_getter() if callable(search_status_getter) else {}

        if match:
            solution = match["solution"]
            response_solution = {
                "error_type": solution.get("error_type"),
                "error_message": solution.get("error_message"),
                "solution_steps": solution.get("solution_steps", []),
                "solution_code": solution.get("solution_code", ""),
                "environment": solution.get("environment", ""),
                "confidence": solution.get("confidence", 1.0),
                "usage_count": solution.get("usage_count", 0),
                "success_count": solution.get("success_count", 0),
                "failure_count": solution.get("failure_count", 0),
                "success_rate": solution.get("success_rate"),
                "solution_id": match.get("solution_id"),
                "agent_id": solution.get("agent_id")
            }

            solution_id = match.get("solution_id")
            increment_usage = getattr(self.kb, "increment_usage", None)
            if solution_id and callable(increment_usage):
                try:
                    if increment_usage(solution_id):
                        response_solution["usage_count"] = int(response_solution.get("usage_count", 0) or 0) + 1
                except Exception:
                    pass

            return {
                "found": True,
                "similarity": match["score"],
                "solution": response_solution,
                "availability": search_status,
            }

        return {
            "found": False,
            "availability": search_status,
        }


class StoreSolutionSkill(Skill):
    """
    存储解决方案技能

    将成功解决的问题解决方案写入知识库，供后续复用
    """
    name = "store_solution"
    description = "将解决方案写入知识库供后续复用，实现技能池 Memoization"
    input_schema = StoreSolutionInput

    def __init__(self):
        self.kb = _get_shared_solution_kb()

    def execute(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = "",
        environment: str = "",
        solution_steps: List[str] = None,
        solution_code: str = "",
        result: str = "passed",
        agent_id: str = "",
        confidence: float = 1.0
    ) -> dict:
        """
        执行存储

        Returns:
            {"stored": bool, "signature": str}
        """
        if solution_steps is None:
            solution_steps = []

        record = SolutionRecord(
            error_signature="",  # 自动计算
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
            environment=environment,
            solution_steps=solution_steps,
            solution_code=solution_code,
            result=result,
            confidence=confidence,
            agent_id=agent_id
        )

        success = self.kb.store_solution(record)
        store_status_getter = getattr(self.kb, "get_last_store_status", None)
        store_status = store_status_getter() if callable(store_status_getter) else {}

        return {
            "stored": success,
            "signature": record.error_signature if success else None,
            "availability": store_status,
        }


class FeedbackSolutionSkill(Skill):
    """
    记录命中方案的执行反馈
    """
    name = "feedback_solution"
    description = "将命中方案的执行结果回写知识库，更新成功/失败反馈统计"
    input_schema = FeedbackSolutionInput

    def __init__(self):
        self.kb = _get_shared_solution_kb()

    def execute(self, solution_id: str, result: str) -> dict:
        success = self.kb.record_feedback(solution_id, result)
        feedback_status_getter = getattr(self.kb, "get_last_feedback_status", None)
        feedback_status = feedback_status_getter() if callable(feedback_status_getter) else {}
        return {
            "updated": success,
            "availability": feedback_status,
        }


class BatchSearchSolutionInput(BaseModel):
    """批量检索输入"""
    errors: List[dict] = Field(..., description="错误列表，每个包含 error_type, error_message, stack_trace")


class BatchSearchSolutionSkill(Skill):
    """
    批量检索解决方案

    一次性检索多个错误的解决方案
    """
    name = "batch_search_solution"
    description = "批量检索多个错误的解决方案"
    input_schema = BatchSearchSolutionInput

    def __init__(self):
        self.kb = _get_shared_solution_kb()
        self.search_skill = SearchSolutionSkill()

    def execute(self, errors: List[dict]) -> dict:
        """
        批量检索

        Returns:
            {
                "results": [
                    {"found": bool, "similarity": float, "solution": dict},
                    ...
                ],
                "total_found": int
            }
        """
        results = []
        total_found = 0

        for err in errors:
            result = self.search_skill.execute(
                error_type=err.get("error_type", ""),
                error_message=err.get("error_message", ""),
                stack_trace=err.get("stack_trace", "")
            )
            results.append(result)
            if result.get("found"):
                total_found += 1

        return {
            "results": results,
            "total_found": total_found,
            "total_queries": len(errors)
        }


class GetSolutionStatsInput(BaseModel):
    """获取统计信息输入（空）"""
    pass


class GetSolutionStatsSkill(Skill):
    """
    获取知识库统计信息
    """
    name = "get_solution_stats"
    description = "获取解决方案知识库的统计信息"
    input_schema = GetSolutionStatsInput

    def __init__(self):
        self.kb = _get_shared_solution_kb()

    def execute(self) -> dict:
        return self.kb.get_stats()
