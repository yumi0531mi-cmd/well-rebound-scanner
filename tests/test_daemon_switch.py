"""WELLSCAN_DAEMON_ENABLED on/off switch for the legacy daemon."""
import start


def _run_main(monkeypatch, env_value, streamlit_cli):
    events = []
    if env_value is None:
        monkeypatch.delenv("WELLSCAN_DAEMON_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WELLSCAN_DAEMON_ENABLED", env_value)
    monkeypatch.setenv("PORT", "9877")
    monkeypatch.setattr(start, "shared_runtime_components", lambda: events.append("shared"))
    monkeypatch.setattr(start, "start_daemon_service", lambda: events.append("daemon-start"))
    monkeypatch.setattr(start, "stop_daemon_service", lambda: events.append("daemon-stop"))
    monkeypatch.setattr(start.atexit, "register", lambda cb: events.append(("registered", cb)))
    monkeypatch.setattr(streamlit_cli, "main", lambda: events.append("streamlit") or 0)
    assert start.main() == 0
    return events


def test_daemon_on_by_default(monkeypatch):
    from streamlit.web import cli as streamlit_cli
    events = _run_main(monkeypatch, None, streamlit_cli)
    assert "daemon-start" in events and "streamlit" in events


def test_daemon_off_skips_but_streamlit_runs(monkeypatch, capsys):
    from streamlit.web import cli as streamlit_cli
    for value in ("0", "false", "off", "no"):
        events = _run_main(monkeypatch, value, streamlit_cli)
        assert "daemon-start" not in events
        assert "streamlit" in events
    assert "skipped" in capsys.readouterr().out
