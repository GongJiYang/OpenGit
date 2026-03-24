def test_oauth_github_route_is_mounted(client):
    res = client.get("/api/v1/oauth/github")

    assert res.status_code != 404
