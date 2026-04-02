from core.settings import clear_settings_cache
from core.security import GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL


def test_meta_init_returns_403_when_governance_enforce(client, monkeypatch):
    monkeypatch.setenv("APP_GOVERNANCE_MODE", "enforce")
    clear_settings_cache()

    res = client.post(
        "/api/v1/meta/init",
        json={
            "deploy_root": "/tmp",
            "require_approval_count": 2,
            "require_human_approval": True,
        },
    )

    assert res.status_code == 403
    assert res.json().get("detail") == GOVERNANCE_ENFORCE_EXECUTION_FORBIDDEN_DETAIL
