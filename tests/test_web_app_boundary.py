from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from streamlit.testing.v1 import AppTest

from wellscan.kis import KISError
from wellscan.models import Candidate, Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.sessions import SessionStatus


def admin_app() -> AppTest:
    app = AppTest.from_file("app.py")
    app.query_params["admin"] = "backtest"
    return app


def test_public_admin_query_is_disabled_without_server_secret(monkeypatch):
    monkeypatch.delenv("WELLSCAN_ADMIN_TOKEN", raising=False)
    app = admin_app().run(timeout=20)

    assert not app.exception
    assert len(app.text_input) == 0
    assert any("비활성" in item.value for item in app.error)


def test_admin_query_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", "a-deterministic-admin-secret")
    app = admin_app().run(timeout=20)
    app.text_input[0].input("wrong-secret")
    app.button[0].click()
    app.run(timeout=20)

    assert not app.exception
    assert len(app.text_input) == 1
    assert any("올바르지" in item.value for item in app.error)


def test_admin_query_accepts_matching_server_secret(monkeypatch):
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", "a-deterministic-admin-secret")
    app = admin_app().run(timeout=20)
    app.text_input[0].input("a-deterministic-admin-secret")
    app.button[0].click()
    app.run(timeout=20)

    assert not app.exception
    assert len(app.text_input) == 0
    assert any("백테스트 관리" in item.value for item in app.title)


def test_web_resource_factories_use_the_process_shared_runtime_graph():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = {
        "client": "client",
        "history": "history",
        "sequences": "sequences",
        "validations": "validations",
    }
    for function_name, attribute_name in expected.items():
        returns = [node for node in ast.walk(functions[function_name]) if isinstance(node, ast.Return)]
        assert len(returns) == 1
        value = returns[0].value
        assert isinstance(value, ast.Attribute) and value.attr == attribute_name
        assert isinstance(value.value, ast.Call)
        assert isinstance(value.value.func, ast.Name)
        assert value.value.func.id == "shared_runtime_components"


