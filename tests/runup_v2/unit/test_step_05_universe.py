"""V2 Step 05 — universe·mapping 검사 (합성 fixture)."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import runup.domain as D
from runup.catalyst import mapping as M
from runup.data import universe as U
from runup.domain.base import validate
from runup.storage import connect, migrate, transaction
from runup.storage import repositories as repo

T0 = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
T2 = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)


def _security(ticker, exchange="NASDAQ", equity="COMMON", valid_from=None,
              valid_to=None, currency="USD"):
    return D.Security(
        security_id=f"{ticker}.{exchange}", issuer_id="i1", ticker=ticker,
        exchange=exchange, currency=currency, equity_type=equity,
        listing_status="LISTED", valid_from=valid_from or T0,
        valid_to=valid_to)


def test_step05_multi_ticker_and_similar_names_not_merged():
    first = _security("AAA")
    second = _security("AAAB")
    assert first.security_id != second.security_id
    assert validate(first) == [] and validate(second) == []
    other = D.Issuer(issuer_id="i2", legal_name="AAA Therapeutics",
                     valid_from=T0)
    assert other.issuer_id != "i1"


def test_step05_cik_leading_zero_and_rename_delisting_windows():
    good = D.Issuer(issuer_id="i1", legal_name="n", cik="0123456789",
                    valid_from=T0)
    assert validate(good) == []
    old, _ = U.resolve_security_at([_security("AAA", valid_to=T1)], "AAA",
                                   T0)
    assert old is not None
    gone, issues = U.resolve_security_at(
        [_security("AAA", valid_to=T1)], "AAA", T2)
    assert gone is None and issues
    new, _ = U.resolve_security_at([_security("AAA", valid_from=T1)], "AAA",
                                   T2)
    assert new is not None
    dup, issues = U.resolve_security_at(
        [_security("AAA"), _security("AAA", exchange="NYSE")], "AAA", T2)
    assert dup is None and any("REVIEW" in i for i in issues)


def test_step05_etf_non_us_unreviewed_excluded():
    eligible, _ = U.is_us_listed(_security("AAA"))
    assert eligible
    for bad in (_security("AAA", exchange="KRX"),
                _security("AAA", currency="KRW"),
                _security("QQQ", equity="ETF")):
        eligible, reasons = U.is_us_listed(bad)
        assert not eligible and reasons


def test_step05_sector_bucket_and_review_on_weak_evidence():
    assert U.sector_bucket("BIO") == "BIOPHARMA"
    assert U.sector_bucket("pharma") == "BIOPHARMA"
    assert U.sector_bucket("SPACE") == "SPACE"
    assert U.sector_bucket("") == "OTHER"
    assert U.sector_bucket("rumor") == "OTHER"


def test_step05_mcap_not_zero_no_backdating_and_stale():
    obs_new = D.UniverseObservation(
        security_id="s", market_cap=Decimal("1e9"),
        as_of=T1, available_at=T1, source_id="curated")
    obs_old = D.UniverseObservation(
        security_id="s", market_cap=None, as_of=T0, available_at=T0,
        source_id="curated")
    assert validate(obs_new) == [] and validate(obs_old) == []
    assert U.latest_observation([obs_new, obs_old], T0) is obs_old
    assert U.latest_observation([obs_new, obs_old], T2) is obs_new
    assert U.latest_observation([obs_new], T0) is None
    assert U.is_stale(obs_new, T2, max_age_seconds=3600)
    assert not U.is_stale(obs_new, T1, max_age_seconds=3600)
    assert U.is_stale(None, T1, max_age_seconds=1)


def test_step05_coverage_honest_scope():
    coverage = U.UniverseCoverage(
        coverage_id="c1", universe_id="curated-biotech-50", as_of=T1,
        source_id="curated", scope_note="수동 선별 50종, 미국 전체 아님",
        total_listed=50, classified=40, pending=8, stale=2)
    assert validate(coverage) == []
    assert "전체" in coverage.scope_note


def test_step05_substring_only_never_confirms_hospital_review():
    proposals = M.propose_mapping("Nova", [("i1", "s1", "partial",
                                            ["이름 일부 일치"])])
    assert len(proposals) == 1
    assert M.requires_review(proposals[0])
    assert proposals[0].issuer_id == "i1"
    hospital = M.propose_mapping("Soroka University Medical Center",
                                 [(None, None, "hospital",
                                   ["병원 sponsor"])])
    assert hospital[0].issuer_id is None
    assert M.requires_review(hospital[0])
    with pytest.raises(ValueError, match="match kind"):
        M.propose_mapping("x", [("i1", None, "vibes", [])])
    record = M.review_mapping(proposals[0], reviewer="human",
                              decision="REJECTED", evidence=["병원임"])
    assert record["status"] == "REVIEWED"
    with pytest.raises(ValueError, match="decision"):
        M.review_mapping(proposals[0], reviewer="human", decision="MAYBE")


def test_step05_manual_entry_needs_evidence_and_stays_review():
    with pytest.raises(ValueError, match="근거"):
        U.manual_issuer_entry("i9", "N", "NVX", "NASDAQ", T1, evidence=[])
    entry = U.manual_issuer_entry("i9", "Nova Bio", "NVX", "NASDAQ", T1,
                                  evidence=["IR 2024-01-10"],
                                  sector_tags=["BIO"])
    assert entry["review_status"] == "REVIEW"
    assert validate(entry["issuer"]) == []
    assert validate(entry["security"]) == []
    listed = U.from_master_catalog([{"ticker": "NVX",
                                        "exchange": "NASDAQ"}])
    assert listed[0]["status"] == "UNVERIFIED_LISTING_CANDIDATE"


def test_step05_observations_and_reviews_persist(tmp_path):
    conn = connect(tmp_path / "u5.sqlite3")
    migrate(conn)
    with transaction(conn):
        repo.upsert_issuer(conn, "i1", "Nova Bio", cik="0123456789",
                           observed_at=T0.isoformat())
        repo.upsert_security(conn, "NVX.NASDAQ", "i1", "NVX", "NASDAQ",
                             "USD", "COMMON", "LISTED", T0.isoformat())
        repo.save_universe_observation(
            conn, "NVX.NASDAQ", 1000000000, T1.isoformat(), T1.isoformat(),
            "curated", status="OK")
        repo.save_mapping_review(conn, "i1", "NVX.NASDAQ", "REVIEW",
                                 "sponsor 부분일치", "human", T1.isoformat())
    row = conn.execute(
        "SELECT market_cap FROM universe_observations").fetchone()
    assert row[0] == "1000000000"
    row = conn.execute(
        "SELECT status FROM mapping_reviews").fetchone()
    assert row[0] == "REVIEW"
    conn.close()
