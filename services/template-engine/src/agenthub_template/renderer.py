"""
模板渲染器
使用 Jinja2 渲染模板，支持参数验证和代码格式化
"""
import ast
import os
import re
import subprocess
import sys
from typing import Any, Dict, List

from jinja2 import BaseLoader, Environment, TemplateSyntaxError, UndefinedError



def _load_protocol_templates():
    """Load protocol template models with monorepo path fallback."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../packages/protocol/src"))

    from agenthub_protocol.template import RenderedTemplate, Template

    return Template, RenderedTemplate


Template, RenderedTemplate = _load_protocol_templates()


class TemplateRenderer:
    """
    模板渲染器

    支持:
    - Jinja2 语法
    - 参数验证
    - 代码格式化 (black/ruff)
    - 安全渲染（防止注入）
    """

    def __init__(self, auto_format: bool = True):
        self.auto_format = auto_format
        self.env = Environment(
            loader=BaseLoader(),
            autoescape=False,  # 代码模板不需要转义
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True
        )

        # 添加自定义过滤器
        self.env.filters['camel_case'] = self._to_camel_case
        self.env.filters['snake_case'] = self._to_snake_case
        self.env.filters['pascal_case'] = self._to_pascal_case
        self.env.filters['kebab_case'] = self._to_kebab_case

    def render(self, template: Template, parameters: Dict[str, Any]) -> RenderedTemplate:
        """
        渲染模板

        Args:
            template: 模板对象
            parameters: 参数字典

        Returns:
            RenderedTemplate 渲染结果
        """
        warnings = []

        # 1. 参数验证
        is_valid, errors = template.validate_parameters(parameters)
        if not is_valid:
            raise ValueError(f"参数验证失败: {errors}")

        # 2. 合并默认值
        merged_params = template.get_defaults()
        merged_params.update(parameters)

        # 3. 检查未知参数
        valid_names = set(template.get_parameter_names())
        unknown = set(parameters.keys()) - valid_names
        if unknown:
            warnings.append(f"忽略未知参数: {unknown}")

        # 4. 渲染模板
        try:
            jinja_template = self.env.from_string(template.template)
            content = jinja_template.render(**merged_params)
        except TemplateSyntaxError as e:
            raise ValueError(f"模板语法错误: {e}")
        except UndefinedError as e:
            raise ValueError(f"模板变量未定义: {e}")

        # 5. 代码格式化
        if self.auto_format and template.language == "python":
            content = self._format_python(content)

        return RenderedTemplate(
            template_id=template.id,
            content=content,
            parameters_used=merged_params,
            source_library=template.source_library,
            hash=template.compute_hash(),
            warnings=warnings
        )

    def render_raw(self, template_str: str, parameters: Dict[str, Any]) -> str:
        """
        直接渲染模板字符串
        """
        jinja_template = self.env.from_string(template_str)
        return jinja_template.render(**parameters)

    def _format_python(self, code: str) -> str:
        """使用 black 格式化 Python 代码"""
        try:
            result = subprocess.run(
                ["black", "--quiet", "-"],
                input=code,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return code

    # --- 自定义过滤器 ---

    @staticmethod
    def _to_camel_case(text: str) -> str:
        """转驼峰命名: my_function -> myFunction"""
        components = text.split('_')
        return components[0] + ''.join(x.title() for x in components[1:])

    @staticmethod
    def _to_snake_case(text: str) -> str:
        """转蛇形命名: MyFunction -> my_function"""
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', text)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    @staticmethod
    def _to_pascal_case(text: str) -> str:
        """转帕斯卡命名: my_function -> MyFunction"""
        return ''.join(x.title() for x in text.split('_'))

    @staticmethod
    def _to_kebab_case(text: str) -> str:
        """转短横线命名: myFunction -> my-function"""
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', text)
        return re.sub('([a-z0-9])([A-Z])', r'\1-\2', s1).lower()


class CodeBlockLocator:
    """
    代码块定位器

    支持三种定位方式:
    1. 行号: "main.py:50-90"
    2. AST节点: "main.py#UserService.login"
    3. 语义: "semantic:用户登录逻辑"
    """

    def __init__(self):
        pass

    def locate(self, file_path: str, location: str) -> Dict[str, Any]:
        """
        定位代码块

        Args:
            file_path: 文件路径
            location: 定位表达式

        Returns:
            {
                "type": "line" | "ast" | "semantic",
                "start_line": int,
                "end_line": int,
                "content": str,
                "node_name": str (仅AST)
            }
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        lines = content.split('\n')

        # 判断定位类型
        if self._is_line_location(location):
            return self._locate_by_line(file_path, lines, location)
        elif location.startswith('#'):
            return self._locate_by_ast(file_path, content, location[1:])
        elif location.startswith('semantic:'):
            return self._locate_by_semantic(file_path, content, location[9:])
        else:
            raise ValueError(f"无法识别的定位格式: {location}")

    def _is_line_location(self, location: str) -> bool:
        """判断是否为行号定位"""
        if ':' not in location and '-' not in location:
            return False
        parts = location.replace('-', ':').split(':')
        return all(p.isdigit() for p in parts if p)

    def _locate_by_line(self, file_path: str, lines: List[str], location: str) -> Dict:
        """行号定位"""
        if '-' in location:
            start, end = location.split('-')
            start_line = int(start) - 1  # 转为0索引
            end_line = int(end)
        else:
            start_line = int(location) - 1
            end_line = start_line + 1

        content = '\n'.join(lines[start_line:end_line])

        return {
            "type": "line",
            "start_line": start_line + 1,
            "end_line": end_line,
            "content": content,
            "node_name": None
        }

    def _locate_by_ast(self, file_path: str, content: str, location: str) -> Dict:
        """AST节点定位"""
        if not file_path.endswith('.py'):
            raise ValueError("AST定位目前只支持Python文件")

        tree = ast.parse(content)

        # 解析节点路径: "UserService.login" -> ["UserService", "login"]
        node_path = location.split('.')

        # 查找节点
        target_node = None

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == node_path[-1]:
                    # 检查父节点路径是否匹配
                    if len(node_path) == 1 or self._check_parent_path(tree, node, node_path[:-1]):
                        target_node = node
                        break

        if not target_node:
            raise ValueError(f"未找到AST节点: {location}")

        return {
            "type": "ast",
            "start_line": target_node.lineno,
            "end_line": target_node.end_lineno or target_node.lineno + 1,
            "content": ast.get_source_segment(content, target_node),
            "node_name": location
        }

    def _check_parent_path(self, tree, node, expected_path: List[str]) -> bool:
        """检查节点的父路径是否匹配"""
        # 简化实现：遍历查找父类
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef):
                for child in ast.iter_child_nodes(parent):
                    if child is node:
                        return parent.name == expected_path[-1] if expected_path else True
        return False

    def _locate_by_semantic(self, file_path: str, content: str, query: str) -> Dict:
        """语义定位（需要向量搜索支持）"""
        # TODO: 接入语义搜索
        # 暂时回退到文本搜索
        lines = content.split('\n')
        query_lower = query.lower()

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # 扩展到完整代码块
                start = i
                end = i + 1
                # 向上查找块开始
                while start > 0 and not lines[start - 1].strip().startswith(('def ', 'class ', 'async def ')):
                    start -= 1
                # 向下查找块结束
                while end < len(lines) and lines[end].strip() and not lines[end].startswith(('def ', 'class ', 'async def ')):
                    end += 1

                return {
                    "type": "semantic",
                    "start_line": start + 1,
                    "end_line": end + 1,
                    "content": '\n'.join(lines[start:end + 1]),
                    "node_name": query
                }

        raise ValueError(f"语义搜索未找到匹配: {query}")


