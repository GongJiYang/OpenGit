"""
Meta-Repository Module

This module implements the self-hosting meta-repository (agenthub-platform)
which enables PR-driven collective optimization of the platform itself.

Key Components:
- MetaRepoConfig: Configuration for the meta-repo
- PlatformPR: Pull Request management
- PlatformUpdate: Deployment tracking
- SyncService: Code synchronization
- HotReloadManager: Service hot-reload
"""

from .routes import meta_router
from .sync_service import MetaSyncService
from .hotreload import HotReloadManager

__all__ = [
    "meta_router",
    "MetaSyncService",
    "HotReloadManager",
]
