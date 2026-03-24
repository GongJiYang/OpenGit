import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from core.middleware import MAX_REQUEST_SIZE, limit_request_size_mw


class _ReceiveStub:
    def __init__(self, body_chunks):
        self._chunks = list(body_chunks)

    async def __call__(self):
        if self._chunks:
            chunk = self._chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(self._chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}


class _RequestStub:
    def __init__(self, headers, chunks):
        self.headers = headers
        self._receive = _ReceiveStub(chunks)


def test_limit_request_size_rejects_oversized_content_length():
    request = _RequestStub(headers={"content-length": str(MAX_REQUEST_SIZE + 1)}, chunks=[])

    async def call_next(_request):
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 413


def test_limit_request_size_rejects_stream_without_content_length():
    request = _RequestStub(headers={}, chunks=[b"a" * (MAX_REQUEST_SIZE + 1)])

    async def call_next(req):
        while True:
            message = await req._receive()
            if not message.get("more_body"):
                break
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 413


def test_limit_request_size_rejects_malformed_content_length_with_400():
    request = _RequestStub(headers={"content-length": "abc"}, chunks=[b"small"])

    async def call_next(req):
        # Should not be called when header is malformed.
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 400
    assert json.loads(response.body.decode("utf-8"))["detail"] == "Invalid Content-Length header"


def test_limit_request_size_rejects_negative_content_length_with_400():
    request = _RequestStub(headers={"content-length": "-1"}, chunks=[b"small"])

    async def call_next(req):
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 400
    assert json.loads(response.body.decode("utf-8"))["detail"] == "Invalid Content-Length header"


def test_limit_request_size_rejects_conflicting_chunked_and_content_length_with_400():
    request = _RequestStub(
        headers={"content-length": "10", "transfer-encoding": "chunked"},
        chunks=[b"small"],
    )

    async def call_next(req):
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 400
    assert json.loads(response.body.decode("utf-8"))["detail"] == "Invalid request framing headers"



def test_limit_request_size_checks_cumulative_stream_chunks():
    request = _RequestStub(
        headers={},
        chunks=[b"a" * (MAX_REQUEST_SIZE // 2), b"b" * (MAX_REQUEST_SIZE // 2 + 2)],
    )

    async def call_next(req):
        while True:
            message = await req._receive()
            if not message.get("more_body"):
                break
        return JSONResponse(status_code=200, content={"ok": True})

    import asyncio

    response = asyncio.run(limit_request_size_mw(request, call_next))
    assert response.status_code == 413



def test_integration_returns_413_for_large_json_body():
    app = FastAPI()
    app.middleware("http")(limit_request_size_mw)

    @app.post("/echo")
    async def echo(request: Request):
        await request.body()
        return {"ok": True}

    client = TestClient(app)
    payload = {"data": "x" * (MAX_REQUEST_SIZE + 1)}
    res = client.post("/echo", json=payload)
    assert res.status_code == 413
