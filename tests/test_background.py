from threading import Event
from time import sleep

from wellscan.background import SnapshotCoordinator


def test_coordinator_keeps_previous_snapshot_during_refresh() -> None:
    coordinator: SnapshotCoordinator[str] = SnapshotCoordinator()
    first_gate = Event()

    first = coordinator.request("US", 1, lambda: "first" if first_gate.wait(1) else "timeout")
    assert first.snapshot is None
    assert first.running
    first_gate.set()

    for _ in range(100):
        ready = coordinator.request("US", 1, lambda: "unused")
        if ready.snapshot is not None:
            break
        sleep(0.001)
    assert ready.snapshot == "first"
    assert not ready.running

    second_gate = Event()
    refreshing = coordinator.request("US", 2, lambda: "second" if second_gate.wait(1) else "timeout")
    assert refreshing.snapshot == "first"
    assert refreshing.running
    second_gate.set()


def test_coordinator_reports_loader_failure() -> None:
    coordinator: SnapshotCoordinator[str] = SnapshotCoordinator()

    coordinator.request("KR", 1, lambda: (_ for _ in ()).throw(ValueError("bad data")))
    for _ in range(100):
        state = coordinator.request("KR", 1, lambda: "unused")
        if state.error:
            break
        sleep(0.001)

    assert state.snapshot is None
    assert state.error == "ValueError: bad data"
    assert not state.running


def test_partial_snapshot_is_visible_before_batch_finishes():
    coordinator = SnapshotCoordinator()
    gate = Event()
    try:
        coordinator.request("key", 1, lambda: "complete" if gate.wait(2) else "timeout")
        coordinator.publish("key", 1, "partial")
        assert coordinator.peek("key") == "partial"
        state = coordinator.request("key", 1, lambda: "must not run")
        assert state.running and state.snapshot == "partial"
        coordinator.publish("key", 0, "stale worker")
        assert coordinator.peek("key") == "partial"
    finally:
        gate.set()


def _wait_snapshot(coordinator, key, bucket, loader):
    for _ in range(200):
        state = coordinator.request(key, bucket, loader)
        if state.snapshot is not None and not state.running:
            return state
        sleep(0.001)
    raise AssertionError("snapshot worker did not finish")


def test_pending_limit_does_not_queue_obsolete_heavy_keys():
    coordinator = SnapshotCoordinator(max_workers=1, max_keys=4, max_pending=1)
    gate = Event()
    calls = []

    coordinator.request("old-market", 1, lambda: "old" if gate.wait(2) else "timeout")
    waiting = coordinator.request("new-market", 1, lambda: calls.append("new") or "new")
    assert waiting.snapshot is None and waiting.running
    assert calls == []

    gate.set()
    _wait_snapshot(coordinator, "old-market", 1, lambda: "must-not-run")
    ready = _wait_snapshot(coordinator, "new-market", 1, lambda: calls.append("new") or "new")
    assert ready.snapshot == "new"
    assert calls == ["new"]


def test_snapshot_keys_are_lru_bounded_and_expire_deterministically():
    now = [0.0]
    coordinator = SnapshotCoordinator(max_keys=2, ttl_seconds=10, max_pending=1, clock=lambda: now[0])

    _wait_snapshot(coordinator, "a", 1, lambda: "A")
    now[0] = 1
    _wait_snapshot(coordinator, "b", 1, lambda: "B")
    now[0] = 2
    _wait_snapshot(coordinator, "c", 1, lambda: "C")

    assert coordinator.cache_size() == 2
    assert coordinator.peek("a") is None
    assert coordinator.peek("b") == "B"
    assert coordinator.peek("c") == "C"

    now[0] = 20
    assert coordinator.cache_size() == 0
