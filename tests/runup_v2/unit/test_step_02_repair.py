"""V2 Step 02 repair — REVIEW D01~D12 재현 검사."""
import dataclasses
import typing
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import runup.domain as D
from runup.domain.base import validate

PAST = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
PAST2 = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)

_OVERRIDES = {
    ("Issuer", "cik"): None,
    ("PriceBar", "basis"): "RAW",
    ("DecisionSnapshot", "target_sell_fraction"): Decimal("0.5"),
    ("ExitDecision", "target"): Decimal("0.5"),
    ("Fill", "currency"): "USD",
    ("ConfigSnapshot", "values"): {"max_positions": 5,
                                   "benchmark": "SPY"},
    ("ProfileCommand", "config_values"): {"max_positions": 5},
    ("EventCandidate", "start"): "2024-01-01",
    ("EventCandidate", "end"): "2024-01-02",
    ("CatalystRevision", "start"): "2024-01-01",
    ("CatalystRevision", "end"): "2024-01-02",
}


def _synthesize(cls, depth=0):
    assert depth < 6, cls
    hints = cls.__kind_hints__
    kwargs = {}
    for field in dataclasses.fields(cls):
        key = (cls.__name__, field.name)
        if key in _OVERRIDES:
            kwargs[field.name] = _OVERRIDES[key]
            continue
        kwargs[field.name] = _value_for(cls, field.name, hints[field.name],
                                        depth)
    return cls(**kwargs)


def _value_for(cls, field, hint, depth):
    import runup.domain as domain

    core = hint.replace("?", "")
    if core in ("str", "id", "text"):
        return f"{cls.__name__}.{field}"
    if core == "bool":
        return True
    if core in ("dec", "dec>=0", "dec>0"):
        return Decimal("1.5")
    if core in ("fnum", "fnum>=0", "fnum>0"):
        return Decimal("1.5")
    if core in ("int", "int>=0", "int>0"):
        return 3
    if core == "num>=0":
        return 2
    if core == "dt":
        return PAST
    if core == "date":
        return PAST.date()
    if core == "any":
        return 1
    if core == "tuple":
        return ()
    if core.startswith("enum:"):
        members = list(getattr(domain, core.split(":", 1)[1]))
        return members[0]
    if core.startswith("tuple[") and core.endswith("]"):
        inner = core[6:-1]
        if inner == "str":
            return (f"{field}-1",)
        target = getattr(domain, inner)
        return (_synthesize(target, depth + 1),)
    if core.startswith("dto:"):
        target = getattr(domain, core.split(":", 1)[1])
        return _synthesize(target, depth + 1)
    raise AssertionError((cls, field, hint))


def _all_dto_classes():
    return [cls for name in D.__all__
            if isinstance((cls := getattr(D, name, None)), type)
            and dataclasses.is_dataclass(cls)]


def test_repair_d11_all_blueprint_dtos_exist():
    for name in ("ReviewCommand", "FillCommand", "SettlementCommand",
                 "ReserveExitCommand", "CapitalFlowCommand",
                 "CorporateActionCommand", "ProfileCommand",
                 "AllocationApprovalCommand", "WithdrawalApprovalCommand",
                 "LedgerCommandResult", "JobContext", "ScanRun",
                 "RiskOverlay", "UIReadModels", "OutboxResult",
                 "TransportResult", "ReviewManifest", "Actor", "AuthContext",
                 "Reservation", "Exposure", "PositionValue"):
        assert hasattr(D, name), name


def test_repair_d01_d12_every_dto_round_trips_nonempty():
    failures = []
    for cls in _all_dto_classes():
        try:
            instance = _synthesize(cls)
            issues = validate(instance)
            assert issues == [], (cls.__name__, issues)
            restored = D.from_dict(cls, D.to_dict(instance))
            assert restored == instance, cls.__name__
            assert D.to_dict(restored) == D.to_dict(instance), cls.__name__
        except Exception as exc:  # noqa: BLE001 — 수집 후 보고
            failures.append((cls.__name__, repr(exc)))
    assert failures == []


