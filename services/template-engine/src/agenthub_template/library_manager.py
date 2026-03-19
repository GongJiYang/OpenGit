"""
模板库管理器
支持多源模板库、优先级合并、热重载
"""
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Dict, List, Optional

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import yaml



def _load_protocol_templates():
    """Load protocol template models with monorepo path fallback."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../packages/protocol/src"))

    from agenthub_protocol.template import (
        ParameterType,
        Template,
        TemplateParameter,
        TemplateRegistry,
    )

    return Template, TemplateRegistry, TemplateParameter, ParameterType


Template, TemplateRegistry, TemplateParameter, ParameterType = _load_protocol_templates()

logger = logging.getLogger(__name__)


@dataclass
class LibraryConfig:
    """模板库配置"""
    name: str
    source: str  # builtin | local path | git url | http url
    source_type: str = "local"
    priority: int = 1
    enabled: bool = True
    readonly: bool = False
    auth: Optional[Dict] = None


@dataclass
class ManagerConfig:
    """管理器配置"""
    mode: str = "merge"  # merge | override
    conflict_strategy: str = "priority"  # priority | error | warn
    extend_builtin: bool = True
    libraries: List[LibraryConfig] = field(default_factory=list)
    hot_reload: bool = True
    cache_enabled: bool = True


class TemplateLibrary(ABC):
    """模板库抽象基类"""

    def __init__(self, config: LibraryConfig):
        self.config = config
        self.name = config.name
        self.priority = config.priority
        self._templates: Dict[str, Template] = {}
        self._registry: Optional[TemplateRegistry] = None
        self._loaded = False
        self._last_load_time = 0

    @abstractmethod
    def load(self) -> Dict[str, Template]:
        """加载所有模板"""
        pass

    @abstractmethod
    def reload(self) -> Dict[str, Template]:
        """重新加载模板"""
        pass

    def is_readonly(self) -> bool:
        """是否只读"""
        return self.config.readonly

    def get_template(self, template_id: str) -> Optional[Template]:
        """获取单个模板"""
        if not self._loaded:
            self.load()
        return self._templates.get(template_id)

    def list_templates(self) -> List[str]:
        """列出所有模板ID"""
        if not self._loaded:
            self.load()
        return list(self._templates.keys())

    def search(self, query: str = None, tags: List[str] = None,
               language: str = None) -> List[str]:
        """搜索模板"""
        if not self._loaded:
            self.load()
        if self._registry:
            return self._registry.search(query, tags, language)
        return []

    def _parse_template_file(self, file_path: str) -> Optional[Template]:
        """解析模板文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 支持YAML格式
            if file_path.endswith('.yaml') or file_path.endswith('.yml'):
                data = yaml.safe_load(content)
            elif file_path.endswith('.json'):
                data = json.loads(content)
            else:
                logger.warning(f"Unsupported template format: {file_path}")
                return None

            # 解析参数
            parameters = []
            for p in data.get('parameters', []):
                if isinstance(p, dict):
                    p['type'] = ParameterType(p.get('type', 'string'))
                    parameters.append(TemplateParameter(**p))

            # 构建模板对象
            template = Template(
                id=data['id'],
                name=data.get('name', data['id']),
                description=data.get('description', ''),
                language=data.get('language', 'python'),
                template=data['template'],
                parameters=parameters,
                metadata=data.get('metadata', {}),
                extends=data.get('extends'),
                override=data.get('override'),
                append_template=data.get('append_template'),
                source_library=self.name,
                source_type=self.config.source_type,
                source_path=file_path
            )

            return template

        except Exception as e:
            logger.error(f"Failed to parse template {file_path}: {e}")
            return None


