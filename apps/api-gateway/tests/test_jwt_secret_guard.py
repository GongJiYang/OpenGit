from core.settings import Settings


def test_effective_jwt_secret_rejects_insecure_default_value():
    settings = Settings.model_construct(
        jwt_secret="change-this-in-production",
        jwt_secret_key="change-this-in-production",
    )

    assert settings.effective_jwt_secret is None


def test_effective_jwt_secret_key_rejects_legacy_insecure_default_value():
    settings = Settings.model_construct(
        jwt_secret="jwt-secret-value",
        jwt_secret_key="dev-secret-key-change-in-production",
    )

    assert settings.effective_jwt_secret_key is None


def test_effective_jwt_secret_rejects_change_me_placeholder_value():
    settings = Settings.model_construct(
        jwt_secret="CHANGE_ME",
        jwt_secret_key="CHANGE_ME",
    )

    assert settings.effective_jwt_secret is None
    assert settings.effective_jwt_secret_key is None
