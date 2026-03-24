import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# --- Rate Limiting ---
limiter = Limiter(key_func=get_remote_address)

# --- Request Size Limit (20MB) ---
MAX_REQUEST_SIZE = 20 * 1024 * 1024  # 20MB


def _payload_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_SIZE / 1024 / 1024}MB."},
    )


async def limit_request_size_mw(request: Request, call_next):
    content_length = request.headers.get("content-length")
    transfer_encoding = (request.headers.get("transfer-encoding") or "").lower()

    if content_length and "chunked" in transfer_encoding:
        return JSONResponse(status_code=400, content={"detail": "Invalid request framing headers"})

    if content_length:
        try:
            parsed_length = int(content_length)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})

        if parsed_length < 0:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})

        if parsed_length > MAX_REQUEST_SIZE:
            return _payload_too_large_response()

    total_received = 0
    original_receive = request._receive
    buffered_messages = []

    while True:
        message = await original_receive()
        message_type = message.get("type")

        if message_type == "http.request":
            body = message.get("body") or b""
            total_received += len(body)
            if total_received > MAX_REQUEST_SIZE:
                return _payload_too_large_response()

            buffered_messages.append(
                {
                    "type": "http.request",
                    "body": body,
                    "more_body": bool(message.get("more_body", False)),
                }
            )
            if not message.get("more_body", False):
                break
            continue

        buffered_messages.append(message)
        if message_type == "http.disconnect":
            break

    message_iter = iter(buffered_messages)

    async def replay_receive():
        try:
            return next(message_iter)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    request._receive = replay_receive

    return await call_next(request)


async def tracing_mw(request: Request, call_next):
    """Simple tracing middleware to attach X-Trace-Id."""
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


def setup_rate_limit_and_middlewares(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.middleware("http")(limit_request_size_mw)
    app.middleware("http")(tracing_mw)
