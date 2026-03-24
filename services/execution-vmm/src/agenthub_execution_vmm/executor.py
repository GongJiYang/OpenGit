from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, Optional

from .sandbox import Sandbox
from .session_store import InMemorySessionStore, SessionStore, build_lease


@dataclass
class _SessionRecord:
    session_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


class InMemorySessionManager:
    """
    In-process volatile session manager for local development only.

    This manager does not provide cross-worker consistency and all session
    state is lost on process restart.
    """

    DEFAULT_TTL_SECONDS = 30 * 60

    def __init__(
        self,
        sandbox: Sandbox,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        session_store: Optional[SessionStore] = None,
    ):
        self.sandbox = sandbox
        self._ttl_seconds = max(1, int(ttl_seconds))
        # key: f"{agent_id}:{task_id}", value: _SessionRecord
        self._sessions: Dict[str, _SessionRecord] = {}
        self._lock = RLock()
        self._store = session_store or InMemorySessionStore()

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _new_expiry(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._ttl_seconds)

    def _is_expired(self, record: _SessionRecord, now: datetime) -> bool:
        return record.expires_at <= now

    def _close_session_safely(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        try:
            self.sandbox.close_session(session_id)
        except Exception:
            # Best-effort close: cleanup must not block caller paths.
            pass

    def _is_session_healthy(self, session_id: Optional[str]) -> bool:
        if not session_id:
            return False
        try:
            return bool(self.sandbox.is_session_alive(session_id))
        except Exception:
            return False

    def get_or_create_session(self, agent_id: str, task_id: str, repo_path: str) -> str:
        """Retrieve an existing sandbox session or spin up a new one."""
        key = f"{agent_id}:{task_id}"

        stale_session_id: Optional[str] = None
        candidate_session_id: Optional[str] = None
        now = self._utcnow()
        with self._lock:
            record = self._sessions.get(key)
            if record and not self._is_expired(record, now):
                candidate_session_id = record.session_id
            elif record:
                stale_session_id = record.session_id
                self._sessions.pop(key, None)

        if stale_session_id:
            self._store.delete(key)
            self._close_session_safely(stale_session_id)

        if candidate_session_id and self._is_session_healthy(candidate_session_id):
            now = self._utcnow()
            with self._lock:
                fresh = self._sessions.get(key)
                if fresh and fresh.session_id == candidate_session_id and not self._is_expired(fresh, now):
                    fresh.last_seen_at = now
                    fresh.expires_at = self._new_expiry(now)
                    self._store.set(
                        key,
                        build_lease(candidate_session_id, self._ttl_seconds, now=now),
                    )
                    return candidate_session_id

        if candidate_session_id:
            self._store.delete(key)
            self._close_session_safely(candidate_session_id)
            with self._lock:
                stale = self._sessions.get(key)
                if stale and stale.session_id == candidate_session_id:
                    self._sessions.pop(key, None)

        store_lease = self._store.get(key)
        if store_lease and self._is_session_healthy(store_lease.session_id):
            now = self._utcnow()
            with self._lock:
                self._sessions[key] = _SessionRecord(
                    session_id=store_lease.session_id,
                    created_at=store_lease.updated_at,
                    last_seen_at=now,
                    expires_at=store_lease.expires_at,
                )
            self._store.set(key, build_lease(store_lease.session_id, self._ttl_seconds, now=now))
            return store_lease.session_id

        if store_lease:
            self._store.delete(key)
            self._close_session_safely(store_lease.session_id)

        print(f"🏗️ [Executor] Creating isolated drafting sandbox for {agent_id} on task {task_id}")
        created_session_id = self.sandbox.create_session(repo_path)

        now = self._utcnow()
        expires_at = self._new_expiry(now)
        stale_after_race: Optional[str] = None
        winner_session_id = created_session_id
        close_created = False

        created_lease = build_lease(created_session_id, self._ttl_seconds, now=now)
        store_won = self._store.upsert_if_absent_or_expired(key, created_lease)
        if not store_won:
            leased = self._store.get(key)
            if leased:
                winner_session_id = leased.session_id
                close_created = leased.session_id != created_session_id
                created_lease = leased

        with self._lock:
            existing = self._sessions.get(key)
            if existing and not self._is_expired(existing, now):
                existing.last_seen_at = now
                existing.expires_at = expires_at
                winner_session_id = existing.session_id
                close_created = True
            else:
                if existing:
                    stale_after_race = existing.session_id
                    self._sessions.pop(key, None)
                self._sessions[key] = _SessionRecord(
                    session_id=winner_session_id,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=created_lease.expires_at if winner_session_id != created_session_id else expires_at,
                )

        self._close_session_safely(stale_after_race)
        if close_created:
            self._close_session_safely(created_session_id)

        return winner_session_id

    def execute(self, agent_id: str, task_id: str, command: str) -> str:
        """Execute a command in the agent's dedicated session."""
        key = f"{agent_id}:{task_id}"

        expired_session_id: Optional[str] = None
        session_id: Optional[str] = None
        now = self._utcnow()
        with self._lock:
            record = self._sessions.get(key)
            if not record:
                lease = self._store.get(key)
                if lease and self._is_session_healthy(lease.session_id):
                    self._sessions[key] = _SessionRecord(
                        session_id=lease.session_id,
                        created_at=lease.updated_at,
                        last_seen_at=now,
                        expires_at=lease.expires_at,
                    )
                    record = self._sessions[key]
                elif lease:
                    self._store.delete(key)
                    self._close_session_safely(lease.session_id)

            if not record:
                return "❌ No active session found for this task. Initialize it first."

            if self._is_expired(record, now):
                expired_session_id = record.session_id
                self._sessions.pop(key, None)
            else:
                record.last_seen_at = now
                record.expires_at = self._new_expiry(now)
                session_id = record.session_id
                self._store.set(
                    key,
                    build_lease(record.session_id, self._ttl_seconds, now=now),
                )

        if expired_session_id:
            self._store.delete(key)
            self._close_session_safely(expired_session_id)
            return "❌ Session expired for this task. Initialize it first."

        if not session_id:
            return "❌ No active session found for this task. Initialize it first."

        if not self._is_session_healthy(session_id):
            self._store.delete(key)
            with self._lock:
                stale = self._sessions.get(key)
                if stale and stale.session_id == session_id:
                    self._sessions.pop(key, None)
            self._close_session_safely(session_id)
            return "❌ Session is unhealthy for this task. Initialize it first."

        exit_code, output = self.sandbox.run_command(session_id, command)
        _ = exit_code
        return output

    def close_session(self, agent_id: str, task_id: str):
        """Cleanup the sandbox for this specific task."""
        key = f"{agent_id}:{task_id}"

        session_id: Optional[str] = None
        with self._lock:
            record = self._sessions.pop(key, None)
            if record:
                session_id = record.session_id

        if session_id:
            self._store.delete(key)
        self._close_session_safely(session_id)
        if session_id:
            print(f"🧹 [Executor] Closed sandbox session for {agent_id}")

    def cleanup_expired_sessions(self) -> int:
        """Remove and close all expired sessions. Returns removed count."""
        now = self._utcnow()
        expired_ids = []
        expired_keys = []

        with self._lock:
            expired_keys = [
                key for key, record in self._sessions.items() if self._is_expired(record, now)
            ]
            for key in expired_keys:
                record = self._sessions.pop(key, None)
                if record:
                    expired_ids.append(record.session_id)

        for key in expired_keys:
            self._store.delete(key)

        for session_id in expired_ids:
            self._close_session_safely(session_id)

        return len(expired_ids)


class SessionManager(InMemorySessionManager):
    """Backward-compatible alias for the in-process session manager."""

    pass
