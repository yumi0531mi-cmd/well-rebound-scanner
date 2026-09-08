"""Render process entry point: daemon scanner plus the existing Streamlit UI."""

from __future__ import annotations

import atexit
import os
import sys

from wellscan.scanner_service import shared_runtime_components, start_daemon_service, stop_daemon_service


def main() -> int:
    # The service contains only read-only KIS discovery/data calls and paper
    # tracking.  No broker order method is imported or invoked.
    # Build the shared Cockroach/KIS/history/state object graph before either
    # the service or Streamlit imports its resource factories.
    shared_runtime_components()
    start_daemon_service()
    atexit.register(stop_daemon_service)

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
        stop_daemon_service()


if __name__ == "__main__":
    raise SystemExit(main())
