import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MEM0_PROVIDER_ALIAS = "langchain"
DEFAULT_MEM0_DIR = os.path.abspath("./agenthub_data/mem0/runtime")
DEFAULT_MEM0_TELEMETRY = "False"
_ROLE_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")

try:
    from mem0 import Memory
    import mem0.memory.main as mem0_main_module
    import mem0.memory.setup as mem0_setup_module
    from mem0.embeddings.base import EmbeddingBase
    from mem0.llms.base import BaseLlmConfig, LLMBase
    from mem0.utils.factory import EmbedderFactory, LlmFactory
except Exception as exc:  # pragma: no cover - depends on optional runtime deps
    Memory = None
    mem0_main_module = None
    mem0_setup_module = None
    EmbeddingBase = object
    BaseLlmConfig = object
    LLMBase = object
    EmbedderFactory = None
    LlmFactory = None
    logger.warning("Mem0 dependencies unavailable: %s", exc)


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class ZhipuLLM(LLMBase):
    """Mem0-compatible LLM adapter backed by ZhipuAI."""

    def __init__(self, config: Optional[Any] = None):
        super().__init__(config)
        from zhipuai import ZhipuAI

        self.api_key = _config_value(config, "api_key") or os.getenv("ZHIPUAI_API_KEY")
        self.model = _config_value(config, "model", "glm-4-flash")
        self.temperature = _config_value(config, "temperature", 0.1)
        self.max_tokens = _config_value(config, "max_tokens", 2000)
        self.client = ZhipuAI(api_key=self.api_key)

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> str:
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = tool_choice
        request_kwargs.update(kwargs)
        response = self.client.chat.completions.create(**request_kwargs)
        return response.choices[0].message.content or ""


class ZhipuEmbedder(EmbeddingBase):
    """Mem0-compatible embedding adapter backed by ZhipuAI."""

    def __init__(self, config: Optional[Any] = None):
        super().__init__(config)
        from zhipuai import ZhipuAI

        self.api_key = _config_value(config, "api_key") or os.getenv("ZHIPUAI_API_KEY")
        self.model = _config_value(config, "model", "embedding-2")
        self.client = ZhipuAI(api_key=self.api_key)

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        del memory_action
        response = self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding


