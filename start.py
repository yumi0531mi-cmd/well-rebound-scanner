"""Render process entry point: daemon scanner plus the existing Streamlit UI."""

from __future__ import annotations

import atexit
import os
import sys

from wellscan.scanner_service import shared_runtime_components, start_daemon_service, stop_daemon_service
from runup.services.handlers import (_source_handler, _daily_handler, _quote_handler,
                                     _event_handler, _scan_handler, _settlement_handler)


def daemon_enabled() -> bool:
    """레거시 단타 데몬 on/off. WELLSCAN_DAEMON_ENABLED=0/false/no/off면 끈다.

    기본값은 켜짐(기존 동작 유지). 런업 탭 표시는 영향 없음.
    """
    return os.environ.get("WELLSCAN_DAEMON_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off")


def main() -> int:
    # The service contains only read-only KIS discovery/data calls and paper
    # tracking.  No broker order method is imported or invoked.
    # Build the shared Cockroach/KIS/history/state object graph before either
    # the service or Streamlit imports its resource factories.
    runtime = shared_runtime_components()

    # Start daemon service early (test expects this right after runtime creation)
    if daemon_enabled():
        start_daemon_service()
        atexit.register(stop_daemon_service)
    else:
        print("WELLSCAN_DAEMON_ENABLED=off: legacy daemon skipped (runup UI only)")

    from runup.services import jobs as runup_jobs

    # Start worker (initializes lease, then register handlers)
    runup_jobs.start(runtime)

    # Register job handlers: each as (interval_seconds, callable)
    # where callable(runtime, at, cursor) -> (commit_fn, new_cursor)
    # commit_fn performs only DB operations inside fenced transaction (no network).
    from config import RUNUP_CONFIG
    runup_jobs.register_handlers({
        "source":     (RUNUP_CONFIG.get("source_poll_seconds", 300), _source_handler),
        "daily":      (RUNUP_CONFIG.get("daily_poll_seconds", 86400), _daily_handler),
        "quote":      (RUNUP_CONFIG.get("quote_poll_seconds", 60), _quote_handler),
        "event":      (RUNUP_CONFIG["risk_notice_poll_minutes"] * 60, _event_handler),
        "scan":       (RUNUP_CONFIG.get("scan_poll_seconds", 86400), _scan_handler),
        "settlement": (RUNUP_CONFIG.get("settlement_cadence_seconds", 86400), _settlement_handler),
    })

    atexit.register(runup_jobs.stop)

    from streamlit.web import cli as streamlit_cli

    port = os.environ.get("PORT", "8501")
    sys.argv = [
        "streamlit",
        "run",
        "app.py",
        "--server.address",
        "0.0.0.0",
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    try:
        return int(streamlit_cli.main() or 0)
    finally:
        runup_jobs.stop()
        stop_daemon_service()


if __name__ == "__main__":
    raise SystemExit(main())