def _ann_matches_hint(ann, hint):
    import datetime as _dt

    import runup.domain as domain

    core = hint.replace("?", "")
    args = typing.get_args(ann)
    rest = tuple(a for a in args if a is not type(None))
    base = rest[0] if len(rest) == 1 else (ann if not args else None)
    multi = set(rest) if args else None
    if core in ("str", "id", "text"):
        return base is str
    if core == "bool":
        return base is bool
    if core in ("dec", "dec>=0", "dec>0"):
        return base is Decimal
    if core in ("int", "int>=0", "int>0"):
        return base is int
    if core == "num>=0":
        return multi == {int, float, Decimal}
    if core in ("fnum", "fnum>=0", "fnum>0"):
        return (multi is not None and bool(multi)
                and multi <= {float, Decimal})
    if core == "dt":
        return base is datetime
    if core == "date":
        return base is _dt.date
    if core == "any":
        return True
    if core == "tuple":
        return ann is tuple or typing.get_origin(ann) is tuple
    if core.startswith("enum:"):
        return base is getattr(domain, core.split(":", 1)[1])
    if core.startswith("tuple[") and core.endswith("]"):
        if typing.get_origin(ann) is not tuple:
            return False
        inner = core[6:-1]
        inner_args = typing.get_args(ann)
        if inner == "str":
            return inner_args[:1] == (str,)
        target = getattr(domain, inner, None)
        return inner_args[:1] == (target,)
    if core.startswith("dto:"):
        return base is getattr(domain, core.split(":", 1)[1])
    return False


def test_repair_d02_annotation_hint_nullable_match():
    special = {("FeatureValue", "value"), ("SetupSnapshot", "score"),
               ("ConfigSnapshot", "values"), ("ProfileCommand",
                                              "config_values")}
    mismatches = []
    for cls in _all_dto_classes():
        hints = cls.__kind_hints__
        resolved = typing.get_type_hints(cls)
        for field in dataclasses.fields(cls):
            hint = hints[field.name]
            ann = resolved[field.name]
            nullable_hint = "?" in hint
            args = typing.get_args(ann)
            nullable_ann = (ann is type(None)
                            or type(None) in args
                            or ann is typing.Any)
            if (cls.__name__, field.name) in special:
                if ann is not dict and not nullable_ann:
                    mismatches.append((cls.__name__, field.name, "special"))
                continue
            if nullable_hint != nullable_ann:
                mismatches.append((cls.__name__, field.name, hint))
            elif not _ann_matches_hint(ann, hint):
                mismatches.append((cls.__name__, field.name, hint))
    assert mismatches == []


def test_repair_d03_finite_float_features_but_not_money():
    ok = D.FeatureValue(name="mom", status=D.FeatureStatus.VALID,
                        value=2.5, reason="")
    assert validate(ok) == []
    score = D.ScoreResult(status=D.FeatureStatus.VALID, reasons=(),
                          input_hash="h", score=75.5)
    assert validate(score) == []
    restored = D.from_dict(D.FeatureValue, D.to_dict(ok))
    assert restored == ok
    fill_dict = {"fill_id": "f", "command_id": "c", "position_id": "p",
                 "security_id": "s", "side": "BUY", "qty": 10.5,
                 "price": "100", "fee": "1", "currency": "USD",
                 "executed_at": PAST.isoformat(),
                 "recorded_at": PAST2.isoformat(), "evidence": "e"}
    with pytest.raises(ValueError, match="float"):
        D.from_dict(D.Fill, fill_dict)


def test_repair_d04_nested_containers_immutable_and_unaliased():
    external = {"max_positions": 5, "setup_weights": {"dryup": 25}}
    snap = D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="id1", config_hash="h",
                            values=external)
    external["max_positions"] = 99
    external["setup_weights"]["dryup"] = 0
    assert snap.values["max_positions"] == 5
    with pytest.raises(TypeError):
        snap.values["max_positions"] = 99
    with pytest.raises(TypeError):
        snap.values["setup_weights"]["dryup"] = 0
    with pytest.raises(TypeError):
        snap.values["new_key"] = 1
    bars = [D.PriceBar(security_id="s", session_date=PAST.date(),
                       currency="USD", source_id="k", basis="RAW",
                       available_at=PAST, fetched_at=PAST2, is_final=True,
                       revision=1, open=Decimal("1"), high=Decimal("2"),
                       low=Decimal("1"), close=Decimal("2"), volume=1)]
    clock = D.EvaluationClock(as_of=PAST, session_date="2024-01-15",
                             calendar_version="v1")
    ctx = D.BarContext(clock=clock, security_id="s", source_id="k",
                       basis="RAW", bar_hash="h", completeness="FULL",
                       bars=bars)
    bars.append(bars[0])
    assert len(ctx.bars) == 1
    with pytest.raises(AttributeError):
        ctx.bars[0].close = Decimal("9")


