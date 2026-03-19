import pytest

from core.security import validate_security_env


def test_security_env_failfast_missing_required(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        validate_security_env()


def test_security_env_failfast_rejects_insecure_defaults(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("JWT_SECRET", "change-this-in-production")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
    monkeypatch.setenv("WECHAT_TOKEN", "agenthub_token")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-github-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-github-client-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")

    with pytest.raises(RuntimeError):
        validate_security_env()


def test_security_env_warn_mode_does_not_raise(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    validate_security_env()
