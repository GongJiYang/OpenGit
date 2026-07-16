import pytest

from core.security import validate_security_env
from core.settings import clear_settings_cache


def test_security_env_failfast_missing_required(monkeypatch):
    clear_settings_cache()
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        validate_security_env()


def test_security_env_failfast_rejects_insecure_defaults(monkeypatch):
    clear_settings_cache()
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "change-this-in-production")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
    monkeypatch.setenv("WECHAT_TOKEN", "agenthub_token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")

    with pytest.raises(RuntimeError):
        validate_security_env()


def test_security_env_warn_mode_does_not_raise(monkeypatch):
    clear_settings_cache()
    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    validate_security_env()


def test_security_env_rejects_mismatched_jwt_aliases(monkeypatch):
    clear_settings_cache()
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-a")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret-b")
    monkeypatch.setenv("WECHAT_TOKEN", "test-wechat-token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")

    with pytest.raises(RuntimeError):
        validate_security_env()
