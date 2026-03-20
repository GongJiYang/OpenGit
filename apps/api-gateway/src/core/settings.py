import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None, case_sensitive=True)

    # Security mode and feature toggles
    app_security_mode: str = Field(default="strict", alias="APP_SECURITY_MODE")
    app_enable_indexer: bool = Field(default=False, alias="APP_ENABLE_INDEXER")
    app_enable_sandbox: bool = Field(default=False, alias="APP_ENABLE_SANDBOX")
    run_scheduler: bool = Field(default=False, alias="RUN_SCHEDULER")

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

    @property
    def normalized_security_mode(self) -> str:
        mode = self.app_security_mode.strip().lower()
        return mode if mode in {"strict", "warn"} else "strict"

    @property
    def effective_jwt_secret(self) -> Optional[str]:
        return self.jwt_secret

    @property
    def effective_jwt_secret_key(self) -> Optional[str]:
        return self.jwt_secret_key

    def collect_security_problems(self) -> list[str]:
        problems: list[str] = []

        if self.jwt_secret and self.jwt_secret_key and self.jwt_secret != self.jwt_secret_key:
            problems.append("JWT_SECRET and JWT_SECRET_KEY must match")

        if not self.jwt_secret:
            problems.append("JWT_SECRET is not set")
        if not self.jwt_secret_key:
            problems.append("JWT_SECRET_KEY is not set")

        if self.jwt_secret == "change-this-in-production":
            problems.append("JWT_SECRET uses insecure default")
        if self.jwt_secret_key == "dev-secret-key-change-in-production":
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
