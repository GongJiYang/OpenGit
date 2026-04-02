import pytest

from core.settings import Settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("off", "off"),
        ("observe", "observe"),
        ("enforce", "enforce"),
        (" EnFoRcE ", "enforce"),
        ("invalid", "off"),
    ],
)
def test_settings_normalized_governance_mode(monkeypatch, raw, expected):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", raw)

    settings = Settings()

    assert settings.normalized_governance_mode == expected


def test_settings_governance_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("APP_GOVERNANCE_MODE", raising=False)

    settings = Settings()

    assert settings.normalized_governance_mode == "off"


def test_settings_rejects_too_small_password_min_length(monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "6")
    monkeypatch.setenv("PASSWORD_MAX_LENGTH", "100")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "PASSWORD_MIN_LENGTH must be at least 8" in problems


def test_settings_rejects_password_max_length_smaller_than_min(monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "16")
    monkeypatch.setenv("PASSWORD_MAX_LENGTH", "12")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "PASSWORD_MAX_LENGTH must be greater than or equal to PASSWORD_MIN_LENGTH" in problems


def test_settings_rejects_invalid_agent_register_name_rate_params(monkeypatch):
    monkeypatch.setenv("AGENT_REGISTER_NAME_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("AGENT_REGISTER_NAME_WINDOW_SECONDS", "0")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "AGENT_REGISTER_NAME_MAX_ATTEMPTS must be at least 1" in problems
    assert "AGENT_REGISTER_NAME_WINDOW_SECONDS must be at least 1" in problems


def test_settings_rejects_subprocess_provider_in_strict_mode_even_with_explicit_allow(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "strict")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "APP_SANDBOX_PROVIDER=subprocess is not allowed in strict security mode" in problems


def test_settings_warn_mode_requires_explicit_allow_for_subprocess_provider(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "false")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "APP_SANDBOX_PROVIDER=subprocess requires APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX=true in warn mode" in problems


def test_settings_warn_mode_accepts_subprocess_provider_with_explicit_allow(monkeypatch):
    monkeypatch.setenv("APP_SECURITY_MODE", "warn")
    monkeypatch.setenv("APP_SANDBOX_PROVIDER", "subprocess")
    monkeypatch.setenv("APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX", "true")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "APP_SANDBOX_PROVIDER=subprocess requires APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX=true in warn mode" not in problems
    assert "APP_SANDBOX_PROVIDER=subprocess is not allowed in strict security mode" not in problems


def test_settings_rejects_redis_session_store_without_url(monkeypatch):
    monkeypatch.setenv("APP_SESSION_STORE_BACKEND", "redis")
    monkeypatch.delenv("APP_SESSION_STORE_REDIS_URL", raising=False)

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "APP_SESSION_STORE_BACKEND=redis requires APP_SESSION_STORE_REDIS_URL" in problems


def test_settings_rejects_non_positive_session_ttl(monkeypatch):
    monkeypatch.setenv("APP_SESSION_TTL_SECONDS", "0")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "APP_SESSION_TTL_SECONDS must be at least 1" in problems


def test_settings_rejects_mismatched_auth_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/main.db")
    monkeypatch.setenv("AUTH_DATABASE_URL", "sqlite:////tmp/auth.db")

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "AUTH_DATABASE_URL and DATABASE_URL must match (single-database mode)" in problems


def test_settings_accepts_matching_auth_database_url(monkeypatch):
    same_url = "sqlite:////tmp/agenthub.db"
    monkeypatch.setenv("DATABASE_URL", same_url)
    monkeypatch.setenv("AUTH_DATABASE_URL", same_url)

    settings = Settings()
    problems = settings.collect_security_problems()

    assert "AUTH_DATABASE_URL and DATABASE_URL must match (single-database mode)" not in problems
