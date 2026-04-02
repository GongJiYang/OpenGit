import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


INSECURE_JWT_SECRETS = {
    "change-this-in-production",
    "dev-secret-key-change-in-production",
    "CHANGE_ME",
    "CHANGE_ME_STRONG_RANDOM_VALUE",
}


class Settings(BaseSettings):
    """Centralized runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None, case_sensitive=True)

    # Security mode and feature toggles
    app_security_mode: str = Field(default="strict", alias="APP_SECURITY_MODE")
    app_governance_mode: str = Field(default="off", alias="APP_GOVERNANCE_MODE")
    app_enable_indexer: bool = Field(default=False, alias="APP_ENABLE_INDEXER")
    app_enable_sandbox: bool = Field(default=False, alias="APP_ENABLE_SANDBOX")
    app_sandbox_provider: str = Field(default="disabled", alias="APP_SANDBOX_PROVIDER")
    app_allow_insecure_subprocess_sandbox: bool = Field(
        default=False,
        alias="APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX",
    )
    run_scheduler: bool = Field(default=False, alias="RUN_SCHEDULER")

    # Session store
    app_session_store_backend: str = Field(default="memory", alias="APP_SESSION_STORE_BACKEND")
    app_session_store_redis_url: Optional[str] = Field(default=None, alias="APP_SESSION_STORE_REDIS_URL")
    app_session_ttl_seconds: int = Field(default=1800, alias="APP_SESSION_TTL_SECONDS")

    # Database
    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")
    auth_database_url: Optional[str] = Field(default=None, alias="AUTH_DATABASE_URL")

    # Shared app config
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    store_root: str = Field(default_factory=lambda: os.path.abspath("./agenthub_data/repos"))

    # Security-critical values
    jwt_secret: Optional[str] = Field(default=None, alias="JWT_SECRET")
    jwt_secret_key: Optional[str] = Field(default=None, alias="JWT_SECRET_KEY")
    wechat_token: Optional[str] = Field(default=None, alias="WECHAT_TOKEN")
    github_client_id: Optional[str] = Field(default=None, alias="GITHUB_CLIENT_ID")
    github_client_secret: Optional[str] = Field(default=None, alias="GITHUB_CLIENT_SECRET")
    internal_api_token: Optional[str] = Field(default=None, alias="INTERNAL_API_TOKEN")

    # Auth/runtime
    jwt_expiration_hours: int = Field(default=24, alias="JWT_EXPIRATION_HOURS")
    default_verification_mode: str = Field(default="auto", alias="DEFAULT_VERIFICATION_MODE")

    # Password policy baseline
    password_min_length: int = Field(default=12, alias="PASSWORD_MIN_LENGTH")
    password_max_length: int = Field(default=100, alias="PASSWORD_MAX_LENGTH")
    password_require_uppercase: bool = Field(default=True, alias="PASSWORD_REQUIRE_UPPERCASE")
    password_require_lowercase: bool = Field(default=True, alias="PASSWORD_REQUIRE_LOWERCASE")
    password_require_digit: bool = Field(default=True, alias="PASSWORD_REQUIRE_DIGIT")
    password_require_special: bool = Field(default=True, alias="PASSWORD_REQUIRE_SPECIAL")

    # Agent registration rate limit
    agent_register_rate_limit: str = Field(default="5/minute", alias="AGENT_REGISTER_RATE_LIMIT")
    agent_register_name_max_attempts: int = Field(default=3, alias="AGENT_REGISTER_NAME_MAX_ATTEMPTS")
    agent_register_name_window_seconds: int = Field(default=300, alias="AGENT_REGISTER_NAME_WINDOW_SECONDS")

    @property
    def normalized_security_mode(self) -> str:
        mode = self.app_security_mode.strip().lower()
        return mode if mode in {"strict", "warn"} else "strict"

    @property
    def normalized_governance_mode(self) -> str:
        mode = (self.app_governance_mode or "off").strip().lower()
        return mode if mode in {"off", "observe", "enforce"} else "off"

    @property
    def effective_jwt_secret(self) -> Optional[str]:
        if not self.jwt_secret:
            return None
        return None if self.jwt_secret in INSECURE_JWT_SECRETS else self.jwt_secret

    @property
    def effective_jwt_secret_key(self) -> Optional[str]:
        if not self.jwt_secret_key:
            return None
        return None if self.jwt_secret_key in INSECURE_JWT_SECRETS else self.jwt_secret_key

    @property
    def normalized_sandbox_provider(self) -> str:
        provider = (self.app_sandbox_provider or "disabled").strip().lower()
        return provider if provider in {"disabled", "subprocess", "runner"} else "disabled"

    @property
    def normalized_session_store_backend(self) -> str:
        backend = (self.app_session_store_backend or "memory").strip().lower()
        return backend if backend in {"memory", "redis"} else "memory"

    def collect_security_problems(self) -> list[str]:
        problems: list[str] = []

        if self.jwt_secret and self.jwt_secret_key and self.jwt_secret != self.jwt_secret_key:
            problems.append("JWT_SECRET and JWT_SECRET_KEY must match")

        if self.normalized_sandbox_provider == "subprocess":
            if self.normalized_security_mode == "strict":
                problems.append("APP_SANDBOX_PROVIDER=subprocess is not allowed in strict security mode")
            elif not self.app_allow_insecure_subprocess_sandbox:
                problems.append(
                    "APP_SANDBOX_PROVIDER=subprocess requires APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX=true in warn mode"
                )

        if self.normalized_session_store_backend == "redis" and not self.app_session_store_redis_url:
            problems.append("APP_SESSION_STORE_BACKEND=redis requires APP_SESSION_STORE_REDIS_URL")

        if self.app_session_ttl_seconds < 1:
            problems.append("APP_SESSION_TTL_SECONDS must be at least 1")

        if self.database_url and self.auth_database_url and self.database_url != self.auth_database_url:
            problems.append("AUTH_DATABASE_URL and DATABASE_URL must match (single-database mode)")

        if self.password_min_length < 8:
            problems.append("PASSWORD_MIN_LENGTH must be at least 8")
        if self.password_max_length < self.password_min_length:
            problems.append("PASSWORD_MAX_LENGTH must be greater than or equal to PASSWORD_MIN_LENGTH")
        if self.password_max_length > 100:
            problems.append("PASSWORD_MAX_LENGTH must be less than or equal to 100")

        if self.agent_register_name_max_attempts < 1:
            problems.append("AGENT_REGISTER_NAME_MAX_ATTEMPTS must be at least 1")
        if self.agent_register_name_window_seconds < 1:
            problems.append("AGENT_REGISTER_NAME_WINDOW_SECONDS must be at least 1")

        if not self.jwt_secret:
            problems.append("JWT_SECRET is not set")
        if not self.jwt_secret_key:
            problems.append("JWT_SECRET_KEY is not set")

        if self.jwt_secret in INSECURE_JWT_SECRETS:
            problems.append("JWT_SECRET uses insecure default")
        if self.jwt_secret_key in INSECURE_JWT_SECRETS:
            problems.append("JWT_SECRET_KEY uses insecure default")

        if not self.wechat_token:
            problems.append("WECHAT_TOKEN is not set")
        elif self.wechat_token == "agenthub_token":
            problems.append("WECHAT_TOKEN uses insecure default")

        if not self.github_client_id:
            problems.append("GITHUB_CLIENT_ID is not set")
        if not self.github_client_secret:
            problems.append("GITHUB_CLIENT_SECRET is not set")
        if not self.internal_api_token:
            problems.append("INTERNAL_API_TOKEN is not set")

        return problems


@lru_cache
def _load_settings() -> Settings:
    return Settings()


def get_settings(refresh: bool = False) -> Settings:
    if refresh:
        _load_settings.cache_clear()
    return _load_settings()


def clear_settings_cache() -> None:
    _load_settings.cache_clear()
