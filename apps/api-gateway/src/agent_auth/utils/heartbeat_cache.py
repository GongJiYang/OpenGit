"""
Heartbeat In-Memory Cache

Lightweight in-memory cache for heartbeat data to reduce database writes.
Batch updates are flushed to SQLite periodically by a background task.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set
from uuid import UUID


@dataclass
class HeartbeatRecord:
    """Single heartbeat record stored in memory."""
    agent_id: UUID
    timestamp: datetime
    status_message: Optional[str] = None
    count: int = 1


class HeartbeatCache:
    """
    Thread-safe in-memory cache for agent heartbeats.

    Design:
    - Heartbeats are stored in memory (Dict) instead of immediate DB writes
    - A background task flushes accumulated heartbeats to DB every N minutes
    - This significantly reduces SQLite write lock contention

    Usage:
        cache = HeartbeatCache()

        # On heartbeat receive
        cache.record(agent_id, status_message)

        # Periodic flush (called by background task)
        records = cache.get_flush_batch()
        # ... write to database ...
        cache.mark_flushed(agent_ids)
    """

    def __init__(self, max_batch_size: int = 1000):
        """
        Initialize the heartbeat cache.

        Args:
            max_batch_size: Maximum records to return in a single flush batch
        """
        self._cache: Dict[UUID, HeartbeatRecord] = {}
        self._lock = threading.RLock()
        self._max_batch_size = max_batch_size

        # Statistics
        self._total_recorded = 0
        self._total_flushed = 0

    def record(self, agent_id: UUID, status_message: Optional[str] = None) -> bool:
        """
        Record a heartbeat event.

        Args:
            agent_id: Agent UUID
            status_message: Optional status message from agent

        Returns:
            bool: True if this is a new agent in cache
        """
        with self._lock:
            now = datetime.utcnow()

            if agent_id in self._cache:
                # Update existing record
                record = self._cache[agent_id]
                record.timestamp = now
                record.status_message = status_message
                record.count += 1
                return False
            else:
                # New record
                self._cache[agent_id] = HeartbeatRecord(
                    agent_id=agent_id,
                    timestamp=now,
                    status_message=status_message,
                    count=1
                )
                self._total_recorded += 1
                return True

    def get_flush_batch(self) -> List[HeartbeatRecord]:
        """
        Get a batch of heartbeat records to flush to database.

        Returns up to max_batch_size records that need to be persisted.
        Does NOT remove them from cache until mark_flushed is called.

        Returns:
            List[HeartbeatRecord]: Records to flush
        """
        with self._lock:
            records = list(self._cache.values())[:self._max_batch_size]
            return records

    def mark_flushed(self, agent_ids: Set[UUID]) -> int:
        """
        Mark agents as flushed, removing them from cache.

        Args:
            agent_ids: Set of agent IDs that were successfully flushed

        Returns:
            int: Number of records removed from cache
        """
        with self._lock:
            removed = 0
            for aid in agent_ids:
                if aid in self._cache:
                    del self._cache[aid]
                    removed += 1
            self._total_flushed += removed
            return removed

    def get_pending_count(self) -> int:
        """Get number of pending heartbeat records in cache."""
        with self._lock:
            return len(self._cache)

    def get_stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            return {
                "pending_count": len(self._cache),
                "total_recorded": self._total_recorded,
                "total_flushed": self._total_flushed,
            }

    def clear(self) -> int:
        """
        Clear all pending records (use with caution).

        Returns:
            int: Number of records cleared
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count


# Global singleton instance
_heartbeat_cache: Optional[HeartbeatCache] = None
_cache_lock = threading.Lock()


def get_heartbeat_cache() -> HeartbeatCache:
    """
    Get the global heartbeat cache instance.

    Returns:
        HeartbeatCache: Singleton cache instance
    """
    global _heartbeat_cache
    if _heartbeat_cache is None:
        with _cache_lock:
            if _heartbeat_cache is None:
                _heartbeat_cache = HeartbeatCache()
    return _heartbeat_cache
