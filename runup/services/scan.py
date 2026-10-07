"""Snapshot-based daily orchestration and a separate intraday risk overlay."""
from __future__ import annotations

import dataclasses
import json
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import runup.domain as D
from runup.catalyst.normalize import risk_boundary
from runup.data import calendar
from runup.domain.base import from_dict, stable_key, to_dict
from runup.engine import exhaustion, features, pivots, setup, strength, trigger
from runup.engine import exit as exit_engine
from runup.portfolio.ledger import _current_positions
from runup.services import config_service
from runup.storage import connect, migrate
from runup.storage.database import transaction


def _at(text):
    at = datetime.fromisoformat(str(text))
    if at.tzinfo is None:
        raise ValueError("missing timezone")
    return at.astimezone(UTC)


def _dto(cls, row):
    data = dict(row)
    fields = {f.name for f in dataclasses.fields(cls)}
    hints = cls.__kind_hints__
    data = {k: v for k, v in data.items() if k in fields}
    for k, value in list(data.items()):
        if hints.get(k, "").startswith("tuple") and isinstance(value, str):
            data[k] = json.loads(value)
        elif hints.get(k) == "bool":
            data[k] = bool(value)
    return from_dict(cls, data)


@dataclasses.dataclass(frozen=True)
class ScanInput:
    market: D.MarketContext
    bars: tuple[D.PriceBar, ...]
    benchmark: tuple[D.PriceBar, ...] = ()
    positions: tuple[D.PositionProjection, ...] = ()
    consumed_generations: tuple[str, ...] = ()


def load_inputs(conn, as_of, profile):
    # One SQLite snapshot; latest revisions known as-of, no speculative missing dates.
    securities = conn.execute("SELECT * FROM securities ORDER BY security_id").fetchall()
    events = {}
    for row in conn.execute("SELECT * FROM catalyst_revisions ORDER BY revision_seq"):
        if _at(row["available_at"]) <= as_of:
            events[row["event_id"]] = _dto(D.CatalystRevision, row)
    items, failures = [], []
    for row in securities:
        sid = row["security_id"]
        try:
            security = _dto(D.Security, row)
            issuer_row = conn.execute("SELECT * FROM issuers WHERE issuer_id=?",
                                      (security.issuer_id,)).fetchone()
            issuer = None
            if issuer_row and _at(issuer_row["observed_at"]) <= as_of:
                issuer = _dto(D.Issuer, issuer_row)
                evidence = tuple(r[0] for r in conn.execute(
                    "SELECT evidence FROM mapping_reviews WHERE security_id=? "
                    "AND status='APPROVED' AND reviewed_at<=?", (sid, as_of.isoformat())))
                issuer = dataclasses.replace(issuer, mapping_evidence=evidence)
            revisions = {}
            for b in conn.execute("SELECT * FROM price_bar_revisions WHERE security_id=? ORDER BY revision", (sid,)):
                if _at(b["available_at"]) <= as_of:
                    revisions[b["session_date"]] = _dto(D.PriceBar, b)
            bars = tuple(revisions[k] for k in sorted(revisions))
            cats = tuple(e for e in events.values() if sid in e.security_ids)
            notices = tuple(_dto(D.RiskNotice, r) for r in conn.execute(
                "SELECT * FROM risk_notices WHERE security_id=?", (sid,))
                if _at(r["available_at"]) <= as_of)
            boundary, notes = risk_boundary(cats, as_of, profile.values["forced_exit_buffer_sessions"])
            session = str(bars[-1].session_date) if bars else as_of.date().isoformat()
            clock = D.EvaluationClock(as_of=as_of, session_date=session, calendar_version="XNYS")
            importance_keys = {"first":"importance_first_key_pivotal_major",
                               "confirmed":"importance_confirmed", "routine":"importance_routine"}
            importance = max((profile.values.get(importance_keys.get(e.importance_class, ""), 0)
                              for e in cats), default=0)
            urow = conn.execute("SELECT * FROM universe_observations WHERE security_id=? "
                                "AND available_at<=? ORDER BY available_at DESC LIMIT 1",
                                (sid, as_of.isoformat())).fetchone()
            observation = _dto(D.UniverseObservation, urow) if urow else None
            market = D.MarketContext(clock=clock, security_id=sid, security=security,
                                     issuer=issuer, issuer_id=security.issuer_id,
                                     universe_observation=observation, catalysts=cats,
                                     risk_notices=notices, importance=importance,
                                     earliest_risk_boundary=boundary["boundary"] if boundary else None,
                                     forced_exit_deadline=boundary["forced_exit_deadline"] if boundary else None)
            positions = tuple(_dto(D.PositionProjection, p) for p in _current_positions(conn, as_of)
                              if p["security_id"] == sid and Decimal(p["qty_remaining"]) > 0)
            consumed = tuple(r[0] for r in conn.execute(
                "SELECT t.generation_id FROM trigger_snapshots t "
                "JOIN decision_snapshots d ON d.trigger_id=t.trigger_id "
                "JOIN allocations a ON a.decision_id=d.decision_id "
                "WHERE a.status IN ('RESERVED','FILLED')"))
            benchmark_rows = {}
            for b in conn.execute("SELECT * FROM price_bar_revisions WHERE security_id=? AND available_at<=? ORDER BY revision", ('BENCHMARK:'+profile.values['benchmark'], as_of.isoformat())):
                benchmark_rows[b['session_date']] = _dto(D.PriceBar, b)
            benchmark = tuple(benchmark_rows[k] for k in sorted(benchmark_rows))
            items.append(ScanInput(market, bars, benchmark=benchmark, positions=positions, consumed_generations=consumed))
        except Exception as exc:
            failures.append(sid+":"+type(exc).__name__)
    return tuple(items), tuple(failures)


