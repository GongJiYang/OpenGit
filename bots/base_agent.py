import os
import sys
import json
import requests
import subprocess
import shutil
import datetime
from typing import List, Dict, Optional

API_URL = os.getenv("AGENTHUB_API_URL", "http://127.0.0.1:8000")
AGENT_API_KEY = os.getenv("AGENT_API_KEY")
GIT_REMOTE_BASE = os.getenv("AGENTHUB_GIT_REMOTE_BASE")
WORKSPACE_DIR = os.path.abspath("./agent_workspace")

# Add root to path so we can import 'skills'
skills_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if skills_root not in sys.path:
    sys.path.append(skills_root)

from skills.registry import SkillRegistry  # noqa: E402
from skills.library.file_ops import ReadFileSkill, WriteFileSkill  # noqa: E402
from skills.library.solution_ops import SearchSolutionSkill, StoreSolutionSkill, FeedbackSolutionSkill  # noqa: E402
from skills.library.template_ops import (  # noqa: E402
    ListTemplatesSkill, GetTemplateSkill, RenderTemplateSkill,
    ReplaceBlockSkill, InsertBlockSkill, WrapBlockSkill,
    RegisterTemplateSkill, SearchTemplatesSkill
)
from skills.library.memory_ops import PersistentMemorySkill  # noqa: E402
from skills.base import ErrorInfo  # noqa: E402


