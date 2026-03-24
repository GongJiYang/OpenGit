from sqlmodel import Session

from core.settings import clear_settings_cache
from agent_auth.models.platform import UserCreate
from agent_auth.services.user_auth import UserAuthService


def _make_user_auth_service(db_engine):
    session = Session(db_engine)
    return session, UserAuthService(session)


def test_register_rejects_weak_password_without_uppercase(db_engine):
    session, service = _make_user_auth_service(db_engine)
    try:
        payload = UserCreate(email="weak-upper@example.com", password="weakpassword1!")
        result, error = service.register(payload)

        assert result is None
        assert error == "Password must include at least one uppercase letter"
    finally:
        session.close()


def test_register_rejects_weak_password_without_special_char(db_engine):
    session, service = _make_user_auth_service(db_engine)
    try:
        payload = UserCreate(email="weak-special@example.com", password="Weakpassword1")
        result, error = service.register(payload)

        assert result is None
        assert error == "Password must include at least one special character"
    finally:
        session.close()


def test_password_baseline_allows_strong_password(monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "12")
    clear_settings_cache()
    try:
        error = UserAuthService.validate_password_baseline("StrongPass1!")
        assert error is None
    finally:
        clear_settings_cache()


def test_password_baseline_respects_custom_min_length(monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "16")
    clear_settings_cache()
    try:
        error = UserAuthService.validate_password_baseline("StrongPass1!")
        assert error == "Password must be at least 16 characters"
    finally:
        clear_settings_cache()


def test_password_baseline_can_disable_special_char_requirement(monkeypatch):
    monkeypatch.setenv("PASSWORD_REQUIRE_SPECIAL", "0")
    clear_settings_cache()
    try:
        error = UserAuthService.validate_password_baseline("Strongpassword1")
        assert error is None
    finally:
        clear_settings_cache()
