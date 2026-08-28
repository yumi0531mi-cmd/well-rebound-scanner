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
