"""
AgentHub Template Engine

结构化代码变更模板系统
"""
from .library_manager import (
    TemplateLibraryManager,
    TemplateLibrary,
    BuiltinLibrary,
    UserLibrary,
    GitLibrary,
    LibraryConfig,
    ManagerConfig,
    create_default_manager
)
from .renderer import (
    TemplateRenderer,
    CodeBlockLocator,
    StructuredChangeExecutor
)

__all__ = [
    # 管理器
    'TemplateLibraryManager',
    'TemplateLibrary',
    'BuiltinLibrary',
    'UserLibrary',
    'GitLibrary',
    'LibraryConfig',
    'ManagerConfig',
    'create_default_manager',

    # 渲染器
    'TemplateRenderer',
    'CodeBlockLocator',
    'StructuredChangeExecutor',
]