def test_repair_d05_wrong_types_yield_issues_not_exceptions():
    bad_issuer = D.Issuer(issuer_id="i", legal_name="n", cik=12345)
    assert any("cik" in i.path for i in validate(bad_issuer))
    bad_bar = D.PriceBar(security_id="s", session_date=PAST.date(),
                         currency="USD", source_id="k", basis="RAW",
                         available_at=PAST, fetched_at=PAST2, is_final=True,
                         revision=1, open="99", high=Decimal("1"),
                         low=Decimal("1"), close=Decimal("1"), volume=1)
    assert validate(bad_bar)
    bad_rev = D.CatalystRevision(
        event_id="e", revision_id="r", revision_seq=0, event_type="t",
        date_precision="not-a-precision", available_at=PAST, status="s",
        risk_class="r", mapping_status="m", importance_class="i")
    assert any("date_precision" in i.path for i in validate(bad_rev))


def test_repair_d06_cross_field_ranges():
    base = dict(security_id="s", session_date=PAST.date(), currency="USD",
                source_id="k", basis="RAW", available_at=PAST,
                fetched_at=PAST2, is_final=True, revision=1, volume=1,
                open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
                close=Decimal("2"))
    missing = {k: (v.isoformat() if hasattr(v, "isoformat")
                       else (str(v) if isinstance(v, Decimal) else v))
               for k, v in base.items() if k != "close"}
    with pytest.raises(ValueError, match="필수값 누락"):
        D.from_dict(D.PriceBar, missing)
    krw = {"fill_id": "f", "command_id": "c", "position_id": "p",
           "security_id": "s", "side": "BUY", "qty": "10", "price": "100",
           "fee": "1", "currency": "KRW",
           "executed_at": PAST.isoformat(), "recorded_at": PAST2.isoformat(),
           "evidence": "e"}
    assert any("currency" in i.path
               for i in validate(D.from_dict(D.Fill, krw)))
    nan_score = D.SetupSnapshot(
        setup_id="s", generation_id="g", security_id="s", as_of=PAST,
        score=float("nan"), qualified=False, expires_session="2024-01-16")
    assert any("score" in i.path for i in validate(nan_score))
    over = D.SetupSnapshot(
        setup_id="s", generation_id="g", security_id="s", as_of=PAST,
        score=101, qualified=False, expires_session="2024-01-16")
    assert any("score" in i.path for i in validate(over))
    target2 = D.ExitDecision(
        action=D.ExitAction.PARTIAL, target=Decimal("2"),
        additional_qty=Decimal("1"), qty_basis="Q0", decision_id="d",
        input_hash="h")
    assert any("target" in i.path for i in validate(target2))


def test_repair_d07_valid_needs_value_nonvalid_needs_reason():
    assert validate(D.FeatureValue(
        name="f", status=D.FeatureStatus.VALID, value=False)) == []
    assert any(i.code == "MISSING" for i in validate(D.FeatureValue(
        name="f", status=D.FeatureStatus.VALID, value=None)))
    assert any(i.code == "TYPE_ERROR" for i in validate(D.FeatureValue(
        name="f", status=D.FeatureStatus.VALID, value="high")))
    assert any("reason" in i.path for i in validate(D.FeatureValue(
        name="f", status=D.FeatureStatus.STALE, value=None, reason="")))
    hl = D.FeatureValue(name="HH", status=D.FeatureStatus.VALID,
                        value=Decimal("1"))
    pivots = D.PivotSet(reference_high_known_previous_session="2024-01-10",
                        reference_low="2024-01-12", higher_high=hl)
    assert validate(pivots) == []