class BuiltinLibrary(TemplateLibrary):
    """平台内置模板库（只读）"""

    def __init__(self, config: LibraryConfig):
        config.source_type = "builtin"
        config.readonly = True
        super().__init__(config)

        # 内置库路径
        self.base_path = Path(__file__).parent.parent.parent.parent.parent / "templates"

    def load(self) -> Dict[str, Template]:
        """加载内置模板"""
        if self._loaded:
            return self._templates

        if not self.base_path.exists():
            logger.warning(f"Builtin template path not found: {self.base_path}")
            self._loaded = True
            return self._templates

        # 加载注册表
        registry_path = self.base_path / "registry.json"
        if registry_path.exists():
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry_data = json.load(f)
            self._registry = TemplateRegistry(**registry_data)

        # 扫描所有模板文件
        for template_file in self.base_path.rglob("*.yaml"):
            template = self._parse_template_file(str(template_file))
            if template:
                self._templates[template.id] = template

        self._loaded = True
        self._last_load_time = time.time()
        logger.info(f"Builtin library loaded: {len(self._templates)} templates")
        return self._templates

    def reload(self) -> Dict[str, Template]:
        """内置库通常不需要重载"""
        return self._templates


class UserLibrary(TemplateLibrary):
    """用户本地模板库（可读写）"""

    def __init__(self, config: LibraryConfig):
        config.source_type = "user"
        config.readonly = False
        super().__init__(config)

        self.base_path = Path(config.source).expanduser().resolve()
        self._ensure_structure()

    def _ensure_structure(self):
        """确保目录结构存在"""
        self.base_path.mkdir(parents=True, exist_ok=True)
        (self.base_path / "python").mkdir(exist_ok=True)
        (self.base_path / "typescript").mkdir(exist_ok=True)

        # 创建默认配置
        config_path = self.base_path / "config.yaml"
        if not config_path.exists():
            default_config = {
                "library_name": "user-templates",
                "description": "用户自定义模板库",
                "version": "1.0.0"
            }
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(default_config, f)

    def load(self) -> Dict[str, Template]:
        """加载用户模板"""
        if not self.base_path.exists():
            self._loaded = True
            return self._templates

        # 加载注册表
        registry_path = self.base_path / "registry.json"
        if registry_path.exists():
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry_data = json.load(f)
            self._registry = TemplateRegistry(**registry_data)

        # 扫描模板
        for template_file in self.base_path.rglob("*.yaml"):
            if template_file.name == "config.yaml":
                continue
            template = self._parse_template_file(str(template_file))
            if template:
                self._templates[template.id] = template

        self._loaded = True
        self._last_load_time = time.time()
        logger.info(f"User library loaded: {len(self._templates)} templates")
        return self._templates

    def reload(self) -> Dict[str, Template]:
        """重新加载"""
        self._templates.clear()
        self._loaded = False
        return self.load()

    def register_template(self, template: Template, file_name: str = None) -> bool:
        """注册新模板"""
        try:
            if not file_name:
                file_name = f"{template.id}.yaml"

            lang_dir = self.base_path / template.language
            lang_dir.mkdir(exist_ok=True)

            file_path = lang_dir / file_name

            # 序列化模板
            data = {
                'id': template.id,
                'name': template.name,
                'description': template.description,
                'language': template.language,
                'template': template.template,
                'parameters': [
                    {'name': p.name, 'type': p.type.value, 'required': p.required,
                     'default': p.default, 'description': p.description}
                    for p in template.parameters
                ],
                'metadata': template.metadata.model_dump(),
                'extends': template.extends,
                'override': template.override,
                'append_template': template.append_template
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            # 更新内存缓存
            template.source_library = self.name
            template.source_type = "user"
            template.source_path = str(file_path)
            self._templates[template.id] = template

            # 更新注册表
            self._update_registry(template, str(file_path))

            logger.info(f"Template registered: {template.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to register template: {e}")
            return False

    def delete_template(self, template_id: str) -> bool:
        """删除模板"""
        template = self._templates.get(template_id)
        if not template:
            return False

        try:
            file_path = Path(template.source_path)
            if file_path.exists():
                file_path.unlink()

            del self._templates[template_id]

            if self._registry:
                self._registry.remove_template(template_id)

            logger.info(f"Template deleted: {template_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete template: {e}")
            return False

    def _update_registry(self, template: Template, file_path: str):
        """更新注册表"""
        registry_path = self.base_path / "registry.json"

        if not self._registry:
            self._registry = TemplateRegistry(
                library_name="user-templates",
                library_type="user"
            )

        self._registry.add_template(template, file_path)

        with open(registry_path, 'w', encoding='utf-8') as f:
            json.dump(self._registry.model_dump(), f, indent=2, ensure_ascii=False)


class GitLibrary(TemplateLibrary):
    """Git 远程模板库"""

    def __init__(self, config: LibraryConfig):
        config.source_type = "git"
        config.readonly = True
        super().__init__(config)

        self.repo_url = config.source
        self.local_path = Path.home() / ".agenthub" / "template_cache" / config.name

    def load(self) -> Dict[str, Template]:
        """加载Git仓库模板"""
        self._ensure_cloned()

        if not self.local_path.exists():
            self._loaded = True
            return self._templates

        # 扫描模板
        for template_file in self.local_path.rglob("*.yaml"):
            template = self._parse_template_file(str(template_file))
            if template:
                self._templates[template.id] = template

        self._loaded = True
        return self._templates

    def reload(self) -> Dict[str, Template]:
        """拉取最新并重载"""
        self._pull_latest()
        self._templates.clear()
        self._loaded = False
        return self.load()

    def _ensure_cloned(self):
        """确保仓库已克隆"""
        import subprocess

        if not self.local_path.exists():
            self.local_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", self.repo_url, str(self.local_path)],
                    check=True,
                    capture_output=True
                )
                logger.info(f"Git library cloned: {self.name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone git library: {e}")

    def _pull_latest(self):
        """拉取最新代码"""
        import subprocess

        if self.local_path.exists():
            try:
                subprocess.run(
                    ["git", "pull"],
                    cwd=str(self.local_path),
                    check=True,
                    capture_output=True
                )
            except subprocess.CalledProcessError:
                pass


