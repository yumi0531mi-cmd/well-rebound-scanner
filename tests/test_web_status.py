from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from wellscan.models import Candidate, Market, Stage, TradingSession
from wellscan.web_status import (
    PipelineIssueCollector,
    PipelineStage,
    admin_session_fingerprint,
    admin_session_valid,
    admin_token_configured,
    admin_token_valid,
    candidate_matches_filter,
    prioritize_realtime_candidates,
    safe_pipeline_message,
    shared_scan_key,
    tracked_candidate_for_case,
    tracking_refresh_scope,
)


def candidate(price=100.0, change=3.0, symbol="TEST"):
    return Candidate(symbol, "테스트", price, change, 1000, 100000)


def test_admin_page_is_disabled_without_a_strong_server_secret():
    assert not admin_token_configured(None)
    assert not admin_token_configured("short")
    assert not admin_token_valid(None, None)
    assert not admin_token_valid("short", "short")
    assert admin_session_fingerprint(None) is None


def test_admin_grant_is_bound_to_the_current_secret():
    secret = "a-deterministic-admin-secret"
    replacement = "a-different-admin-secret---"

    assert admin_token_valid(secret, secret)
    assert not admin_token_valid(secret, secret + "x")
    fingerprint = admin_session_fingerprint(secret)
    assert admin_session_valid(secret, fingerprint)
    assert not admin_session_valid(replacement, fingerprint)


def test_pipeline_issues_are_redacted_deduplicated_and_bounded():
    collector = PipelineIssueCollector(limit=2)
    detail = "postgresql://scanner:plain-password@db.example/defaultdb password=hidden"
    collector.add(PipelineStage.HISTORY, "DB_READ", "KR:TEST", detail)
    collector.add(PipelineStage.HISTORY, "DB_READ", "KR:TEST", detail)
    collector.add(PipelineStage.QUOTE, "QUOTE", "US:AAA", "timeout")
    collector.add(PipelineStage.ENGINE, "ENGINE", "US:BBB", "bad input")

    issues = collector.snapshot()
    assert len(issues) == 2
    assert collector.dropped == 1
    assert "plain-password" not in issues[0].message
    assert "hidden" not in issues[0].message
    assert "postgresql://***@db.example" in issues[0].message
    assert issues[0].row()["구간"] == "분봉"


def test_pipeline_messages_are_single_line_and_bounded():
    message = safe_pipeline_message("first\nsecond " + "x" * 300)
    assert "\n" not in message
    assert len(message) == 240


@pytest.mark.parametrize(
    ("mode", "change", "expected"),
    [("전체", math.nan, True), ("일반주", 0, True), ("일반주", 7, True),
     ("일반주", math.nan, False), ("급등주", 7.01, True), ("급등주", 20, True), ("급등주", 21, False)],
)
def test_display_filter_is_independent_of_the_heavy_scan(mode, change, expected):
    assert candidate_matches_filter(candidate(change=change), mode, 10, 200) is expected


def test_display_filter_rejects_invalid_price_or_configuration():
    assert not candidate_matches_filter(candidate(price=math.nan), "전체", 10, 200)
    assert not candidate_matches_filter(candidate(price=5), "전체", 10, 200)
    with pytest.raises(ValueError, match="mode"):
        candidate_matches_filter(candidate(), "unknown", 10, 200)
    with pytest.raises(ValueError, match="price"):
        candidate_matches_filter(candidate(), "전체", 200, 10)


def test_heavy_scan_key_cannot_vary_with_display_filters():
    key = shared_scan_key(Market.US, TradingSession.US_PRE)
    assert key == ("US", "US_PRE")
    assert key == shared_scan_key(Market.US, TradingSession.US_PRE)


def test_session_transition_stops_quote_but_keeps_closed_bar_replay():
    transition = tracking_refresh_scope(TradingSession.US_DAY, TradingSession.US_PRE, True)
    assert transition.observe_live_quote is False
    assert transition.replay_completed_bars is True

    closed = tracking_refresh_scope(TradingSession.US_REGULAR, TradingSession.CLOSED, False)
    assert closed.observe_live_quote is False
    assert closed.replay_completed_bars is True

    current = tracking_refresh_scope(TradingSession.US_PRE, TradingSession.US_PRE, True)
    assert current.observe_live_quote is True
    assert current.replay_completed_bars is True


def test_tracked_candidate_missing_price_stays_unknown_not_zero():
    recovered = tracked_candidate_for_case("US:NAS:US_PRE:AAPL", None, {})
    assert recovered is not None
    assert math.isnan(recovered.price)
    assert recovered.price != 0
    assert recovered.session == TradingSession.US_PRE


def test_tracked_candidate_reuses_current_market_candidate():
    visible = candidate(price=123.0)
    recovered = tracked_candidate_for_case(visible.key, None, {visible.key: visible})
    assert recovered is visible


def test_realtime_candidates_are_bounded_deduplicated_and_actionable_first():
    watch = candidate(symbol="WATCH")
    waiting = candidate(symbol="WAIT")
    buy = candidate(symbol="BUY")
    results = [
        (watch, SimpleNamespace(stage=Stage.CANDIDATE, score=99)),
        (waiting, SimpleNamespace(stage=Stage.ENTRY_WAIT, score=10)),
        (buy, SimpleNamespace(stage=Stage.FINAL_BUY, score=1)),
        (buy, SimpleNamespace(stage=Stage.CANDIDATE, score=100)),
    ]
    selected = prioritize_realtime_candidates(results, limit=2)
    assert [item.symbol for item in selected] == ["BUY", "WAIT"]
    with pytest.raises(ValueError):
        prioritize_realtime_candidates(results, limit=0)
