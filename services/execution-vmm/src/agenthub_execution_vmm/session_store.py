from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol


@dataclass
class SessionLease:
    session_id: str
    updated_at: datetime
    expires_at: datetime


class SessionStore(Protocol):
    def get(self, key: str) -> Optional[SessionLease]:
        ...

    def set(self, key: str, lease: SessionLease) -> None:
        ...

    def upsert_if_absent_or_expired(self, key: str, lease: SessionLease) -> bool:
        ...

    def delete(self, key: str) -> Optional[SessionLease]:
        ...


class InMemorySessionStore:
    """Simple in-process store for local/dev usage."""

    def __init__(self):
        self._data: dict[str, SessionLease] = {}

    def get(self, key: str) -> Optional[SessionLease]:
        lease = self._data.get(key)
        if not lease:
            return None
        if lease.expires_at <= datetime.now(timezone.utc):
            self._data.pop(key, None)
            return None
        return lease

    def set(self, key: str, lease: SessionLease) -> None:
        self._data[key] = lease

    def upsert_if_absent_or_expired(self, key: str, lease: SessionLease) -> bool:
        existing = self._data.get(key)
        if existing and existing.expires_at > datetime.now(timezone.utc):
            return False
        self._data[key] = lease
        return True

    def delete(self, key: str) -> Optional[SessionLease]:
        return self._data.pop(key, None)


class RedisSessionStore:
    """Redis-backed session lease store for cross-worker consistency."""

    def __init__(self, redis_url: str, key_prefix: str = "agenthub:session"):
        if not redis_url:
            raise ValueError("redis_url is required")
        try:
            import redis
        except Exception as exc:  # pragma: no cover - exercised via unit monkeypatch
            raise RuntimeError("redis package is required for RedisSessionStore") from exc

        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix.strip() or "agenthub:session"

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def _serialize(self, lease: SessionLease) -> str:
        return json.dumps(
            {
                "session_id": lease.session_id,
                "updated_at": lease.updated_at.isoformat(),
                "expires_at": lease.expires_at.isoformat(),
            },
            separators=(",", ":"),
        )

    def _deserialize(self, payload: str) -> Optional[SessionLease]:
        if not payload:
            return None
        data = json.loads(payload)
        return SessionLease(
            session_id=str(data["session_id"]),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
        )

    def get(self, key: str) -> Optional[SessionLease]:
        raw = self._redis.get(self._redis_key(key))
        if not raw:
            return None
        lease = self._deserialize(raw)
        if not lease:
            return None
        if lease.expires_at <= datetime.now(timezone.utc):
            self._redis.delete(self._redis_key(key))
            return None
        return lease

    def set(self, key: str, lease: SessionLease) -> None:
        redis_key = self._redis_key(key)
        ttl = max(1, int((lease.expires_at - datetime.now(timezone.utc)).total_seconds()))
        self._redis.set(redis_key, self._serialize(lease), ex=ttl)

    def upsert_if_absent_or_expired(self, key: str, lease: SessionLease) -> bool:
        redis_key = self._redis_key(key)
        existing_raw = self._redis.get(redis_key)
        if existing_raw:
            existing = self._deserialize(existing_raw)
            if existing and existing.expires_at > datetime.now(timezone.utc):
                return False

        self.set(key, lease)
        return True

    def delete(self, key: str) -> Optional[SessionLease]:
        redis_key = self._redis_key(key)
        pipe = self._redis.pipeline(transaction=True)
        pipe.get(redis_key)
        pipe.delete(redis_key)
        raw, _ = pipe.execute()
        return self._deserialize(raw) if raw else None


def build_lease(session_id: str, ttl_seconds: int, now: Optional[datetime] = None) -> SessionLease:
    now = now or datetime.now(timezone.utc)
    ttl = max(1, int(ttl_seconds))
    return SessionLease(
        session_id=session_id,
        updated_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
