"""runup_app worker wiring (import-safe, no streamlit run)."""
import runup_app


def test_handlers_cover_all_jobs():
    from runup.services.jobs import JOB_NAMES
    handlers = runup_app.worker_handlers()
    assert set(handlers) == set(JOB_NAMES)
    for name, (interval, fn) in handlers.items():
        assert interval > 0 and callable(fn), name


def test_worker_disabled_by_default_no_side_effects(monkeypatch):
    from runup.services import jobs
    monkeypatch.setattr(runup_app, "worker_enabled", lambda: False)
    before = jobs._worker
    assert runup_app.maybe_start_worker() == {"state": "DISABLED"}
    assert jobs._worker is before


def test_jobs_start_honors_explicit_enabled(monkeypatch, tmp_path):
    from runup.services import jobs
    assert jobs.start(object(), enabled=False)["state"] == "DISABLED"
    assert jobs._worker is None


def test_worker_enabled_returns_bool():
    assert isinstance(runup_app.worker_enabled(), bool)
