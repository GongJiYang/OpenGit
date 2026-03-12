"""
模板数据模型定义
支持模板继承、参数化、多语言
"""
from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator
from enum import Enum
import hashlib
import time


class ParameterType(str, Enum):
    """模板参数类型"""
    STRING = "string"
    INTEGER = "int"
    FLOAT = "float"
    BOOLEAN = "bool"
    LIST = "list"
    DICT = "dict"


class TemplateParameter(BaseModel):
    """模板参数定义"""
    name: str = Field(..., description="参数名")
    type: ParameterType = Field(ParameterType.STRING, description="参数类型")
    required: bool = Field(True, description="是否必填")
    default: Optional[Any] = Field(None, description="默认值")
    description: str = Field("", description="参数说明")
    validation: Optional[str] = Field(None, description="验证规则（正则或表达式）")
    options: Optional[List[str]] = Field(None, description="可选值列表")

    @field_validator('default', mode='before')
    @classmethod
    def validate_default(cls, v, info):
        """确保默认值与类型匹配"""
        if v is None:
            return v
        param_type = info.data.get('type', ParameterType.STRING)
        # 类型检查可以在这里扩展
        return v


class TemplateMetadata(BaseModel):
    """模板元数据"""
    author: str = Field("", description="作者")
    version: str = Field("1.0.0", description="版本号")
    created_at: float = Field(default_factory=time.time, description="创建时间")
    updated_at: float = Field(default_factory=time.time, description="更新时间")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    deprecated: bool = Field(False, description="是否已废弃")
    deprecation_message: Optional[str] = Field(None, description="废弃说明")
    examples: List[Dict[str, Any]] = Field(default_factory=list, description="使用示例")


class Template(BaseModel):
    """模板定义"""
    id: str = Field(..., description="模板唯一标识")
    name: str = Field(..., description="模板名称")
    description: str = Field("", description="模板描述")
    language: str = Field("python", description="目标语言")
    template: str = Field(..., description="模板内容（Jinja2格式）")
    parameters: List[TemplateParameter] = Field(default_factory=list, description="参数列表")
    metadata: TemplateMetadata = Field(default_factory=TemplateMetadata, description="元数据")

    # 继承相关
    extends: Optional[str] = Field(None, description="继承的父模板ID (格式: library_id:template_id)")
    override: Optional[Dict[str, Any]] = Field(None, description="覆盖父模板的部分内容")
    append_template: Optional[str] = Field(None, description="追加到父模板的内容")

    # 来源信息
    source_library: str = Field("", description="来源模板库名称")
    source_type: str = Field("", description="来源类型: builtin/user/git/http")
    source_path: str = Field("", description="来源文件路径")

    def compute_hash(self) -> str:
        """计算模板内容hash"""
        content = f"{self.id}:{self.template}:{self.metadata.version}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def get_parameter_names(self) -> List[str]:
        """获取所有参数名"""
        return [p.name for p in self.parameters]

    def get_required_parameters(self) -> List[str]:
        """获取必填参数名"""
        return [p.name for p in self.parameters if p.required]

    def get_defaults(self) -> Dict[str, Any]:
        """获取所有参数的默认值"""
        return {p.name: p.default for p in self.parameters if p.default is not None}

    def validate_parameters(self, params: Dict[str, Any]) -> tuple[bool, List[str]]:
        """
        验证参数是否有效

        Returns:
            (is_valid, errors)
        """
        errors = []

        # 检查必填参数
        required = set(self.get_required_parameters())
        provided = set(params.keys())
        missing = required - provided
        if missing:
            errors.append(f"缺少必填参数: {missing}")

        # 检查未知参数
        valid_names = set(self.get_parameter_names())
        unknown = provided - valid_names
        if unknown:
            errors.append(f"未知参数: {unknown}")

        # 检查参数选项
        for p in self.parameters:
            if p.options and params.get(p.name) not in p.options and p.name in params:
                errors.append(f"参数 '{p.name}' 值 '{params[p.name]}' 不在可选值 {p.options} 中")

        return len(errors) == 0, errors


class TemplateRegistry(BaseModel):
    """模板注册表（索引文件）"""
    version: str = Field("1.0", description="注册表版本")
    library_name: str = Field(..., description="模板库名称")
    library_type: str = Field(..., description="库类型: builtin/user/git")
    templates: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="模板索引: {template_id: {name, description, tags, file}}"
    )
    updated_at: float = Field(default_factory=time.time)

    def add_template(self, template: Template, file_path: str):
        """添加模板到索引"""
        self.templates[template.id] = {
            "name": template.name,
            "description": template.description,
            "language": template.language,
            "tags": template.metadata.tags,
            "file": file_path,
            "version": template.metadata.version
        }
        self.updated_at = time.time()

    def remove_template(self, template_id: str):
        """从索引移除模板"""
        if template_id in self.templates:
            del self.templates[template_id]
            self.updated_at = time.time()

    def search(self, query: str = None, tags: List[str] = None, language: str = None) -> List[str]:
        """搜索模板"""
        results = []
        for tid, info in self.templates.items():
            # 语言过滤
            if language and info.get("language") != language:
                continue
            # 标签过滤
            if tags and not any(t in info.get("tags", []) for t in tags):
                continue
            # 文本搜索
            if query:
                q = query.lower()
                if q not in info.get("name", "").lower() and \
                   q not in info.get("description", "").lower():
                    continue
            results.append(tid)
        return results


class RenderedTemplate(BaseModel):
    """渲染后的模板结果"""
    template_id: str
    content: str
    parameters_used: Dict[str, Any]
    source_library: str
    hash: str
    warnings: List[str] = Field(default_factory=list)


# 预定义的常用模板参数
COMMON_PARAMETERS = {
    "function_name": TemplateParameter(
        name="function_name",
        type=ParameterType.STRING,
        required=True,
        description="函数名称"
    ),
    "class_name": TemplateParameter(
        name="class_name",
        type=ParameterType.STRING,
        required=True,
        description="类名称"
    ),
    "timeout_seconds": TemplateParameter(
        name="timeout_seconds",
        type=ParameterType.INTEGER,
        required=False,
        default=30,
        description="超时时间（秒）"
    ),
    "retry_count": TemplateParameter(
        name="retry_count",
        type=ParameterType.INTEGER,
        required=False,
        default=3,
        description="重试次数"
    ),
    "async_enabled": TemplateParameter(
        name="async_enabled",
        type=ParameterType.BOOLEAN,
        required=False,
        default=True,
        description="是否启用异步"
    )
}
