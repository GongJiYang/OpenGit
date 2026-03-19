from typing import Dict


def test_definitions_paging(client, auth_headers):
    r = client.get("/api/v1/skills/definitions?limit=1", headers=auth_headers)
    assert r.status_code == 200
    body: Dict = r.json()
    assert body["ok"] is True
    assert "paging" in body
    paging = body["paging"]
    assert "limit" in paging and paging["limit"] == 1
    assert "has_more" in paging
    assert isinstance(body.get("data"), list)

    # follow next_cursor if present
    if paging.get("next_cursor"):
        r2 = client.get(f"/api/v1/skills/definitions?cursor={paging['next_cursor']}&limit=1", headers=auth_headers)
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["ok"] is True
