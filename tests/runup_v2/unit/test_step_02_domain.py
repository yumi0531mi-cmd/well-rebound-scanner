"""V2 Step 02 — 도메인 자료형·입출력 계약 검사."""
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import runup.domain as D
from runup.domain.base import validate

PAST = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
PAST2 = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
FUTURE = datetime(2100, 1, 1, tzinfo=UTC)


def _fill(**over):
    base = dict(fill_id="f1", command_id="c1", position_id="p1",
                security_id="s1", side=D.FillSide.BUY, qty=Decimal("10"),
                price=Decimal("100"), fee=Decimal("1"), currency="USD",
                executed_at=PAST, recorded_at=PAST2, evidence="broker-confirm")
    base.update(over)
    return D.Fill(**base)


def _bar(**over):
    base = dict(security_id="s1", session_date=PAST.date(), currency="USD",
                source_id="kis", basis="RAW", available_at=PAST,
                fetched_at=PAST2, is_final=True, revision=1,
                open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
                close=Decimal("100"), volume=1000)
    base.update(over)
    return D.PriceBar(**base)


def test_step02_valid_dtos_have_no_issues():
    assert validate(_fill()) == []
    assert validate(_bar()) == []
    rev = D.CatalystRevision(
        event_id="e1", revision_id="r1", revision_seq=0,
        event_type="readout", date_precision=D.DatePrecision.EXACT_DATE,
        available_at=PAST, status="APPROVED", risk_class="BINARY",
        mapping_status="VERIFIED", importance_class="confirmed",
        start="2024-01-15", end="2024-01-16")
    assert validate(rev) == []
    assert validate(D.DomainIssue(code="X", path="p", message="m")) == []


def test_step02_missing_and_wrong_types_flagged():
    with pytest.raises(ValueError, match="필수값 누락"):
        D.from_dict(D.Fill, {"fill_id": "f1"})
    bad = _fill()
    object.__setattr__(bad, "qty", "10")
    assert any(i.code == "TYPE_ERROR" for i in validate(bad))
    bad = _fill()
    object.__setattr__(bad, "side", "BUY")
    assert any(i.code == "UNKNOWN_ENUM" for i in validate(bad))


def test_step02_decimal_rejects_float_nan_bool_negative():
    with pytest.raises(ValueError, match="float"):
        D.from_dict(D.Fill, {**D.to_dict(_fill()), "qty": 10.5})
    with pytest.raises(ValueError, match="bool"):
        D.from_dict(D.Fill, {**D.to_dict(_fill()), "fee": True})
    with pytest.raises(ValueError, match="Decimal"):
        D.from_dict(D.Fill, {**D.to_dict(_fill()), "price": "NaN"})
    bad = _fill()
    object.__setattr__(bad, "qty", Decimal("-1"))
    assert any(i.code in ("NEGATIVE_NOT_ALLOWED", "OUT_OF_RANGE")
               for i in validate(bad))
    realized = D.PositionProjection(
        position_id="p1", security_id="s1", issuer_id="i1", sector="BIO",
        qty_remaining=Decimal("5"), entry_qty_total=Decimal("10"),
        qty_sold=Decimal("5"), buy_reserved=Decimal("0"),
        sell_reserved=Decimal("0"), avg_entry_price_ex_fee=Decimal("10"),
        cost_basis_remaining=Decimal("50"), realized_pnl=Decimal("-3"),
        status="OPEN", revision=2)
    assert validate(realized) == []


def test_step02_naive_rejected_future_allowed_without_wall_clock():
    from pathlib import Path

    bad = _fill()
    object.__setattr__(bad, "executed_at",
                       datetime(2024, 1, 15, 12, 0))
    naive = [i for i in validate(bad) if i.code == "NAIVE_TIMESTAMP"]
    assert len(naive) == 1 and naive[0].severity == D.Severity.CRITICAL
    bad = _fill()
    object.__setattr__(bad, "executed_at", FUTURE)
    assert [i for i in validate(bad)
            if i.code in ("NAIVE_TIMESTAMP", "FUTURE_TIMESTAMP")] == []
    domain_dir = Path(D.__file__).parent
    for path in sorted(domain_dir.glob("*.py")):
        assert "datetime.now" not in path.read_text(encoding="utf-8"), path.name