def test_running_daemon_feed_prevents_a_second_browser_heavy_scan(monkeypatch):
    import wellscan.quotes as quote_module
    import wellscan.realtime as realtime_module
    import wellscan.scanner_service as scanner_service
    import wellscan.sessions as sessions

    class FakeClient:
        configured = True
        candidate_calls = 0

        def candidate_union(self, _limit):
            self.candidate_calls += 1
            raise AssertionError("the browser must not start a competing daemon scan")

    class FakeHistory:
        def persistence_status(self):
            return SimpleNamespace(configured=False, available=False, last_error="DATABASE_URL 미설정")

    class FakeRealtime:
        instances = []

        def __init__(self, _client):
            self.connected = False
            self.last_error = ""
            self.configured = []
            self.instances.append(self)

        def configure(self, candidates):
            self.configured.append(tuple(candidates))

        def metrics(self):
            return {
                "connection_attempts": 0,
                "reconnects": 0,
                "received_ticks": 0,
                "subscriptions": 0,
                "connected": False,
                "last_error": "",
            }

        def tick(self, _candidate):
            return None

    class FakeQuotes:
        def __init__(self, _client, _hub):
            pass

        def get(self, _candidate):
            raise KISError("deterministic quote unavailable")

    class FakeValidations:
        def daily_cases(self, *_args, **_kwargs):
            return []

        def session_progress(self, *_args, **_kwargs):
            return {"unique_symbols": 0, "signals": 0, "entries": 0, "pending": 0}

        def tracking_cases(self):
            return []

        def retry_pending_durable(self):
            return 0

        def expire_unobserved(self, *_args, **_kwargs):
            return 0

    fake_client = FakeClient()
    runtime = SimpleNamespace(
        client=fake_client,
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidations(),
    )
    now = datetime.now(UTC)
    candidate = Candidate(
        "005930",
        "삼성전자",
        50000.0,
        1.0,
        1000.0,
        50_000_000.0,
        market=Market.KR,
        exchange="KRX",
        session=TradingSession.KR_REGULAR,
    )
    result = ScanResult(
        symbol=candidate.key,
        evaluated_at=now,
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_CONTINUATION,
        risk_state=RiskState.NORMAL,
        score=90,
        persistence=80.0,
        evidence_confidence=80.0,
        pattern_fatigue=0.0,
        net_swing_pct=2.0,
        levels=TradeLevels(
            entry=50000.0,
            target1=51000.0,
            target2=52000.0,
            soft_stop=49500.0,
            hard_stop=49250.0,
            structural_stop=49000.0,
            entry_eta_minutes=1,
            target1_eta_minutes=10,
            target2_eta_minutes=20,
        ),
        conditions={"FINAL_BUY": True},
        trend_label="상승",
        matched_strategies=(Strategy.TREND_CONTINUATION, Strategy.BREAKOUT),
        diagnostics={"completed_bar_at": now.isoformat(), "net_rr_target1": 1.1},
    )
    service_status = SimpleNamespace(
        running=True,
        last_cycle_started_at=now.isoformat(),
        last_cycle_elapsed_seconds=0.25,
        recent_errors=(),
    )
    session_feed = SimpleNamespace(
        session=TradingSession.KR_REGULAR,
        day=now.astimezone().date().isoformat(),
        updated_at=now.isoformat(),
        results=((candidate, result),),
    )
    result_feed = SimpleNamespace(updated_at=now.isoformat(), sessions=(session_feed,), recent_errors=())
    monkeypatch.setattr(scanner_service, "shared_runtime_components", lambda: runtime)
    monkeypatch.setattr(scanner_service, "daemon_service_status", lambda: service_status)
    monkeypatch.setattr(scanner_service, "daemon_results_snapshot", lambda: result_feed)
    monkeypatch.setattr(realtime_module, "RealtimeHub", FakeRealtime)
    monkeypatch.setattr(quote_module, "QuoteBook", FakeQuotes)
    monkeypatch.setattr(
        sessions,
        "session_status",
        lambda market, now=None: SessionStatus(Market(market), TradingSession.KR_REGULAR, True, "국내 정규장"),
    )
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    try:
        app = AppTest.from_file("app.py").run(timeout=20)
        assert not app.exception
        assert fake_client.candidate_calls == 0
        assert any("상시 스캐너 실행 중" in item.value for item in app.sidebar.success)
        assert FakeRealtime.instances[0].configured[0][0].symbol == "005930"
        rendered = "\n".join(item.value for item in app.markdown)
        for label in ("적용기법", "현재가 미수신", "진입가", "구조 STOP", "최대 Hard Stop", "T1", "T2", "진입 ETA", "구조 기준 완료봉", "현재가 수신"):
            assert label in rendered
        assert "지금 매수 금지" in rendered
        assert any("9개 활성 매매기법 전체의 실제 ENTRY를 합산" in item.value for item in app.caption)
        assert any("아직 기록 없음" in item.value for item in app.info)
    finally:
        st.cache_resource.clear()
        st.cache_data.clear()


def test_tracking_history_is_an_always_visible_numbered_list_of_all_daily_cases():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    render_source = ast.get_source_segment(source, functions["render_current_session_tracking"])
    history_source = ast.get_source_segment(source, functions["render_tracking_history"])
    html_source = ast.get_source_segment(source, functions["_tracking_history_html"])

    assert render_source is not None and "render_tracking_history(market_daily)" in render_source
    assert "st.expander" not in render_source
    assert history_source is not None and "아직 기록 없음" in history_source
    assert html_source is not None and '<ol class="tracking-history">' in html_source and "<li>" in html_source
    assert "verified_execution_contract" not in html_source
    assert "market_daily = validations().daily_cases(None, market.value)" in render_source
