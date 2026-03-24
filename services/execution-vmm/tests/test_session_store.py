from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agenthub_execution_vmm.session_store import RedisSessionStore, build_lease


class _PipelineStub:
    def __init__(self, data: dict[str, str], key: str):
        self._data = data
        self._key = key

    def get(self, key):
        assert key == self._key
        return self

    def delete(self, key):
        assert key == self._key
        return self

    def execute(self):
        raw = self._data.get(self._key)
        self._data.pop(self._key, None)
        return [raw, 1 if raw else 0]


class _RedisStub:
    def __init__(self):
        self._data = {}
        self._ttl = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = value
        self._ttl[key] = ex
        return True

    def delete(self, key):
        existed = key in self._data
        self._data.pop(key, None)
        self._ttl.pop(key, None)
        return 1 if existed else 0

    def pipeline(self, transaction=True):
        assert transaction is True
        if self._data:
            key = next(iter(self._data.keys()))
        else:
            key = ""
        return _PipelineStub(self._data, key)


def _patch_redis(monkeypatch, redis_stub):
    import sys

    factory = SimpleNamespace(from_url=lambda url, decode_responses=True: redis_stub)
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=factory))


def test_build_lease_uses_positive_ttl():
    now = datetime.now(timezone.utc)
    lease = build_lease("s1", ttl_seconds=30, now=now)
    assert lease.session_id == "s1"
    assert lease.updated_at == now
    assert lease.expires_at == now + timedelta(seconds=30)


def test_redis_session_store_roundtrip(monkeypatch):
    redis_stub = _RedisStub()
    _patch_redis(monkeypatch, redis_stub)

    store = RedisSessionStore("redis://test:6379/0")
    lease = build_lease("sid-1", ttl_seconds=60)

    assert store.upsert_if_absent_or_expired("a:t", lease) is True

    loaded = store.get("a:t")
    assert loaded is not None
    assert loaded.session_id == "sid-1"


def test_redis_session_store_rejects_active_existing_lease(monkeypatch):
    redis_stub = _RedisStub()
    _patch_redis(monkeypatch, redis_stub)

    store = RedisSessionStore("redis://test:6379/0")
    lease1 = build_lease("sid-1", ttl_seconds=60)
    lease2 = build_lease("sid-2", ttl_seconds=60)

    assert store.upsert_if_absent_or_expired("a:t", lease1) is True
    assert store.upsert_if_absent_or_expired("a:t", lease2) is False

    loaded = store.get("a:t")
    assert loaded is not None
    assert loaded.session_id == "sid-1"


def test_redis_session_store_delete_returns_previous_lease(monkeypatch):
    redis_stub = _RedisStub()
    _patch_redis(monkeypatch, redis_stub)

    store = RedisSessionStore("redis://test:6379/0")
    lease = build_lease("sid-del", ttl_seconds=60)
    store.set("a:t", lease)

    deleted = store.delete("a:t")
    assert deleted is not None
    assert deleted.session_id == "sid-del"
    assert store.get("a:t") is None


def test_redis_session_store_requires_redis_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("no redis")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    with pytest.raises(RuntimeError, match="redis package is required"):
        RedisSessionStore("redis://test:6379/0")