def _sessions(item):
    day = item.market.clock.session_date
    first = date.fromisoformat(str(item.bars[0].session_date)) if item.bars else datetime.fromisoformat(day).date()
    sessions = calendar.get_calendar().sessions_in_range(
        first-timedelta(days=180),
        (datetime.fromisoformat(day)+timedelta(days=180)).date())
    return [d.date().isoformat() for d in sessions]


def evaluate_daily(item, profile, run_id, previous=None):
    market = item.market
    cfg = dict(profile.values, config_hash=profile.config_hash)
    bars = [to_dict(b) for b in item.bars if b.is_final and b.available_at <= market.clock.as_of]
    if not bars:
        raise ValueError("NO_FINAL_BARS")
    if len({(b["basis"], b["source_id"]) for b in bars}) != 1 or bars[0]["basis"] == "UNKNOWN":
        raise ValueError("PRICE_BASIS_CONFLICT")
    session = bars[-1]["session_date"]
    sessions = _sessions(item)
    actual_sessions = [s for s in sessions if bars[0]["session_date"] <= s <= session]
    if [b["session_date"] for b in bars] != actual_sessions:
        raise ValueError("MISSING_PRICE_SESSION")
    feature = features.compute(bars, [to_dict(b) for b in item.benchmark], market.clock.as_of, cfg)
    feature = dataclasses.replace(feature, feature_id=stable_key(
        {"bar": feature.bar_hash, "profile": profile.config_hash, "as_of": str(feature.as_of)}))
    pivot = pivots.confirm(bars, session, cfg)
    context = D.SetupContext(market=market, config=profile, features=feature,
                             pivots=pivot, previous_setup=previous)
    prepared = setup.evaluate(context, sessions)
    numbers = {c.name: c.value for c in feature.components if c.status == D.FeatureStatus.VALID}
    prior = features.compute(bars[:-1], [to_dict(b) for b in item.benchmark], market.clock.as_of, cfg)
    prior_n = {c.name: c.value for c in prior.components if c.status == D.FeatureStatus.VALID}
    tr = None
    if all(numbers.get(k) is not None for k in ("sma20", "rvol", "close_location")) and prior_n.get("atr14"):
        tr = trigger.evaluate(D.TriggerContext(
            setup=context, current_close=Decimal(bars[-1]["close"]), current_ma=numbers["sma20"],
            atr_previous=prior_n["atr14"], rvol=numbers["rvol"], close_location=numbers["close_location"],
            previous_close=Decimal(bars[-2]["close"]), previous_ma=prior_n.get("sma20"),
            consumed_generations=item.consumed_generations, setup_snapshot=prepared.snapshot),
            session, sessions)
    score_ctx = D.ScoreContext(config=profile, features=feature, pivots=pivot,
                               previous_features=prior, current_bar=item.bars[-1],
                               previous_bar=item.bars[-2] if len(item.bars)>1 else None)
    strong, exhausted = strength.evaluate(score_ctx), exhaustion.evaluate(score_ctx)
    entry = bool(tr and tr.eligible and not market.gate_issues)
    reasons = tuple(prepared.reasons)+(tuple(tr.reasons) if tr else ("TRIGGER_INPUT_UNAVAILABLE",))
    exits = []
    for p in item.positions:
        stop_ctx = D.StopContext(config=profile, clock=market.clock, position_id=p.position_id,
                                 position=p, features=feature, pivots=pivot)
        ex = exit_engine.evaluate(p, D.ExitContext(stop=stop_ctx, market=market,
                                  cumulative_target=Decimal("0"), score=exhausted),
                                  thresholds=cfg["exhaustion_thresholds"],
                                  targets=cfg["exhaustion_cumulative_targets"], lot_size=cfg["fractional_lot_shares"])
        exits.append(ex)
    action = max(exits, key=lambda e: e.target).action if exits else D.ExitAction.HOLD
    target = max((e.target for e in exits), default=Decimal("0"))
    decision = D.DecisionSnapshot(
        decision_id=stable_key({"run":run_id,"sid":market.security_id}), run_id=run_id,
        security_id=market.security_id, as_of=market.clock.as_of, config_hash=profile.config_hash,
        feature_hash=feature.bar_hash, setup_state=prepared.state, entry_eligible=entry,
        exit_action=action, target_sell_fraction=target, reasons=reasons, health="OK",
        event_ids=tuple(e.event_id for e in market.catalysts),
        trigger_id=tr.trigger_id if tr else None,
        strength=Decimal(str(strong.score)) if strong.score is not None else None,
        exhaustion=Decimal(str(exhausted.score)) if exhausted.score is not None else None)
    return {"decision":to_dict(decision),"features":to_dict(feature),
            "setup":to_dict(prepared.snapshot) if prepared.snapshot else None,
            "trigger":to_dict(tr.snapshot) if tr and tr.snapshot else None,
            "exits":[to_dict(e) for e in exits]}


