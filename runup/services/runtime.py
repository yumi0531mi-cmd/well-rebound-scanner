"""Process-wide runup runtime.

The runup app only needs a KIS read client and its SQLite path.  Keeping this
small avoids pulling the legacy scanner daemon, Cockroach store, and strategy
engine into the runup process.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import config
from runup.core.kis import KISClient


@dataclass(frozen=True)
class RunupRuntime:
    client: KISClient
    runup_db_path: str


_RUNTIME_LOCK = threading.Lock()
_RUNTIME: RunupRuntime | None = None


def shared_runtime_components() -> RunupRuntime:
    """Create the KIS client once per process."""
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = RunupRuntime(
                client=KISClient(auth_store=None, use_environment=False),
                runup_db_path=str(config.RUNUP_CONFIG["runup_db_path"]),
            )
        return _RUNTIME
