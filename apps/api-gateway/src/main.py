try:
    # Package import path (used by uvicorn src.main:create_app --factory)
    from .app_factory import app, create_app
except ImportError:  # pragma: no cover
    # Script import path fallback
    from app_factory import app, create_app

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:create_app", factory=True, host="0.0.0.0", port=8000)
