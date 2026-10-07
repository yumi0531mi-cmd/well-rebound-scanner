"""V2 Step 15 — SETUP 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime

import config
import runup.domain as D
from runup.domain.base import validate
from runup.engine import setup as S

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
SESSIONS = [f"2024-01-{day:02d}" for day in range(1, 32)]
BASE_VALUES = dict(config.RUNUP_CONFIG,
                   config_hash=config.runup_config_hash())


def _features(**over):
    def _v(name, value):
        return D.FeatureValue(name=name, status=D.FeatureStatus.VALID,
                              value=value, reason="")

    parts = {"dryup": 0.4, "ma_spread": 0.03, "close": 105.0,
             "sma20": 100.0, "ma20_cross_age": 3, "slope20": 0.001,
             "slow_k": 30.0, "slow_k_prev": 20.0}
    parts.update(over)
    return D.FeatureSnapshot(
        feature_id="f1", security_id="s1", as_of=T0,
        feature_version="runup-features-v2", bar_hash="b",
        config_hash=BASE_VALUES["config_hash"],
        components=tuple(_v(k, v) for k, v in parts.items()), issues=())


def _market(session="2024-01-10", at=None, **over):
    clock = D.EvaluationClock(
        as_of=at or T0, session_date=session, calendar_version="v1")
    security = D.Security(
        security_id="s1", issuer_id="i1", ticker="NVX", exchange="NASDAQ",
        currency="USD", equity_type="COMMON", listing_status="LISTED",
        valid_from=T0)
    issuer = D.Issuer(issuer_id="i1", legal_name="Nova", sector_tags=["BIO"],
                      valid_from=T0, mapping_evidence=["sec-ciK"])
    catalyst = D.CatalystRevision(
        event_id="e1", revision_id="r1", revision_seq=0,
        event_type="READOUT_CANDIDATE",
        date_precision=D.DatePrecision.EXACT_DATE, available_at=T0,
        status="APPROVED", risk_class="BINARY", mapping_status="VERIFIED",
        importance_class="confirmed", issuer_ids=("i1",),
        security_ids=("s1",), start="2024-03-01")
    health = D.SourceHealthSnapshot(
        source_id="sec", scope="filings", capability="SUPPORTED_MANUAL",
        status="OK", revision=1)
    base = dict(clock=clock, security_id="s1", security=security,
                issuer=issuer, catalysts=(catalyst,),
                source_health=(health,), importance=75)
    base.update(over)
    return D.MarketContext(**base)


def _pivots(hl=True):
    return D.PivotSet(
        reference_high_known_previous_session="2024-01-09",
        reference_low="90",
        higher_low=D.FeatureValue(
            name="higher_low", status=D.FeatureStatus.VALID,
            value=1.0 if hl else 0.0, reason=""))


def _config(values=None):
    snapshot_values = dict(BASE_VALUES)
    if values:
        snapshot_values.update(values)
    return D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="p1",
                            config_hash=BASE_VALUES["config_hash"],
                            values=snapshot_values)


def _context(features=None, market=None, pivots=None, previous=None,
             values=None):
    return D.SetupContext(
        market=market or _market(),
        config=_config(values),
        features=features or _features(),
        pivots=pivots or _pivots(), previous_setup=previous)


def _later_context(session, day, features=None, previous=None, market=None):
    at = datetime(2024, 1, day, 12, 0, tzinfo=UTC)
    return _context(features=features, previous=previous,
                    market=market or _market(session=session, at=at))


def test_step15_score_85_threshold_equal_weight0_missing():
    weights = BASE_VALUES["setup_weights"]
    flags = {"dryup": True, "squeeze": True, "ma20_recovery": True,
             "positive_slope": False, "higher_low": True,
             "stoch_turn": False}
    score, _ = S.score_setup(flags, weights)
    assert score == 85.0
    equal = {k: True for k in flags}
    score, _ = S.score_setup(equal, dict(weights, stoch_turn=0))
    assert score == 100.0
    assert S.score_setup(dict(flags, dryup=None), weights)[0] is None
    assert S.score_setup(flags, {k: 0 for k in flags})[0] is None


def test_step15_qualify_stable_generation_and_unavailable():
    first = S.evaluate(_context(), SESSIONS)
    assert first.state == D.SetupState.SETUP
    assert first.snapshot.qualified is True
    second = S.evaluate(_context(), SESSIONS)
    assert second.snapshot.generation_id == first.snapshot.generation_id
    assert "dryup@2024-01-10" in first.snapshot.reasons
    assert validate(first.snapshot) == []
    assert S.evaluate(
        _context(features=_features(dryup=None)), SESSIONS).state == \
        D.SetupState.UNAVAILABLE


def test_step15_gate_failures_are_watch():
    assert S.evaluate(
        _context(market=_market(security=None)), SESSIONS).state == \
        D.SetupState.WATCH
    assert S.evaluate(
        _context(market=_market(importance=10)), SESSIONS).state == \
        D.SetupState.WATCH
    bad_catalyst = D.CatalystRevision(
        event_id="e1", revision_id="r1", revision_seq=0,
        event_type="FILING_OBSERVED",
        date_precision=D.DatePrecision.UNKNOWN, available_at=T0,
        status="PENDING", risk_class="", mapping_status="",
        importance_class="unreviewed")
    assert S.evaluate(
        _context(market=_market(catalysts=(bad_catalyst,))), SESSIONS
    ).state == D.SetupState.WATCH


def test_step15_latch_expiry_and_invalidations():
    first = S.evaluate(_context(), SESSIONS)
    assert first.state == D.SetupState.SETUP
    previous = first.snapshot
    assert previous.expires_session == "2024-01-20"
    weak = _features(dryup=0.9, slope20=-0.5)
    held = S.evaluate(
        _later_context("2024-01-15", 15, features=weak,
                       previous=previous), SESSIONS)
    assert held.state == D.SetupState.SETUP
    assert held.generation_id == previous.generation_id
    assert held.expires_session == previous.expires_session
    expired = S.evaluate(
        _later_context("2024-01-21", 21, features=weak,
                       previous=previous), SESSIONS)
    assert expired.state == D.SetupState.WATCH
    assert "만료" in expired.reasons[0]
    broken = S.evaluate(
        _later_context("2024-01-15", 15,
                       features=_features(dryup=0.9, slope20=-0.5,
                                          close=80.0),
                       previous=previous), SESSIONS)
    assert broken.state == D.SetupState.INVALID
    risky = D.RiskNotice(
        notice_id="n1", security_id="s1", reason="halt",
        severity=D.Severity.CRITICAL, available_at=datetime(
            2024, 1, 12, 12, 0, tzinfo=UTC), review_status="PENDING")
    invalid = S.evaluate(
        _later_context("2024-01-15", 15, features=weak, previous=previous,
                       market=_market(
                           session="2024-01-15",
                           at=datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
                           risk_notices=(risky,))), SESSIONS)
    assert invalid.state == D.SetupState.INVALID
    other_event = D.CatalystRevision(
        event_id="e2", revision_id="r2", revision_seq=0,
        event_type="READOUT_CANDIDATE",
        date_precision=D.DatePrecision.EXACT_DATE, available_at=T0,
        status="APPROVED", risk_class="BINARY", mapping_status="VERIFIED",
        importance_class="confirmed", issuer_ids=("i1",),
        security_ids=("s1",), start="2024-03-05")
    changed = S.evaluate(
        _later_context("2024-01-15", 15, features=weak, previous=previous,
                       market=_market(
                           session="2024-01-15",
                           at=datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
                           catalysts=(other_event,))), SESSIONS)
    assert changed.state == D.SetupState.INVALID


def test_step15_low_score_is_watch_not_invalid():
    result = S.evaluate(
        _context(features=_features(dryup=0.9, slope20=-0.5)), SESSIONS)
    assert result.state == D.SetupState.WATCH