def run(as_of, config_profile_id=None, conn=None, inputs=None, in_transaction=False):
    owned = conn is None
    conn = conn or connect()
    try:
        if not in_transaction:
            migrate(conn)
        at = _at(as_of)
        with (nullcontext() if in_transaction else transaction(conn)):
            profile = config_service.load(conn, config_profile_id)
            items, failures = (tuple(inputs), ()) if inputs is not None else load_inputs(conn, at, profile)
            input_hash = stable_key([dataclasses.asdict(i.market.clock) |
                {"market":to_dict(i.market),"bars":[to_dict(b) for b in i.bars],
                 "benchmark":[to_dict(b) for b in i.benchmark],"positions":[to_dict(p) for p in i.positions],
                 "consumed":i.consumed_generations} for i in items])
            run_id = stable_key({"as_of":at.isoformat(),"profile":profile.config_hash,"inputs":input_hash})
            existing = conn.execute("SELECT run_id FROM runup_results WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                return run_id
            previous_rows = {r["security_id"]: from_dict(D.SetupSnapshot, json.loads(r["payload"]))
                             for r in conn.execute("SELECT * FROM runup_setup_state WHERE as_of<? AND profile_hash=? ORDER BY as_of",
                                                    (at.isoformat(),profile.config_hash))}
        results, errors = [], list(failures)
        for item in items:
            try:
                results.append(evaluate_daily(item, profile, run_id, previous_rows.get(item.market.security_id)))
            except Exception as exc:
                errors.append(item.market.security_id+":"+type(exc).__name__)
        status = "PARTIAL" if errors else (("EMPTY_CONFIRMED" if inputs is not None else "UNAVAILABLE") if not items else "OK")
        payload = {"run_id":run_id,"as_of":at.isoformat(),"profile_hash":profile.config_hash,
                   "status":status,"errors":errors,"decisions":results,"coverage":"LIVE_UNVERIFIED"}
        with (nullcontext() if in_transaction else transaction(conn)):
            conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                         (run_id,at.isoformat(),profile.config_hash,input_hash,status,json.dumps(payload)))
            for result in results:
                prepared = result["setup"]
                if prepared:
                    conn.execute("INSERT OR REPLACE INTO runup_setup_state VALUES (?,?,?,?)",
                                 (prepared["security_id"],at.isoformat(),profile.config_hash,json.dumps(prepared)))
                # Enqueue alert for each decision
                decision = from_dict(D.DecisionSnapshot, result["decision"])
                from runup.services.alerts import enqueue as enqueue_alert
                enqueue_alert(decision, conn=conn, in_transaction=True)
        return run_id
    finally:
        if owned:
            conn.close()


def risk_overlay(daily, market, position, quote, profile, features_snapshot=None, pivot_set=None):
    # Risk refresh never calls features/setup/trigger; daily snapshot identity is retained.
    stop_ctx = D.StopContext(config=profile,clock=market.clock,position_id=position.position_id,
                             position=position,quote=quote,features=features_snapshot,pivots=pivot_set)
    result = exit_engine.evaluate(position,D.ExitContext(stop=stop_ctx,market=market,
                             cumulative_target=Decimal(daily.target_sell_fraction)),
                              quote=quote,thresholds=profile.values["exhaustion_thresholds"],
                              targets=profile.values["exhaustion_cumulative_targets"])
    return D.RiskOverlay(security_id=position.security_id,observed_at=market.clock.as_of,
                         daily_decision_id=daily.decision_id,recommendation=result.action.value,
                         health=quote.status if quote else "QUOTE_UNKNOWN",
                         event_revisions=tuple(e.revision_id for e in market.catalysts))
