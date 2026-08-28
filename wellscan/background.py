from __future__ import annotations

from collections.abc import Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SnapshotState(Generic[T]):
    snapshot: T | None
    running: bool
    error: str | None


class SnapshotCoordinator(Generic[T]):
    """Keep the last usable snapshot while one bounded worker refreshes it."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scanner")
        self._lock = Lock()
        self._snapshots: dict[Hashable, T] = {}
        self._futures: dict[Hashable, Future[T]] = {}
        self._future_buckets: dict[Hashable, int] = {}
        self._completed_buckets: dict[Hashable, int] = {}
        self._errors: dict[Hashable, str] = {}

    def request(self, key: Hashable, bucket: int, loader: Callable[[], T]) -> SnapshotState[T]:
        with self._lock:
            future = self._futures.get(key)
            if future is not None and future.done():
                completed_bucket = self._future_buckets[key]
                try:
                    self._snapshots[key] = future.result()
                    self._completed_buckets[key] = completed_bucket
                    self._errors.pop(key, None)
                except Exception as exc:  # surfaced to the UI without discarding an older snapshot
                    self._errors[key] = f"{type(exc).__name__}: {exc}"
                    self._completed_buckets[key] = completed_bucket
                finally:
                    self._futures.pop(key, None)
                    self._future_buckets.pop(key, None)
                future = None

            if future is None and self._completed_buckets.get(key) != bucket:
                future = self._executor.submit(loader)
                self._futures[key] = future
                self._future_buckets[key] = bucket

            return SnapshotState(
                snapshot=self._snapshots.get(key),
                running=future is not None,
                error=self._errors.get(key),
            )
