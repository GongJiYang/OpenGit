from __future__ import annotations

from .database import get_db

# Public dependency facades for external modules (e.g., main, meta)

# Keep function names stable for interface exposure

def get_auth_session():
    """Return SQLModel Session from agent_auth database layer."""
    yield from get_db()  # delegated to internal database.get_db