class TemplateLibraryManager:
    """
    模板库管理器

    支持:
    - 多源模板库加载
    - 优先级合并/覆盖
    - 模板继承解析
    - 热重载
    """

    def __init__(self, config: ManagerConfig = None):
        self.config = config or ManagerConfig()
        self.libraries: Dict[str, TemplateLibrary] = {}
        self._template_cache: Dict[str, Template] = {}
        self._lock = threading.RLock()

        # 加载配置中的库
        self._init_libraries()

    def _init_libraries(self):
        """初始化模板库"""
        for lib_config in self.config.libraries:
            if not lib_config.enabled:
                continue

            lib = self._create_library(lib_config)
            if lib:
                self.libraries[lib_config.name] = lib

    def _create_library(self, config: LibraryConfig) -> Optional[TemplateLibrary]:
        """创建模板库实例"""
        if config.source == "builtin":
            return BuiltinLibrary(config)
        elif config.source_type == "user" or config.source.startswith('./') or config.source.startswith('/'):
            return UserLibrary(config)
        elif config.source.startswith('http') or config.source.endswith('.git'):
            return GitLibrary(config)
        else:
            # 默认作为本地路径
            config.source_type = "user"
            return UserLibrary(config)

    def load_all(self):
        """加载所有模板库"""
        with self._lock:
            for lib in self.libraries.values():
                lib.load()

            # 构建合并后的模板视图
            self._rebuild_cache()

    def _rebuild_cache(self):
        """重建模板缓存（处理优先级和继承）"""
        self._template_cache.clear()

        # 按优先级排序（低优先级先加载，高优先级覆盖）
        sorted_libs = sorted(self.libraries.values(), key=lambda x: x.priority)

        for lib in sorted_libs:
            for tid, template in lib._templates.items():
                # 处理继承
                resolved = self._resolve_inheritance(template)
                self._template_cache[tid] = resolved

    def _resolve_inheritance(self, template: Template) -> Template:
        """解析模板继承"""
        if not template.extends:
            return template

        # 解析父模板引用
        parent_ref = template.extends
        parent = self.get_template(parent_ref)

        if not parent:
            logger.warning(f"Parent template not found: {parent_ref}")
            return template

        # 合并参数
        merged_params = {p.name: p for p in parent.parameters}
        if template.override and 'parameters' in template.override:
            for p in template.override['parameters']:
                if isinstance(p, dict):
                    p['type'] = ParameterType(p.get('type', 'string'))
                    param = TemplateParameter(**p)
                    merged_params[param.name] = param

        # 合并模板内容
        merged_template = parent.template
        if template.append_template:
            merged_template = merged_template + "\n" + template.append_template

        # 创建合并后的模板
        return Template(
            id=template.id,
            name=template.name or parent.name,
            description=template.description or parent.description,
            language=template.language,
            template=merged_template,
            parameters=list(merged_params.values()),
            metadata=template.metadata,
            source_library=template.source_library,
            source_type=template.source_type,
            source_path=template.source_path
        )

    def get_template(self, template_id: str) -> Optional[Template]:
        """获取模板（自动处理合并逻辑）"""
        with self._lock:
            # 先查缓存
            if template_id in self._template_cache:
                return self._template_cache[template_id]

            # 直接查找（支持 library:template 格式）
            if ':' in template_id:
                lib_name, tid = template_id.split(':', 1)
                lib = self.libraries.get(lib_name)
                if lib:
                    return lib.get_template(tid)
                return None

            # 按优先级查找
            sorted_libs = sorted(self.libraries.values(),
                                 key=lambda x: x.priority, reverse=True)
            for lib in sorted_libs:
                template = lib.get_template(template_id)
                if template:
                    resolved = self._resolve_inheritance(template)
                    self._template_cache[template_id] = resolved
                    return resolved

            return None

    def list_templates(self, language: str = None, tags: List[str] = None) -> List[Dict]:
        """列出所有可用模板"""
        with self._lock:
            if not self._template_cache:
                self.load_all()

            results = []
            seen = set()

            for tid, template in self._template_cache.items():
                if tid in seen:
                    continue
                seen.add(tid)

                # 过滤
                if language and template.language != language:
                    continue
                if tags and not any(t in template.metadata.tags for t in tags):
                    continue

                results.append({
                    "id": tid,
                    "name": template.name,
                    "description": template.description,
                    "language": template.language,
                    "tags": template.metadata.tags,
                    "source": template.source_library,
                    "source_type": template.source_type,
                    "parameters": [p.name for p in template.parameters]
                })

            return results

    def search_templates(self, query: str, language: str = None) -> List[Dict]:
        """语义搜索模板"""
        # 简单实现：文本匹配
        # TODO: 接入向量搜索
        results = []
        query_lower = query.lower()

        for template_info in self.list_templates(language=language):
            if query_lower in template_info['name'].lower() or \
               query_lower in template_info['description'].lower() or \
               any(query_lower in t.lower() for t in template_info['tags']):
                results.append(template_info)

        return results

    def register_user_template(self, template: Template, file_name: str = None) -> bool:
        """注册用户模板"""
        user_lib = None
        for lib in self.libraries.values():
            if isinstance(lib, UserLibrary) and not lib.is_readonly():
                user_lib = lib
                break

        if not user_lib:
            # 创建默认用户库
            default_path = Path.home() / ".agenthub" / "user_templates"
            config = LibraryConfig(
                name="user",
                source=str(default_path),
                priority=10
            )
            user_lib = UserLibrary(config)
            self.libraries["user"] = user_lib

        success = user_lib.register_template(template, file_name)
        if success:
            self._rebuild_cache()

        return success

    def delete_user_template(self, template_id: str) -> bool:
        """删除用户模板"""
        for lib in self.libraries.values():
            if isinstance(lib, UserLibrary) and not lib.is_readonly():
                if lib.get_template(template_id):
                    success = lib.delete_template(template_id)
                    if success:
                        self._rebuild_cache()
                    return success
        return False

    def reload_all(self):
        """重新加载所有库"""
        with self._lock:
            self._template_cache.clear()
            for lib in self.libraries.values():
                lib.reload()
            self._rebuild_cache()

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "total_templates": len(self._template_cache),
            "libraries": {
                name: {
                    "templates": len(lib._templates),
                    "type": lib.config.source_type,
                    "priority": lib.priority
                }
                for name, lib in self.libraries.items()
            }
        }


def create_default_manager() -> TemplateLibraryManager:
    """创建默认配置的管理器"""
    config = ManagerConfig(
        mode="merge",
        conflict_strategy="priority",
        extend_builtin=True,
        libraries=[
            LibraryConfig(name="builtin", source="builtin", priority=1),
            LibraryConfig(
                name="user",
                source=os.environ.get("AGENTHUB_USER_TEMPLATES",
                                      str(Path.home() / ".agenthub" / "user_templates")),
                priority=10
            )
        ]
    )
    return TemplateLibraryManager(config)
