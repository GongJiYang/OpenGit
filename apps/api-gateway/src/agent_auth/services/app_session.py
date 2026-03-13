"""
AppSession - Unified Database Session Wrapper

Solves the "Pass-Through Variable" problem where both bounty_session
and auth_session need to be passed through multiple layers.

This wrapper provides a single interface that internally manages
both sessions, reducing cognitive load for callers.
"""

from contextlib import contextmanager
from typing import Generator, Optional
from sqlmodel import Session

from agent_auth.database import get_engine as get_auth_engine
from persistence import get_engine as get_bounty_engine


class AppSession:
    """
    Unified session wrapper for both databases.

    Usage:
        app = AppSession()
        agent = app.auth.exec(select(Agent).where(...)).first()
        bounty = app.bounty.get(Bounty, bounty_id)
        app.commit()  # Commits both sessions
    """

    def __init__(self, auth_session: Session = None, bounty_session: Session = None):
        self._auth_session = auth_session
        self._bounty_session = bounty_session
        self._owns_auth = auth_session is None
        self._owns_bounty = bounty_session is None

    @property
    def auth(self) -> Session:
        """Get auth database session (lazy initialization)."""
        if self._auth_session is None:
            self._auth_session = Session(get_auth_engine())
        return self._auth_session

    @property
    def bounty(self) -> Session:
        """Get bounty database session (lazy initialization)."""
        if self._bounty_session is None:
            self._bounty_session = Session(get_bounty_engine())
        return self._bounty_session

    def commit(self):
        """Commit both sessions."""
        if self._auth_session:
            self._auth_session.commit()
        if self._bounty_session and self._bounty_session is not self._auth_session:
            self._bounty_session.commit()

    def rollback(self):
        """Rollback both sessions."""
        if self._auth_session:
            self._auth_session.rollback()
        if self._bounty_session and self._bounty_session is not self._auth_session:
            self._bounty_session.rollback()

    def close(self):
        """Close both sessions if we own them."""
        if self._owns_auth and self._auth_session:
            self._auth_session.close()
        if self._owns_bounty and self._bounty_session:
            self._bounty_session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


@contextmanager
def get_app_session() -> Generator[AppSession, None, None]:
    """
    FastAPI dependency for unified app session.

    Usage:
        @app.get("/bounties/{id}")
        def get_bounty(id: str, app: AppSession = Depends(get_app_session)):
            return app.bounty.get(Bounty, id)
    """
    app = AppSession()
    try:
        yield app
        app.commit()
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()


# Convenience function for creating AppSession with existing sessions
def create_app_session(
    auth_session: Session = None,
    bounty_session: Session = None
) -> AppSession:
    """Create AppSession with optional existing sessions."""
    return AppSession(auth_session=auth_session, bounty_session=bounty_session)
