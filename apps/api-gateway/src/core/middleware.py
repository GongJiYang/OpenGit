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


class _RequestTooLargeError(Exception):
    pass


def _payload_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_SIZE / 1024 / 1024}MB."},
    )


async def limit_request_size_mw(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_SIZE:
                return _payload_too_large_response()
        except (TypeError, ValueError):
            # Ignore malformed header and enforce size via streamed body accounting.
            pass

    total_received = 0
    original_receive = request._receive

    async def limited_receive():
        nonlocal total_received
        message = await original_receive()
        if message.get("type") == "http.request":
            total_received += len(message.get("body") or b"")
            if total_received > MAX_REQUEST_SIZE:
                raise _RequestTooLargeError
        return message

    request._receive = limited_receive

    try:
        return await call_next(request)
    except _RequestTooLargeError:
        return _payload_too_large_response()


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
