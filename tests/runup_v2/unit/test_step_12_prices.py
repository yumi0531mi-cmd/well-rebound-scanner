"""V2 Step 12 — 일봉·bridge 검사 (합성 + 기존 정책 회귀)."""
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.data import prices as P
from runup.data import quote_bridge as Q
from runup.domain.base import validate

ASOF = datetime(2024, 1, 16, 21, 0, tzinfo=UTC)


def _bar(session, close="100", extra=None):
    bar = {"security_id": "s", "session_date": session,
           "source_id": "kis", "revision": 0, "open": "99", "high": "101",
           "low": "98", "close": close, "volume": 1000, "currency": "USD",
           "basis": "RAW", "available_at": "2024-01-16T21:00:00+00:00",
           "fetched_at": "2024-01-16T21:00:00+00:00", "is_final": True}
    if extra:
        bar.update(extra)
    return bar


def _provider(bars):
    def _fetch(security_id, start, end):
        assert security_id == "s"
        return [dict(b) for b in bars]
    return _fetch


def test_step12_valid_bars_and_completeness():
    bars = [_bar("2024-01-10"), _bar("2024-01-11"),
            _bar("2024-01-12", close="101")]
    result, issues = P.fetch("s", "2024-01-10", "2024-01-12", ASOF,
                             provider=_provider(bars))
    assert result.status == D.CollectionStatus.OK, issues
    assert len(result.items) == 3
    assert all(validate(b) == [] for b in result.items)
    assert result.items[2].close == Decimal("101")


def test_step12_ohlc_nan_volume_missing_dup_holiday():
    bad_ohlc = [_bar("2024-01-10", extra={"high": "90"})]
    result, issues = P.fetch("s", "2024-01-10", "2024-01-10", ASOF,
                             provider=_provider(bad_ohlc))
    assert result.status == D.CollectionStatus.PARTIAL
    assert result.items == ()
    nan_bar = [_bar("2024-01-10", extra={"close": "NaN"})]
    result, _ = P.fetch("s", "2024-01-10", "2024-01-10", ASOF,
                        provider=_provider(nan_bar))
    assert result.items == ()
    dup = [_bar("2024-01-10"), _bar("2024-01-10")]
    result, issues = P.fetch("s", "2024-01-10", "2024-01-10", ASOF,
                             provider=_provider(dup))
    assert len(result.items) == 1 and any("중복" in i for i in issues)
    holiday = [_bar("2024-01-10")]
    result, issues = P.fetch("s", "2024-01-10", "2024-01-12", ASOF,
                             provider=_provider(holiday))
    assert any("누락" in i for i in issues)


def test_step12_in_progress_and_grace_boundary():
    friday = [_bar("2024-01-12")]
    early = datetime(2024, 1, 12, 20, 30, tzinfo=UTC)
    result, issues = P.fetch("s", "2024-01-12", "2024-01-12", early,
                             provider=_provider(friday))
    assert result.items == () and any("진행봉" in i for i in issues)
    late = datetime(2024, 1, 12, 21, 30, tzinfo=UTC)
    result, _ = P.fetch("s", "2024-01-12", "2024-01-12", late,
                        provider=_provider(friday))
    assert len(result.items) == 1


def test_step12_basis_split_and_future_action():
    unknown = [_bar("2024-01-10", extra={"basis": "UNKNOWN"})]
    result, issues = P.fetch("s", "2024-01-10", "2024-01-10", ASOF,
                             provider=_provider(unknown))
    assert result.items == () and any("basis" in i for i in issues)
    adjusted = [_bar("2024-01-10", extra={"basis": "SPLIT_ADJUSTED"})]
    result, _ = P.fetch("s", "2024-01-10", "2024-01-10", ASOF,
                        provider=_provider(adjusted))
    assert result.items[0].basis == "SPLIT_ADJUSTED"
    assert result.items[0].close == Decimal("100")


def test_step12_provider_absent_and_kis_capability():
    result, _ = P.fetch("s", "2024-01-10", "2024-01-12", ASOF,
                        provider=None)
    assert result.status == D.CollectionStatus.UNSUPPORTED
    assert P.kis_daily_capability()["supported"] is False
    assert P.describe_shared_runtime()["status"] == "NEEDS_INPUT"


def test_step12_quote_received_vs_trade_and_stale():
    class Runtime:
        def __init__(self, quote):
            self.quote = quote

        def get_quote(self, security_id):
            assert security_id == "s"
            return self.quote

    fresh = {"last": Decimal("100"), "bid": Decimal("99.9"),
             "ask": Decimal("100.1"),
             "received_at": datetime(2024, 1, 16, 20, 59, 30, tzinfo=UTC),
             "trade_at": datetime(2024, 1, 16, 20, 59, 0, tzinfo=UTC),
             "time_quality": "EXCHANGE", "source_id": "kis",
             "session": "US_REGULAR", "delay_known": True}
    obs = Q.observe(Runtime(fresh), "s", ASOF, quote_max_age_seconds=120)
    assert obs.status == "FRESH"
    assert obs.trade_at != obs.received_at
    assert validate(obs) == []
    stale_quote = dict(fresh, received_at=datetime(2024, 1, 16, 20, 0,
                                                   tzinfo=UTC))
    obs = Q.observe(Runtime(stale_quote), "s", ASOF,
                    quote_max_age_seconds=120)
    assert obs.status == "STALE"
    assert obs.age_seconds > 120
    unknown = dict(fresh, received_at=None)
    obs = Q.observe(Runtime(unknown), "s", ASOF)
    assert obs.status == "UNKNOWN" and obs.age_seconds is None
    future = dict(fresh, received_at=datetime(2024, 1, 17, 12, 0,
                                              tzinfo=UTC))
    obs = Q.observe(Runtime(future), "s", ASOF,
                    future_tolerance_seconds=5)
    assert obs.status == "INVALID_FUTURE"