class BaseAgent:
    def __init__(self, agent_id: str, role: str):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "gpt-4-turbo"

        # Skills System
        self.skills = SkillRegistry()
        self.load_default_skills()

        if not os.path.exists(WORKSPACE_DIR):
            os.makedirs(WORKSPACE_DIR)

    def load_default_skills(self):
        self.skills.register(ReadFileSkill(root_dir=WORKSPACE_DIR))
        self.skills.register(WriteFileSkill(root_dir=WORKSPACE_DIR))
        # 解决方案知识库技能
        self.skills.register(SearchSolutionSkill())
        self.skills.register(StoreSolutionSkill())
        self.skills.register(FeedbackSolutionSkill())
        # 结构化变更模板技能
        self.skills.register(ListTemplatesSkill())
        self.skills.register(GetTemplateSkill())
        self.skills.register(RenderTemplateSkill())
        self.skills.register(ReplaceBlockSkill())
        self.skills.register(InsertBlockSkill())
        self.skills.register(WrapBlockSkill())
        self.skills.register(RegisterTemplateSkill())
        self.skills.register(SearchTemplatesSkill())
        # 持久化记忆技能
        self.skills.register(PersistentMemorySkill())

    def use_skill(self, skill_name: str, **kwargs):
        skill = self.skills.get(skill_name)
        if not skill:
            return f"Error: Skill '{skill_name}' not found."
        try:
            return skill.validate_and_execute(**kwargs)
        except Exception as e:
            return f"Error executing skill '{skill_name}': {e}"

    def use_skill_enveloped(self, skill_name: str, **kwargs) -> Dict:
        """
        调用技能并返回统一响应信封（含 job 等元信息）。
        不破坏原 use_skill 的返回约定，供需要结构化输出的路径使用。
        """
        skill = self.skills.get(skill_name)
        if not skill:
            return {
                "ok": False,
                "data": None,
                "message": f"Skill '{skill_name}' not found",
                "error": {
                    "code": "skill_not_found",
                    "reason": f"No such skill: {skill_name}",
                    "retriable": False,
                },
                "meta": {"agent_id": self.agent_id, "model_name": self.model_name},
            }
        try:
            # 优先使用 Skill 基类提供的 run_with_envelope（M3）
            if hasattr(skill, "run_with_envelope"):
                return skill.run_with_envelope(**kwargs)
            # 回退：老技能仅有 validate_and_execute → 包装成信封
            data = skill.validate_and_execute(**kwargs)
            if hasattr(skill, "make_envelope"):
                return skill.make_envelope(
                    ok=True,
                    data=data,
                    message="ok",
                    description=getattr(skill, "description", ""),
                )
            # 极限回退：直接手工构造
            return {
                "ok": True,
                "data": data,
                "message": "ok",
                "meta": {"agent_id": self.agent_id, "model_name": self.model_name},
                "description": getattr(skill, "description", ""),
            }
        except Exception as e:  # noqa: BLE001
            # 优先走统一信封错误
            if hasattr(skill, "make_envelope"):
                return skill.make_envelope(
                    ok=False,
                    data=None,
                    message="skill execution failed",
                    error=ErrorInfo(code="skill_execution_error", reason=str(e), retriable=False),
                    description=getattr(skill, "description", ""),
                )
            return {
                "ok": False,
                "data": None,
                "message": "skill execution failed",
                "error": {
                    "code": "skill_execution_error",
                    "reason": str(e),
                    "retriable": False,
                },
                "meta": {"agent_id": self.agent_id, "model_name": self.model_name},
                "description": getattr(skill, "description", ""),
            }

    def log(self, msg: str, emoji: str = "🤖"):
        print(f"{emoji} [{self.role.upper()}]: {msg}")

    # --- API Wrappers ---

    def create_repo(self, name: str) -> Optional[str]:
        self.log(f"Creating repo '{name}'...", "🏗️")
        try:
            headers = {"X-API-Key": AGENT_API_KEY} if AGENT_API_KEY else {}
            res = requests.post(f"{API_URL}/repos", json={"name": name}, headers=headers)
            if res.status_code == 200:
                data = res.json()
                self.log(f"Repo created at {data['path']}", "✅")
                return data['path']  # Remote path
            else:
                self.log(f"Failed to create repo: {res.text}", "❌")
                return None
        except Exception as e:
            self.log(f"API Error: {e}", "❌")
            return None

    def search_code(self, query: str) -> List[Dict]:
        try:
            res = requests.get(f"{API_URL}/search", params={"query": query, "strict": True})
            if res.status_code == 200:
                return res.json()
            if res.status_code == 503:
                detail = {}
                try:
                    detail = res.json().get("detail", {})
                except Exception:
                    detail = {}
                reason = detail.get("reason") or detail.get("error_code") or str(res.status_code)
                self.log(f"语义搜索不可用，降级为空结果: {reason}", "⚠️")
                return []
            self.log(f"语义搜索失败: {res.status_code}", "⚠️")
            return []
        except Exception as e:
            self.log(f"语义搜索请求失败: {e}", "⚠️")
            return []

    def list_repos(self) -> List[str]:
        try:
            res = requests.get(f"{API_URL}/repos")
            if res.status_code == 200:
                return res.json()
            return []
        except Exception:
            return []

    def get_remote_path(self, repo_name: str) -> Optional[str]:
        if GIT_REMOTE_BASE:
            return f"{GIT_REMOTE_BASE.rstrip('/')}/{repo_name}"
        return None

    def trigger_verify(self, repo_name: str) -> Dict:
        self.log(f"Requesting verification for {repo_name}...", "🧪")
        headers = {"X-API-Key": AGENT_API_KEY} if AGENT_API_KEY else {}
        res = requests.post(f"{API_URL}/verify", params={"repo_name": repo_name, "cmd": "pytest"}, headers=headers)
        return res.json()

    # --- Git Helpers ---

    def clone_repo(self, remote_path: str, repo_name: str) -> str:
        local_path = os.path.join(WORKSPACE_DIR, repo_name.replace(".git", ""))
        if os.path.exists(local_path):
            shutil.rmtree(local_path)

        self.log(f"Cloning {repo_name}...", "⬇️")
        subprocess.check_call(["git", "clone", remote_path, local_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return local_path

    def commit_and_push(self, repo_dir: str, message_data: Dict):
        # 1. Add
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)

        # 2. Trace Commit
        trace_json = json.dumps(message_data)
        subprocess.run(["git", "commit", "-m", trace_json], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL)

        # 3. Push
        self.log("Pushing changes...", "🚀")
        try:
            # Use run to capture stderr
            _ = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True
            )
            self.log("Push Accepted.", "✅")
            return True
        except subprocess.CalledProcessError as e:
            self.log(f"Push Rejected! Hook output:\n{e.stderr}", "❌")
            return False

    def construct_trace(self, summary: str, reasoning: List[str], intent_desc: str) -> Dict:
        """Helper to build protocol-compliant JSON"""
        return {
            "diff_summary": summary,
            "reasoning_trace": reasoning,
            "rejected_alternatives": ["None considered"],
            "context_snapshot": {
                "file_paths": [],
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {}
            },
            "intent": {
                "description": intent_desc,
                "category": "feature"
            },
            "author": {
                "agent_id": self.agent_id,
                "model_name": self.model_name
            },
            "timestamp": datetime.datetime.utcnow().isoformat()
        }

    # --- 解决方案知识库复用 (Skill Memoization) ---

    def resolve_error(self, error_type: str, error_message: str, stack_trace: str = "") -> Dict:
        """
        智能错误解决 - 技能池复用核心

        使用策略:
            1. 新异常 → 先在 KB 检索
            2. 如果命中相似度高 → 直接返回方案，LLM 只需确认+执行
            3. 如果 KB 不可用或无命中 → LLM 正常推理 → 推理成功后写回 KB

        Args:
            error_type: 错误类型 (TypeError, ImportError, etc.)
            error_message: 完整错误消息
            stack_trace: 栈追踪信息

        Returns:
            {
                "source": "kb_memoization" | "kb_unavailable_fallback" | "llm_inference",
                "solution": {...} | None,
                "action": "confirm_and_execute" | "reason_and_solve",
                "similarity": float,
                "availability": dict,
                "solution_id": str | None,
            }
        """
        kb_result = self.use_skill("search_solution",
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace
        )
        availability = kb_result.get("availability") or {}

        if kb_result.get("found"):
            similarity = kb_result.get("similarity", 0)
            if similarity >= 0.85:
                self.log(f"KB 命中! 相似度 {similarity:.2%}", "🎯")
                return {
                    "source": "kb_memoization",
                    "action": "confirm_and_execute",
                    "similarity": similarity,
                    "solution": kb_result["solution"],
                    "solution_id": kb_result["solution"].get("solution_id"),
                    "availability": availability,
                }

        if availability.get("unavailable"):
            reason = availability.get("reason") or availability.get("error_code") or "unknown"
            self.log(f"KB 不可用，降级到 LLM 推理: {reason}", "⚠️")
            return {
                "source": "kb_unavailable_fallback",
                "action": "reason_and_solve",
                "similarity": 0,
                "solution": None,
                "solution_id": None,
                "availability": availability,
            }

        self.log("KB 未命中，启动 LLM 推理...", "🧠")
        return {
            "source": "llm_inference",
            "action": "reason_and_solve",
            "similarity": 0,
            "solution": None,
            "solution_id": None,
            "availability": availability,
        }

    def store_solution(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str,
        solution_steps: List[str],
        solution_code: str = "",
        environment: str = "",
        result: str = "passed",
        confidence: float = 1.0
    ) -> bool:
        """
        将解决方案写入知识库

        应在 LLM 推理成功后调用此方法

        Args:
            error_type: 错误类型
            error_message: 完整错误消息
            stack_trace: 栈追踪
            solution_steps: 修复步骤列表
            solution_code: 修复代码片段
            environment: 运行环境信息
            result: 结果状态 "passed" / "failed"
            confidence: 方案置信度

        Returns:
            存储是否成功
        """
        result = self.use_skill("store_solution",
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
            solution_steps=solution_steps,
            solution_code=solution_code,
            environment=environment,
            result=result,
            confidence=confidence,
            agent_id=self.agent_id
        )

        if result.get("stored"):
            self.log(f"解决方案已写入 KB (sig: {result.get('signature')})", "💾")
            return True

        availability = result.get("availability") or {}
        if availability.get("unavailable"):
            reason = availability.get("reason") or availability.get("error_code") or "unknown"
            self.log(f"解决方案未写入 KB（后端不可用）: {reason}", "⚠️")
            return False

        if availability.get("error_code") or availability.get("reason"):
            detail = availability.get("reason") or availability.get("error_code")
            self.log(f"解决方案写入失败: {detail}", "⚠️")
            return False

        self.log("解决方案写入失败", "⚠️")
        return False

    def record_solution_feedback(self, solution_id: str, result: str) -> bool:
        feedback = self.use_skill("feedback_solution", solution_id=solution_id, result=result)
        availability = feedback.get("availability") or {}

        if feedback.get("updated"):
            self.log(f"命中方案反馈已回写 KB: {result}", "📈")
            return True

        if availability.get("unavailable"):
            reason = availability.get("reason") or availability.get("error_code") or "unknown"
            self.log(f"命中方案反馈未写入 KB（后端不可用）: {reason}", "⚠️")
            return False

        if availability.get("error_code") or availability.get("reason"):
            detail = availability.get("reason") or availability.get("error_code")
            self.log(f"命中方案反馈写入失败: {detail}", "⚠️")
            return False

        self.log("命中方案反馈写入失败", "⚠️")
        return False

    def parse_error_output(self, error_output: str) -> Dict:
        """
        解析错误输出，提取错误类型、消息和栈追踪

        Args:
            error_output: 完整的错误输出文本

        Returns:
            {
                "error_type": str,
                "error_message": str,
                "stack_trace": str
            }
        """
        import re

        lines = error_output.strip().split('\n')

        # 常见 Python 错误模式
        error_pattern = r'^(\w+Error|\w+Exception|AssertionError|SyntaxError|IndentationError|KeyError|ValueError|TypeError|AttributeError|ImportError|ModuleNotFoundError|FileNotFoundError|PermissionError|RuntimeError|StopIteration|ZeroDivisionError|IndexError): (.*)'

        error_type = "UnknownError"
        error_message = error_output[:200]
        stack_trace = ""

        for i, line in enumerate(lines):
            match = re.match(error_pattern, line)
            if match:
                error_type = match.group(1)
                error_message = match.group(2)
                # 剩余部分作为栈追踪
                stack_trace = '\n'.join(lines[i+1:i+10])  # 取后续最多10行
                break

        return {
            "error_type": error_type,
            "error_message": error_message,
            "stack_trace": stack_trace
        }
