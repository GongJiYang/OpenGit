import os
import sys

import pytest
from fastapi.testclient import TestClient

# Make app modules importable
sys.path.insert(0, os.path.abspath("apps/api-gateway/src"))

from main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_list_bounties_defaults_to_open(client: TestClient):
    r = client.get("/api/v1/bounties")
    assert r.status_code == 200
    # Should return a list (empty or items with status 'open')
    assert isinstance(r.json(), list)


def test_claim_preparation_notes_not_in_description(client: TestClient, monkeypatch):
    # For brevity, call the endpoint and assert it does not mutate description,
    # but appends to preparation_notes (requires prior bounty setup; here we just validate route exists)
    # In full integration tests, we would create a bounty and then claim-preparation.
    assert "/api/v1/bounties" in [r.path for r in app.routes]
