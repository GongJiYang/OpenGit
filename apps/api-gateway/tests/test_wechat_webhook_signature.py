import hashlib

from agent_auth.routers.wechat import WECHAT_TOKEN


def _sign(timestamp: str, nonce: str) -> str:
    payload = "".join(sorted([WECHAT_TOKEN, timestamp, nonce]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def test_wechat_webhook_post_rejects_invalid_signature(client):
    xml = """
    <xml>
        <ToUserName><![CDATA[to-user]]></ToUserName>
        <FromUserName><![CDATA[from-user]]></FromUserName>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[hello]]></Content>
    </xml>
    """.strip()

    res = client.post(
        "/api/v1/wechat/webhook",
        params={"signature": "invalid", "timestamp": "1700000000", "nonce": "abc123"},
        data=xml,
        headers={"Content-Type": "application/xml"},
    )

    assert res.status_code == 403
    assert res.json()["detail"] == "Signature verification failed"


def test_wechat_webhook_post_accepts_valid_signature(client):
    timestamp = "1700000001"
    nonce = "xyz789"
    signature = _sign(timestamp, nonce)

    xml = """
    <xml>
        <ToUserName><![CDATA[to-user]]></ToUserName>
        <FromUserName><![CDATA[from-user]]></FromUserName>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[hello]]></Content>
    </xml>
    """.strip()

    res = client.post(
        "/api/v1/wechat/webhook",
        params={"signature": signature, "timestamp": timestamp, "nonce": nonce},
        data=xml,
        headers={"Content-Type": "application/xml"},
    )

    assert res.status_code == 200
    assert "application/xml" in res.headers.get("content-type", "")
    assert "AgentHub 已收到您的消息。" in res.text
