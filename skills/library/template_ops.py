"""
模板操作技能
提供 LLM 可调用的结构化变更工具
"""
import os
import sys
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from skills.base import Skill



def _load_template_deps():
    """Load template-engine and protocol deps with monorepo path fallback."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/template-engine/src"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../packages/protocol/src"))

    from agenthub_protocol.template import ParameterType, Template, TemplateMetadata, TemplateParameter
    from agenthub_template.library_manager import TemplateLibraryManager, create_default_manager
    from agenthub_template.renderer import StructuredChangeExecutor, TemplateRenderer

    return (
        TemplateLibraryManager,
        create_default_manager,
        TemplateRenderer,
        StructuredChangeExecutor,
        Template,
        TemplateParameter,
        ParameterType,
        TemplateMetadata,
    )


(
    TemplateLibraryManager,
    create_default_manager,
    TemplateRenderer,
    StructuredChangeExecutor,
    Template,
    TemplateParameter,
    ParameterType,
    TemplateMetadata,
) = _load_template_deps()


# ============== 输入模型 ==============

class ListTemplatesInput(BaseModel):
    """列出模板输入"""
    language: Optional[str] = Field(None, description="过滤语言: python, typescript")
    tags: Optional[List[str]] = Field(None, description="过滤标签")


class GetTemplateInput(BaseModel):
    """获取模板详情输入"""
    template_id: str = Field(..., description="模板ID，如 'async-handler-v2' 或 'builtin:async-handler-v2'")


class RenderTemplateInput(BaseModel):
    """渲染模板输入"""
    template_id: str = Field(..., description="模板ID")
    parameters: Dict[str, Any] = Field(..., description="模板参数")


class ReplaceBlockInput(BaseModel):
    """替换代码块输入"""
    file: str = Field(..., description="目标文件路径")
    location: str = Field(..., description="定位: 'main.py:50-90' 或 '#UserService.login' 或 'semantic:登录逻辑'")
    template_id: str = Field(..., description="模板ID")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="模板参数")
    backup: bool = Field(True, description="是否备份原文件")


class InsertBlockInput(BaseModel):
    """插入代码块输入"""
    file: str = Field(..., description="目标文件路径")
    location: str = Field(..., description="定位位置")
    template_id: str = Field(..., description="模板ID")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="模板参数")
    position: Literal["before", "after", "inside"] = Field("after", description="插入位置")


class WrapBlockInput(BaseModel):
    """包装代码块输入"""
    file: str = Field(..., description="目标文件路径")
    location: str = Field(..., description="定位位置")
    template_id: str = Field(..., description="包装模板ID（如装饰器模板）")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="模板参数")


class RegisterTemplateInput(BaseModel):
    """注册用户模板输入"""
    id: str = Field(..., description="模板唯一ID")
    name: str = Field(..., description="模板名称")
    description: str = Field("", description="模板描述")
    language: str = Field("python", description="目标语言")
    template: str = Field(..., description="模板内容（Jinja2格式）")
    parameters: List[Dict[str, Any]] = Field(default_factory=list, description="参数定义")
    tags: List[str] = Field(default_factory=list, description="标签")


class DeleteTemplateInput(BaseModel):
    """删除用户模板输入"""
    template_id: str = Field(..., description="要删除的模板ID")


class SearchTemplatesInput(BaseModel):
    """搜索模板输入"""
    query: str = Field(..., description="搜索关键词")
    language: Optional[str] = Field(None, description="过滤语言")


# ============== 全局管理器实例 ==============

_manager: Optional[TemplateLibraryManager] = None
_renderer: Optional[TemplateRenderer] = None
_executor: Optional[StructuredChangeExecutor] = None


def get_manager() -> TemplateLibraryManager:
    """获取模板库管理器单例"""
    global _manager
    if _manager is None:
        _manager = create_default_manager()
        _manager.load_all()
    return _manager


def get_renderer() -> TemplateRenderer:
    """获取渲染器单例"""
    global _renderer
    if _renderer is None:
        _renderer = TemplateRenderer()
    return _renderer


def get_executor() -> StructuredChangeExecutor:
    """获取执行器单例"""
    global _executor
    if _executor is None:
        _executor = StructuredChangeExecutor()
    return _executor


# ============== 技能实现 ==============

class ListTemplatesSkill(Skill):
    """
    列出可用模板

    列出所有已加载的模板，支持按语言和标签过滤
    """
    name = "list_templates"
    description = "列出所有可用的代码模板，可按语言/标签过滤"
    input_schema = ListTemplatesInput

    def execute(self, language: str = None, tags: List[str] = None) -> dict:
        manager = get_manager()
        templates = manager.list_templates(language=language, tags=tags)

        return {
            "success": True,
            "count": len(templates),
            "templates": templates
        }


class GetTemplateSkill(Skill):
    """
    获取模板详情

    查看模板的完整定义、参数说明和使用示例
    """
    name = "get_template"
    description = "获取模板的详细信息和参数定义"
    input_schema = GetTemplateInput

    def execute(self, template_id: str) -> dict:
        manager = get_manager()
        template = manager.get_template(template_id)

        if not template:
            return {
                "success": False,
                "error": f"模板不存在: {template_id}"
            }

        return {
            "success": True,
            "template": {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "language": template.language,
                "template_preview": template.template[:500] + "..." if len(template.template) > 500 else template.template,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type.value,
                        "required": p.required,
                        "default": p.default,
                        "description": p.description,
                        "options": p.options
                    }
                    for p in template.parameters
                ],
                "tags": template.metadata.tags,
                "source": template.source_library,
                "examples": template.metadata.examples
            }
        }


class RenderTemplateSkill(Skill):
    """
    渲染模板

    使用指定参数渲染模板，返回生成的代码
    """
    name = "render_template"
    description = "使用参数渲染模板，返回生成的代码"
    input_schema = RenderTemplateInput

    def execute(self, template_id: str, parameters: Dict[str, Any]) -> dict:
        manager = get_manager()
        renderer = get_renderer()

        template = manager.get_template(template_id)
        if not template:
            return {
                "success": False,
                "error": f"模板不存在: {template_id}"
            }

        try:
            result = renderer.render(template, parameters)
            return {
                "success": True,
                "content": result.content,
                "template_id": result.template_id,
                "parameters_used": result.parameters_used,
                "warnings": result.warnings
            }
        except ValueError as e:
            return {
                "success": False,
                "error": str(e)
            }


class ReplaceBlockSkill(Skill):
    """
    替换代码块

    用模板渲染的内容替换文件中指定位置的代码块
    这是核心的结构化变更工具
    """
    name = "replace_block"
    description = "用模板生成的内容替换文件中的代码块。location格式: 'file.py:50-90' 或 'file.py#ClassName.method'"
    input_schema = ReplaceBlockInput

    def execute(
        self,
        file: str,
        location: str,
        template_id: str,
        parameters: Dict[str, Any] = None,
        backup: bool = True
    ) -> dict:
        if parameters is None:
            parameters = {}

        manager = get_manager()
        renderer = get_renderer()
        executor = get_executor()

        # 获取模板
        template = manager.get_template(template_id)
        if not template:
            return {
                "success": False,
                "error": f"模板不存在: {template_id}"
            }

        # 渲染模板
        try:
            rendered = renderer.render(template, parameters)
        except ValueError as e:
            return {
                "success": False,
                "error": f"模板渲染失败: {e}"
            }

        # 执行替换
        try:
            result = executor.replace_block(
                file_path=file,
                location=location,
                new_content=rendered.content,
                backup=backup
            )
            return {
                "success": True,
                "file": file,
                "location": result["location"],
                "lines_changed": result["lines_changed"],
                "backup_path": result["backup_path"],
                "template_used": template_id,
                "parameters_used": rendered.parameters_used
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"替换失败: {e}"
            }


class InsertBlockSkill(Skill):
    """
    插入代码块

    在指定位置插入模板渲染的内容
    """
    name = "insert_block"
    description = "在文件指定位置插入模板生成的代码块"
    input_schema = InsertBlockInput

    def execute(
        self,
        file: str,
        location: str,
        template_id: str,
        parameters: Dict[str, Any] = None,
        position: str = "after"
    ) -> dict:
        if parameters is None:
            parameters = {}

        manager = get_manager()
        renderer = get_renderer()
        executor = get_executor()

        template = manager.get_template(template_id)
        if not template:
            return {
                "success": False,
                "error": f"模板不存在: {template_id}"
            }

        try:
            rendered = renderer.render(template, parameters)
        except ValueError as e:
            return {
                "success": False,
                "error": f"模板渲染失败: {e}"
            }

        try:
            result = executor.insert_block(
                file_path=file,
                location=location,
                content=rendered.content,
                position=position
            )
            return {
                "success": True,
                "file": file,
                "insert_line": result["insert_line"],
                "position": position,
                "template_used": template_id
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"插入失败: {e}"
            }


class WrapBlockSkill(Skill):
    """
    包装代码块

    用模板内容包装目标代码块（如添加装饰器）
    """
    name = "wrap_block"
    description = "用模板内容包装代码块，常用于添加装饰器"
    input_schema = WrapBlockInput

    def execute(
        self,
        file: str,
        location: str,
        template_id: str,
        parameters: Dict[str, Any] = None
    ) -> dict:
        if parameters is None:
            parameters = {}

        manager = get_manager()
        renderer = get_renderer()
        executor = get_executor()

        template = manager.get_template(template_id)
        if not template:
            return {
                "success": False,
                "error": f"模板不存在: {template_id}"
            }

        try:
            rendered = renderer.render(template, parameters)
        except ValueError as e:
            return {
                "success": False,
                "error": f"模板渲染失败: {e}"
            }

        try:
            result = executor.wrap_block(
                file_path=file,
                location=location,
                wrapper_template=rendered.content
            )
            return {
                "success": True,
                "file": file,
                "wrapper_line": result["wrapper_line"],
                "template_used": template_id
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"包装失败: {e}"
            }


class RegisterTemplateSkill(Skill):
    """
    注册用户模板

    将自定义模板添加到用户模板库
    """
    name = "register_template"
    description = "注册自定义模板到用户模板库"
    input_schema = RegisterTemplateInput

    def execute(
        self,
        id: str,
        name: str,
        template: str,
        description: str = "",
        language: str = "python",
        parameters: List[Dict[str, Any]] = None,
        tags: List[str] = None
    ) -> dict:
        if parameters is None:
            parameters = []
        if tags is None:
            tags = []

        manager = get_manager()

        # 构建参数列表
        param_objects = []
        for p in parameters:
            p['type'] = ParameterType(p.get('type', 'string'))
            param_objects.append(TemplateParameter(**p))

        # 构建模板对象
        new_template = Template(
            id=id,
            name=name,
            description=description,
            language=language,
            template=template,
            parameters=param_objects,
            metadata=TemplateMetadata(tags=tags)
        )

        success = manager.register_user_template(new_template)

        return {
            "success": success,
            "template_id": id,
            "message": "模板注册成功" if success else "模板注册失败"
        }


class DeleteTemplateSkill(Skill):
    """
    删除用户模板

    从用户模板库中删除模板
    """
    name = "delete_template"
    description = "从用户模板库删除模板（只能删除用户自定义模板）"
    input_schema = DeleteTemplateInput

    def execute(self, template_id: str) -> dict:
        manager = get_manager()
        success = manager.delete_user_template(template_id)

        return {
            "success": success,
            "template_id": template_id,
            "message": "模板删除成功" if success else "模板删除失败（可能不存在或非用户模板）"
        }


class SearchTemplatesSkill(Skill):
    """
    搜索模板

    通过关键词搜索匹配的模板
    """
    name = "search_templates"
    description = "通过关键词搜索模板"
    input_schema = SearchTemplatesInput

    def execute(self, query: str, language: str = None) -> dict:
        manager = get_manager()
        results = manager.search_templates(query, language=language)

        return {
            "success": True,
            "query": query,
            "count": len(results),
            "results": results
        }


class GetTemplateStatsSkill(Skill):
    """获取模板库统计信息"""

    name = "get_template_stats"
    description = "获取模板库的统计信息"
    input_schema = type('EmptyInput', (BaseModel,), {})

    def execute(self) -> dict:
        manager = get_manager()
        return {
            "success": True,
            **manager.get_stats()
        }


# ============== 技能注册表导出 ==============

ALL_TEMPLATE_SKILLS = [
    ListTemplatesSkill,
    GetTemplateSkill,
    RenderTemplateSkill,
    ReplaceBlockSkill,
    InsertBlockSkill,
    WrapBlockSkill,
    RegisterTemplateSkill,
    DeleteTemplateSkill,
    SearchTemplatesSkill,
    GetTemplateStatsSkill,
]
