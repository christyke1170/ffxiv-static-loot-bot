"""Small in-process serialization helper for SQLite's single-bot deployment mode."""

from collections import defaultdict
from contextlib import contextmanager
from threading import RLock

_guard = RLock()
_locks: dict[tuple[str, int], RLock] = defaultdict(RLock)


@contextmanager
def entity_lock(kind: str, entity_id: int):
    """Serialize writes to one entity inside a single Python process."""
    key = (kind, entity_id)
    with _guard:
        lock = _locks[key]
    with lock:
        yield