def test_repair_d08_future_events_allowed_naive_refused():
    future = datetime(2030, 5, 1, 12, 0, tzinfo=UTC)
    rev = D.CatalystRevision(
        event_id="e", revision_id="r", revision_seq=0, event_type="launch",
        date_precision=D.DatePrecision.EXACT_DATE, available_at=PAST,
        status="PENDING", risk_class="SCHEDULE", mapping_status="REVIEW",
        importance_class="routine", start=future)
    assert [i for i in validate(rev)
            if i.code in ("NAIVE_TIMESTAMP", "FUTURE_TIMESTAMP")] == []
    naive = D.CatalystRevision(
        event_id="e", revision_id="r", revision_seq=0, event_type="launch",
        date_precision=D.DatePrecision.EXACT_DATE, available_at=PAST,
        status="PENDING", risk_class="SCHEDULE", mapping_status="REVIEW",
        importance_class="i", start=datetime(2030, 5, 1, 12, 0))
    assert any(i.code == "TYPE_ERROR" for i in validate(naive))


def test_repair_d09_provenance_and_collection_invariants():
    doc = D.SourceDocument(document_id="d", source_id="s",
                           url="https://example.com/x", fetched_at=PAST,
                           first_seen_at=PAST, available_at=PAST,
                           payload_hash="ab")
    assert validate(doc) == []
    bare = D.SourceDocument(document_id="d", source_id="s",
                            url="https://example.com/x", fetched_at=PAST,
                            first_seen_at=PAST, available_at=PAST,
                            payload_hash="ab")
    object.__setattr__(bare, "first_seen_at", None)
    object.__setattr__(bare, "available_at", None)
    object.__setattr__(bare, "payload_hash", "")
    codes = {i.path for i in validate(bare)}
    codes = {i.path for i in validate(bare)}
    assert "SourceDocument.first_seen_at" in codes
    assert "SourceDocument.available_at" in codes
    assert "SourceDocument.payload_hash" in codes
    items_str = D.CollectionResult(status=D.CollectionStatus.OK,
                                  observed_at=PAST, items="abc")
    assert any("items" in i.path for i in validate(items_str))
    empty_nonempty = D.CollectionResult(
        status=D.CollectionStatus.EMPTY_CONFIRMED, observed_at=PAST,
        items=({"a": 1},))
    assert any("items" in i.path for i in validate(empty_nonempty))
    failed_bare = D.CollectionResult(status=D.CollectionStatus.FAILED,
                                     observed_at=PAST)
    assert any("errors" in i.path for i in validate(failed_bare))


def test_repair_d10_canonical_utc_and_decimal_scale():
    plus9 = datetime(2024, 1, 15, 12, 0,
                     tzinfo=UTC).astimezone(ZoneInfo("Asia/Seoul"))
    utc = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    assert D.stable_key({"t": plus9}) == D.stable_key({"t": utc})
    assert D.stable_key({"v": Decimal("1.50")}) == \
        D.stable_key({"v": Decimal("1.500")})
    assert D.stable_key({"v": Decimal("100")}) == \
        D.stable_key({"v": Decimal("100.0")})


def test_repair_d14_unknown_keys_enums_nested_reported():
    good = {"fill_id": "f", "command_id": "c", "position_id": "p",
            "security_id": "s", "side": "BUY", "qty": "10", "price": "100",
            "fee": "1", "currency": "USD",
            "executed_at": PAST.isoformat(),
            "recorded_at": PAST2.isoformat(), "evidence": "e",
            "hacker": 1}
    with pytest.raises(ValueError, match="unknown keys"):
        D.from_dict(D.Fill, good)
    bad_enum = dict(good)
    del bad_enum["hacker"]
    bad_enum["side"] = "HOLD"
    with pytest.raises(ValueError, match="enum"):
        D.from_dict(D.Fill, bad_enum)
    with pytest.raises(ValueError, match="mapping"):
        D.from_dict(D.Fill, ["not", "a", "mapping"])