def test_step02_unknown_feature_vs_normal_false():
    unknown = D.FeatureValue(name="news_reaction",
                             status=D.FeatureStatus.UNDEFINED, value=None,
                             reason="timestamp 정의 없음")
    assert validate(unknown) == []
    normal_false = D.FeatureValue(name="breakout",
                                  status=D.FeatureStatus.VALID,
                                  value=Decimal("0"))
    assert validate(normal_false) == []
    logical_false = D.FeatureValue(name="formed", status=D.FeatureStatus.VALID,
                                   value=False)
    assert validate(logical_false) == []
    bad = D.FeatureValue(name="x", status=D.FeatureStatus.VALID, value="high")
    assert any(i.code == "TYPE_ERROR" for i in validate(bad))
    missing = D.FeatureValue(name="y", status=D.FeatureStatus.VALID,
                             value=None, reason="")
    assert any(i.code == "MISSING" for i in validate(missing))


def test_step02_round_trip_preserves_values_and_units():
    for dto in (_fill(), _bar(),
                D.CatalystRevision(
                    event_id="e1", revision_id="r1", revision_seq=2,
                    event_type="pdufa", date_precision=D.DatePrecision.WINDOW,
                    available_at=PAST, status="PENDING", risk_class="B",
                    mapping_status="REVIEW", importance_class="routine",
                    issuer_ids=("i1",), security_ids=("s1", "s2")),
                D.LedgerEvent(
                    event_id="l1", command_id="c1", event_type="FILL_BUY",
                    occurred_at=PAST, recorded_at=PAST2,
                    settled_delta=Decimal("-101"), unsettled_delta=Decimal("0"),
                    qty_delta=Decimal("10"), cost_basis_delta=Decimal("101"),
                    realized_delta=Decimal("0"),
                    external_flow_delta=Decimal("0"))):
        restored = D.from_dict(type(dto), D.to_dict(dto))
        assert restored == dto
        assert D.to_dict(restored) == D.to_dict(dto)


def test_step02_frozen_mutation_refused():
    fill = _fill()
    with pytest.raises(dataclasses.FrozenInstanceError):
        fill.qty = Decimal("99")


def test_step02_stable_key_canonical():
    a = {"b": 1, "a": [1, 2]}
    b = {"a": [1, 2], "b": 1}
    assert D.stable_key(a) == D.stable_key(b)
    assert D.stable_key({"a": 1}) != D.stable_key({"a": 2})


def test_step02_price_bar_ohlc_and_basis_rules():
    bad = _bar(open=Decimal("102"))
    assert any("ohlc" in i.path for i in validate(bad))
    bad = _bar(basis="WEIRD")
    assert any("basis" in i.path for i in validate(bad))


def test_step02_cik_and_revision_order_rules():
    bad = D.Issuer(issuer_id="i1", legal_name="n", cik="123")
    assert any("cik" in i.path for i in validate(bad))
    good = D.Issuer(issuer_id="i1", legal_name="n", cik="1234567890")
    assert validate(good) == []
    bad = D.CatalystRevision(
        event_id="e1", revision_id="r1", revision_seq=0, event_type="t",
        date_precision=D.DatePrecision.EXACT_DATE, available_at=PAST,
        status="s", risk_class="r", mapping_status="m",
        importance_class="i", start="2024-01-16", end="2024-01-15")
    assert any("end" in i.path for i in validate(bad))


def test_step02_every_field_has_kind_hint():

    checked = 0
    for name in D.__all__:
        cls = getattr(D, name, None)
        if isinstance(cls, type) and dataclasses.is_dataclass(cls):
            hints = getattr(cls, "__kind_hints__", {})
            fields = {f.name for f in dataclasses.fields(cls)}
            assert set(hints) == fields, name
            checked += 1
    assert checked >= 30


def test_step02_no_forbidden_imports_in_domain():
    from pathlib import Path

    forbidden = ("streamlit", "requests", "sqlite3", "http.client",
                 "wellscan", "import config", "from config", "urllib")
    domain_dir = Path(D.__file__).parent
    for path in sorted(domain_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, (path.name, token)


def test_step02_collection_result_statuses():
    ok = D.CollectionResult(status=D.CollectionStatus.OK,
                            observed_at=PAST, items=())
    assert validate(ok) == []
    failed = D.CollectionResult(
        status=D.CollectionStatus.FAILED, observed_at=PAST, items=(),
        errors=(D.DomainIssue(code="E", path="p", message="m"),))
    assert validate(failed) == []
    with pytest.raises(ValueError, match="enum"):
        D.from_dict(D.CollectionResult,
                    {"status": "BOGUS", "observed_at": PAST.isoformat()})
