import time
from datetime import datetime, timedelta, timezone

from agenthub_execution_vmm.executor import InMemorySessionManager, SessionManager
from agenthub_execution_vmm.sandbox import Sandbox
from agenthub_execution_vmm.session_store import InMemorySessionStore, SessionLease


class _SandboxStub(Sandbox):
    def __init__(self):
        self.created = []
        self.closed = []
        self.unhealthy = set()

    def run_tests(self, repo_path: str, test_command: str):
        return 0, "ok"

    def create_session(self, repo_path=None):
        sid = f"s-{len(self.created) + 1}"
        self.created.append((sid, repo_path))
        return sid

    def run_command(self, session_id: str, command: str, cwd: str = "/home/user/repo"):
        return 0, f"{session_id}:{command}"

    def close_session(self, session_id: str):
        self.closed.append(session_id)

    def is_session_alive(self, session_id: str) -> bool:
        return session_id not in self.unhealthy


def test_session_manager_alias_points_to_inmemory():
    assert issubclass(SessionManager, InMemorySessionManager)


def test_inmemory_session_manager_reuses_active_session():
    sb = _SandboxStub()
    mgr = InMemorySessionManager(sb, ttl_seconds=30)

    sid1 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    sid2 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")

    assert sid1 == sid2
    assert len(sb.created) == 1


def test_inmemory_session_manager_expires_and_recreates_session():
    sb = _SandboxStub()
    mgr = InMemorySessionManager(sb, ttl_seconds=1)

    sid1 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    assert sid1 == "s-1"

    time.sleep(1.1)

    msg = mgr.execute("agent-1", "task-1", "pytest -q")
    assert "expired" in msg.lower()
    assert "s-1" in sb.closed

    sid2 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    assert sid2 == "s-2"


def test_inmemory_session_manager_cleanup_expired_sessions():
    sb = _SandboxStub()
    mgr = InMemorySessionManager(sb, ttl_seconds=1)

    mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    mgr.get_or_create_session("agent-2", "task-2", "/tmp/repo")

    time.sleep(1.1)

    removed = mgr.cleanup_expired_sessions()
    assert removed == 2
    assert set(sb.closed) == {"s-1", "s-2"}


def test_inmemory_session_manager_reuses_store_lease_without_creating_new_session():
    sb = _SandboxStub()
    store = InMemorySessionStore()
    now = datetime.now(timezone.utc)
    store.set(
        "agent-1:task-1",
        SessionLease(
            session_id="store-session-1",
            updated_at=now,
            expires_at=now + timedelta(seconds=60),
        ),
    )

    mgr = InMemorySessionManager(sb, ttl_seconds=30, session_store=store)

    sid = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    assert sid == "store-session-1"
    assert sb.created == []


def test_inmemory_session_manager_uses_store_for_cross_instance_reuse():
    sb1 = _SandboxStub()
    sb2 = _SandboxStub()
    shared_store = InMemorySessionStore()

    mgr1 = InMemorySessionManager(sb1, ttl_seconds=60, session_store=shared_store)
    mgr2 = InMemorySessionManager(sb2, ttl_seconds=60, session_store=shared_store)

    sid1 = mgr1.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    sid2 = mgr2.get_or_create_session("agent-1", "task-1", "/tmp/repo")

    assert sid1 == "s-1"
    assert sid2 == "s-1"
    assert len(sb1.created) == 1
    assert len(sb2.created) == 0


def test_inmemory_session_manager_recreates_when_cached_session_unhealthy():
    sb = _SandboxStub()
    mgr = InMemorySessionManager(sb, ttl_seconds=60)

    sid1 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    assert sid1 == "s-1"

    sb.unhealthy.add("s-1")
    sid2 = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")

    assert sid2 == "s-2"
    assert "s-1" in sb.closed


def test_inmemory_session_manager_execute_rejects_unhealthy_session():
    sb = _SandboxStub()
    mgr = InMemorySessionManager(sb, ttl_seconds=60)

    sid = mgr.get_or_create_session("agent-1", "task-1", "/tmp/repo")
    sb.unhealthy.add(sid)

    msg = mgr.execute("agent-1", "task-1", "pytest -q")

    assert "unhealthy" in msg.lower()
    assert sid in sb.closed
