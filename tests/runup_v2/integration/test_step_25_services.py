"""Immutable profile, deterministic scan and risk-only refresh."""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import config
import runup.domain as D
from runup.data.calendar import get_calendar
from runup.services import config_service, read_models, scan
from runup.storage import connect, migrate

T = datetime(2024, 6, 28, 22, tzinfo=UTC)


def db(tmp_path):
    conn = connect(tmp_path/"s.db")
    migrate(conn)
    profile = config_service.save(conn, dict(config.RUNUP_CONFIG), "TEST_UNVALIDATED", None, T)
    return conn, profile


def item():
    days = get_calendar().sessions_in_range("2024-01-02", "2024-06-28")
    bars = tuple(D.PriceBar(security_id="s1",session_date=day.date().isoformat(),
                 source_id="fixture",basis="RAW",currency="USD",available_at=T,fetched_at=T,is_final=True,
                 revision=1,open=Decimal("10"),high=Decimal("11"),low=Decimal("9"),
                 close=Decimal("10"),volume=500000) for day in days)
    clock = D.EvaluationClock(as_of=T,session_date="2024-06-28",calendar_version="XNYS")
    market = D.MarketContext(clock=clock,security_id="s1",importance=0)
    return scan.ScanInput(market,bars)


def test_profile_json_roundtrip_hash_and_compare_swap(tmp_path):
    conn, p = db(tmp_path)
    assert config_service.load(conn).config_hash == p.config_hash
    with pytest.raises(TypeError):
        p.values["max_positions"] = 9
    changed = dict(config.RUNUP_CONFIG,max_positions=4)
    with pytest.raises(ValueError):
        config_service.save(conn,changed,"NEW_UNVALIDATED","wrong",T)
    assert conn.execute("SELECT COUNT(*) FROM config_profiles").fetchone()[0] == 1
    new = config_service.save(conn,changed,"NEW_UNVALIDATED",p.profile_id,T)
    assert new.config_hash != p.config_hash
    with pytest.raises(ValueError):
        config_service.save(conn,dict(config.RUNUP_CONFIG,max_positions=True),"BAD_UNVALIDATED",new.profile_id,T)
    conn.close()


def test_deterministic_pipeline_and_unavailable_partial(tmp_path):
    conn,p = db(tmp_path)
    first = scan.run(T,p.profile_id,conn,inputs=(item(),))
    assert first == scan.run(T,p.profile_id,conn,inputs=(item(),))
    view = read_models.load(conn,T)
    assert view.latest["status"] == "OK", view.latest["errors"]
    assert view.latest["decisions"][0]["decision"]["entry_eligible"] is False
    assert "WATCH" == view.latest["decisions"][0]["decision"]["setup_state"]
    later = T.replace(hour=23)
    scan.run(later,p.profile_id,conn,inputs=(replace(item(),bars=()),))
    view = read_models.load(conn,later)
    assert view.latest["status"] == "PARTIAL"
    assert view.last_good["run_id"] == first
    with pytest.raises(TypeError):
        view.latest["status"] = "fake"
    conn.close()


def test_source_empty_is_not_zero_candidates(tmp_path):
    conn,p = db(tmp_path)
    scan.run(T,p.profile_id,conn)
    assert read_models.load(conn,T).latest["status"] == "UNAVAILABLE"
    conn.close()


def test_critical_overlay_keeps_daily_identity(tmp_path,monkeypatch):
    from tests.runup_v2.unit.test_step_19_exit import _position
    conn,p = db(tmp_path)
    i = item()
    daily = D.DecisionSnapshot(decision_id="d1",run_id="r1",security_id="s1",as_of=T,
                              config_hash=p.config_hash,feature_hash="f",setup_state=D.SetupState.WATCH,
                              entry_eligible=False,exit_action=D.ExitAction.HOLD,
                              target_sell_fraction=Decimal("0"),reasons=(),health="OK")
    notice = D.RiskNotice(notice_id="n",security_id="s1",reason="early data",
                          severity=D.Severity.CRITICAL,available_at=T,review_status="APPROVED")
    market = replace(i.market,risk_notices=(notice,))
    def forbidden(*args,**kwargs):
        raise AssertionError("daily engine called during overlay")
    monkeypatch.setattr(scan.features,"compute",forbidden)
    overlay = scan.risk_overlay(daily,market,_position(),None,p)
    assert overlay.recommendation == "FULL" and overlay.daily_decision_id == "d1"
    assert overlay.health == "QUOTE_UNKNOWN"
    conn.close()
