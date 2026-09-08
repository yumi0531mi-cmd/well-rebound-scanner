from __future__ import annotations

from collections.abc import Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SnapshotState(Generic[T]):
    snapshot: T | None
    running: bool
    error: str | None


class SnapshotCoordinator(Generic[T]):
    """Keep the last usable snapshot while one bounded worker refreshes it."""

    def __init__(
        self,
        max_workers: int = 1,
        *,
        max_keys: int = 512,
        ttl_seconds: float = 900,
        max_pending: int | None = None,
        clock=monotonic,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys < 1:
            raise ValueError("max_keys must be a positive integer")
        if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        pending_limit = max_keys if max_pending is None else max_pending
        if isinstance(pending_limit, bool) or not isinstance(pending_limit, int) or pending_limit < 1:
            raise ValueError("max_pending must be a positive integer")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scanner")
        self._lock = Lock()
        self._max_keys = max_keys
        self._ttl_seconds = float(ttl_seconds)
        self._max_pending = min(pending_limit, max_keys)
        self._clock = clock
        self._snapshots: dict[Hashable, T] = {}
        self._futures: dict[Hashable, Future[T]] = {}
        self._future_buckets: dict[Hashable, int] = {}
        self._completed_buckets: dict[Hashable, int] = {}
        self._errors: dict[Hashable, str] = {}
        self._accessed: dict[Hashable, float] = {}

    def _finish_done_locked(self) -> None:
        """Consume all completed jobs, including keys the browser no longer polls."""
        for key, future in tuple(self._futures.items()):
            if not future.done():
                continue
            completed_bucket = self._future_buckets[key]
            try:
                self._snapshots[key] = future.result()
                self._completed_buckets[key] = completed_bucket
                self._errors.pop(key, None)
            except Exception as exc:  # surfaced without discarding an older snapshot
                self._errors[key] = f"{type(exc).__name__}: {exc}"
                self._completed_buckets[key] = completed_bucket
            finally:
                self._futures.pop(key, None)
                self._future_buckets.pop(key, None)

    def _drop_key_locked(self, key: Hashable) -> None:
        if key in self._futures:
            return
        self._snapshots.pop(key, None)
        self._completed_buckets.pop(key, None)
        self._errors.pop(key, None)
        self._accessed.pop(key, None)

    def _prune_locked(self, now: float, protected: Hashable | None = None) -> None:
        for key, accessed in tuple(self._accessed.items()):
            if key != protected and key not in self._futures and now - accessed >= self._ttl_seconds:
                self._drop_key_locked(key)
        while len(self._accessed) > self._max_keys:
            removable = [(accessed, key) for key, accessed in self._accessed.items()
                         if key != protected and key not in self._futures]
            if not removable:
                break
            _, oldest = min(removable, key=lambda item: item[0])
            self._drop_key_locked(oldest)

    def publish(self, key: Hashable, bucket: int, snapshot: T) -> None:
        """Publish incremental worker output without declaring the batch finished."""
        with self._lock:
            now = self._clock()
            self._finish_done_locked()
            if self._future_buckets.get(key) == bucket:
                self._snapshots[key] = snapshot
                self._accessed[key] = now
            self._prune_locked(now, key)

    def peek(self, key: Hashable) -> T | None:
        with self._lock:
            now = self._clock()
            self._finish_done_locked()
            self._prune_locked(now, key)
            if key in self._snapshots:
                self._accessed[key] = now
            return self._snapshots.get(key)

    def request(self, key: Hashable, bucket: int, loader: Callable[[], T]) -> SnapshotState[T]:
        with self._lock:
            now = self._clock()
            self._finish_done_locked()
            self._prune_locked(now, key)
            self._accessed[key] = now
            future = self._futures.get(key)
            if future is None and self._completed_buckets.get(key) != bucket and len(self._futures) < self._max_pending:
                future = self._executor.submit(loader)
                self._futures[key] = future
                self._future_buckets[key] = bucket
            self._prune_locked(now, key)

            return SnapshotState(
                snapshot=self._snapshots.get(key),
                running=future is not None or bool(self._futures),
                error=self._errors.get(key),
            )

    def cache_size(self) -> int:
        """Deterministic diagnostic used by regression tests and health reporting."""
        with self._lock:
            now = self._clock()
            self._finish_done_locked()
            self._prune_locked(now)
            return len(self._accessed)