class MemoryService:
    """
    Agent persistent memory built on top of Mem0 with ZhipuAI adapters.

    The service is intentionally fail-soft:
    - Missing optional dependencies won't crash the API.
    - Missing API keys disable memory features with an explicit reason.
    - Dev environments fall back to a local Qdrant path under `agenthub_data/`.

    Initialization is lazy so env updates after import can take effect without
    process restart.
    """

    _memory_cache: Dict[str, Any] = {}

    def __init__(self, collection_name: str = "agent_memories"):
        self.collection_name = collection_name
        self.memory = None
        self.enabled = False
        self.disabled_reason: Optional[str] = None
        self._active_cache_key: Optional[str] = None

        self.history_db_path = ""
        self.runtime_root = ""
        self.local_qdrant_root = ""
        self.runtime_dir = ""
        self.local_qdrant_path = ""

        self._refresh_runtime_paths()

    def _refresh_runtime_paths(self) -> None:
        self.history_db_path = os.getenv(
            "MEM0_HISTORY_DB_PATH",
            os.path.abspath("./agenthub_data/mem0/history.db"),
        )
        self.runtime_root = os.getenv("MEM0_DIR", DEFAULT_MEM0_DIR)
        self.local_qdrant_root = os.getenv(
            "MEM0_QDRANT_PATH",
            os.path.abspath("./agenthub_data/mem0/qdrant"),
        )
        self.runtime_dir = self._resolve_runtime_dir()
        self.local_qdrant_path = self._resolve_local_qdrant_path()

    def _is_initialized(self) -> bool:
        return self.memory is not None and self.enabled

    def _build_mem0_config(self, zhipu_api_key: str, qdrant_url: Optional[str], qdrant_api_key: Optional[str]) -> Dict[str, Any]:
        vector_store_config: Dict[str, Any] = {
            "collection_name": self.collection_name,
            "embedding_model_dims": 1024,
        }
        if qdrant_url and qdrant_url != ":memory:":
            vector_store_config["url"] = qdrant_url
            if qdrant_api_key:
                vector_store_config["api_key"] = qdrant_api_key
        else:
            vector_store_config["path"] = self.local_qdrant_path
            vector_store_config["on_disk"] = True

        return {
            "vector_store": {
                "provider": "qdrant",
                "config": vector_store_config,
            },
            "llm": {
                "provider": MEM0_PROVIDER_ALIAS,
                "config": {
                    "api_key": zhipu_api_key,
                    "model": os.getenv("MEM0_ZHIPU_LLM_MODEL", "glm-4-flash"),
                },
            },
            "embedder": {
                "provider": MEM0_PROVIDER_ALIAS,
                "config": {
                    "api_key": zhipu_api_key,
                    "model": os.getenv("MEM0_ZHIPU_EMBED_MODEL", "embedding-2"),
                },
            },
            "history_db_path": self.history_db_path,
        }

    def _set_defaults_for_init(self) -> None:
        os.makedirs(DEFAULT_MEM0_DIR, exist_ok=True)
        os.environ.setdefault("MEM0_DIR", DEFAULT_MEM0_DIR)
        os.environ.setdefault("MEM0_TELEMETRY", DEFAULT_MEM0_TELEMETRY)

    def initialize(self, force_refresh: bool = False) -> bool:
        if self._is_initialized() and not force_refresh:
            return True

        self.memory = None
        self.enabled = False
        self.disabled_reason = None
        self._active_cache_key = None

        self._set_defaults_for_init()
        self._refresh_runtime_paths()

        if Memory is None:
            self.disabled_reason = "mem0ai is not installed"
            return False

        zhipu_api_key = os.getenv("ZHIPUAI_API_KEY")
        if not zhipu_api_key:
            self.disabled_reason = "ZHIPUAI_API_KEY is not configured"
            return False

        self._register_custom_providers()

        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")

        os.makedirs(os.path.dirname(self.history_db_path), exist_ok=True)
        os.makedirs(self.runtime_dir, exist_ok=True)
        os.makedirs(self.local_qdrant_path, exist_ok=True)

        config = self._build_mem0_config(zhipu_api_key, qdrant_url, qdrant_api_key)
        cache_key = self._cache_key(config)
        if not force_refresh:
            cached_memory = self._memory_cache.get(cache_key)
            if cached_memory is not None:
                self.memory = cached_memory
                self.enabled = True
                self._active_cache_key = cache_key
                return True

        try:
            self._set_mem0_runtime_dir()
            self.memory = Memory.from_config(config)
            self.enabled = True
            self._active_cache_key = cache_key
            self._memory_cache[cache_key] = self.memory
            logger.info("Mem0 memory service initialized using ZhipuAI + Qdrant")
            return True
        except Exception as exc:
            self.disabled_reason = str(exc)
            self.memory = None
            logger.exception("Mem0 initialization failed")
            return False

    def refresh(self) -> bool:
        return self.initialize(force_refresh=True)

    def _register_custom_providers(self) -> None:
        if not LlmFactory or not EmbedderFactory:
            return
        LlmFactory.provider_to_class[MEM0_PROVIDER_ALIAS] = (
            "agent_auth.services.memory_service.ZhipuLLM",
            BaseLlmConfig,
        )
        EmbedderFactory.provider_to_class[MEM0_PROVIDER_ALIAS] = (
            "agent_auth.services.memory_service.ZhipuEmbedder"
        )

    def status(self) -> Dict[str, Any]:
        qdrant_url = os.getenv("QDRANT_URL")
        return {
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "provider": "zhipuai",
            "collection_name": self.collection_name,
            "qdrant_mode": "remote" if qdrant_url and qdrant_url != ":memory:" else "local_path",
            "history_db_path": self.history_db_path,
            "qdrant_path": None if qdrant_url and qdrant_url != ":memory:" else self.local_qdrant_path,
        }

    def _ensure_ready(self) -> bool:
        return self.initialize(force_refresh=False)

    @staticmethod
    def _normalize_role(role: Optional[str]) -> str:
        if not role:
            return "default"
        normalized = _ROLE_SAFE_RE.sub("_", role.strip().lower()).strip("_")
        return normalized or "default"

    def _memory_actor_id(self, agent_id: str, role: Optional[str]) -> str:
        return f"{agent_id}::role::{self._normalize_role(role)}"

    def add_memory(
        self,
        agent_id: str,
        content: str,
        metadata: Optional[dict] = None,
        role: Optional[str] = None,
    ):
        if not self._ensure_ready() or not self.memory:
            return None

        normalized_role = self._normalize_role(role)
        effective_metadata = dict(metadata or {})
        effective_metadata.setdefault("memory_role", normalized_role)

        return self.memory.add(
            content,
            agent_id=self._memory_actor_id(agent_id, normalized_role),
            metadata=effective_metadata,
        )

    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        limit: int = 5,
        role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self._ensure_ready() or not self.memory:
            return []
        normalized_role = self._normalize_role(role)
        try:
            result = self.memory.search(
                query or "",
                agent_id=self._memory_actor_id(agent_id, normalized_role),
                limit=limit,
            )
        except Exception:
            logger.exception("Mem0 search failed")
            return []
        return self._normalize_search_results(result)

    def delete_all_memories(self, agent_id: str, role: Optional[str] = None):
        if self._ensure_ready() and self.memory:
            self.memory.delete_all(agent_id=self._memory_actor_id(agent_id, role))

    def _normalize_search_results(self, result: Any) -> List[Dict[str, Any]]:
        if isinstance(result, dict):
            items = result.get("results", [])
        elif isinstance(result, list):
            items = result
        else:
            items = []

        normalized: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                normalized.append({"content": str(item)})
                continue
            content = item.get("content") or item.get("memory") or item.get("text") or ""
            normalized.append({**item, "content": content})
        return normalized

    def _resolve_local_qdrant_path(self) -> str:
        if self.local_qdrant_root.endswith(".db"):
            return self.local_qdrant_root
        safe_collection = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in self.collection_name
        ).strip("_") or "default"
        return os.path.join(self.local_qdrant_root, safe_collection)

    @staticmethod
    def _fingerprint_secret(value: Optional[str]) -> str:
        if not value:
            return ""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _cache_key(self, config: Dict[str, Any]) -> str:
        vector_store = (config or {}).get("vector_store", {})
        llm = (config or {}).get("llm", {})
        embedder = (config or {}).get("embedder", {})

        key_payload = {
            "collection_name": self.collection_name,
            "history_db_path": self.history_db_path,
            "runtime_dir": self.runtime_dir,
            "vector_store_provider": vector_store.get("provider"),
            "vector_store_config": vector_store.get("config", {}),
            "llm_provider": llm.get("provider"),
            "llm_model": (llm.get("config") or {}).get("model"),
            "llm_api_key_fp": self._fingerprint_secret((llm.get("config") or {}).get("api_key")),
            "embedder_provider": embedder.get("provider"),
            "embedder_model": (embedder.get("config") or {}).get("model"),
            "embedder_api_key_fp": self._fingerprint_secret((embedder.get("config") or {}).get("api_key")),
        }

        canonical = json.dumps(key_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _resolve_runtime_dir(self) -> str:
        safe_collection = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in self.collection_name
        ).strip("_") or "default"
        return os.path.join(self.runtime_root, safe_collection)

    def _set_mem0_runtime_dir(self) -> None:
        if mem0_main_module is not None:
            mem0_main_module.mem0_dir = self.runtime_dir
        if mem0_setup_module is not None:
            mem0_setup_module.mem0_dir = self.runtime_dir


_memory_service_singleton: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    global _memory_service_singleton
    if _memory_service_singleton is None:
        _memory_service_singleton = MemoryService()
    return _memory_service_singleton


# Backward-compatible handle for existing imports.
memory_service = get_memory_service()