class StructuredChangeExecutor:
    """
    结构化变更执行器

    执行 replace-block / insert-block / wrap-block 操作
    """

    def __init__(self):
        self.locator = CodeBlockLocator()

    def replace_block(
        self,
        file_path: str,
        location: str,
        new_content: str,
        backup: bool = True
    ) -> Dict[str, Any]:
        """
        替换代码块

        Returns:
            {"success": bool, "backup_path": str, "lines_changed": int}
        """
        # 定位
        loc_result = self.locator.locate(file_path, location)

        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 备份
        backup_path = None
        if backup:
            backup_path = f"{file_path}.bak"
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

        # 替换
        start_idx = loc_result['start_line'] - 1
        end_idx = loc_result['end_line']

        # 确保新内容以换行结尾
        if not new_content.endswith('\n'):
            new_content += '\n'

        lines[start_idx:end_idx] = [new_content]

        # 写回
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return {
            "success": True,
            "backup_path": backup_path,
            "lines_changed": end_idx - start_idx,
            "location": loc_result
        }

    def insert_block(
        self,
        file_path: str,
        location: str,
        content: str,
        position: str = "after",  # before | after | inside
        backup: bool = True
    ) -> Dict[str, Any]:
        """
        插入代码块

        Args:
            position:
                - before: 在定位位置之前插入
                - after: 在定位位置之后插入
                - inside: 在类/函数内部末尾插入
        """
        loc_result = self.locator.locate(file_path, location)

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 备份
        backup_path = None
        if backup:
            backup_path = f"{file_path}.bak"
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

        # 确定插入位置
        if position == "before":
            insert_idx = loc_result['start_line'] - 1
        elif position == "after":
            insert_idx = loc_result['end_line']
        else:  # inside
            insert_idx = loc_result['end_line'] - 1

        # 确保内容以换行结尾
        if not content.endswith('\n'):
            content += '\n'

        lines.insert(insert_idx, content)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return {
            "success": True,
            "backup_path": backup_path,
            "insert_line": insert_idx + 1,
            "location": loc_result
        }

    def wrap_block(
        self,
        file_path: str,
        location: str,
        wrapper_template: str,
        backup: bool = True
    ) -> Dict[str, Any]:
        """
        包装代码块（添加装饰器等）
        """
        loc_result = self.locator.locate(file_path, location)

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 备份
        backup_path = None
        if backup:
            backup_path = f"{file_path}.bak"
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

        # 在目标位置之前插入包装代码
        insert_idx = loc_result['start_line'] - 1

        if not wrapper_template.endswith('\n'):
            wrapper_template += '\n'

        lines.insert(insert_idx, wrapper_template)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return {
            "success": True,
            "backup_path": backup_path,
            "wrapper_line": insert_idx + 1,
            "location": loc_result
        }

    def delete_block(
        self,
        file_path: str,
        location: str,
        backup: bool = True
    ) -> Dict[str, Any]:
        """删除代码块"""
        loc_result = self.locator.locate(file_path, location)

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 备份
        backup_path = None
        if backup:
            backup_path = f"{file_path}.bak"
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

        # 删除
        start_idx = loc_result['start_line'] - 1
        end_idx = loc_result['end_line']

        deleted = lines[start_idx:end_idx]
        del lines[start_idx:end_idx]

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return {
            "success": True,
            "backup_path": backup_path,
            "deleted_content": ''.join(deleted),
            "lines_deleted": len(deleted),
            "location": loc_result
        }
