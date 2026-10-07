"""V2 Step 06 — ClinicalTrials 수집기 검사. 실제 fixture + 합성."""
import json
from datetime import UTC, datetime
from pathlib import Path

import runup.domain as D
from runup.catalyst import clinical_trials as CT
from runup.catalyst import mapping as M
from runup.data import source_registry as R
from runup.domain.base import validate
from runup.storage import connect, migrate, transaction
from runup.storage import repositories as repo

NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REAL = json.loads((FIXTURES / "source" / "ct_api_v2_sample.json")
                  .read_text(encoding="utf-8"))


def _policy(**over):
    base = {"source_id": "clinicaltrials", "max_attempts": 3,
            "max_response_bytes": 10485760, "max_redirects": 3,
            "retry_max_seconds": 30, "timeout_seconds": 20}
    base.update(over)
    return base


def test_step06_real_fixture_fields_and_precision():
    study = REAL["studies"][0]
    candidate, detail, issues = CT.parse_study(study)
    assert issues == []
    assert candidate.candidate_id == "NCT01203735"
    assert candidate.event_type == CT.TRIAL_COMPLETION_MARKER
    assert candidate.raw_date_text == "2013-02"
    assert candidate.date_precision == D.DatePrecision.MONTH
    assert detail["overall_status"] == "UNKNOWN"
    assert detail["sponsor"] == "Soroka University Medical Center"
    assert detail["phases"] == ["PHASE1", "PHASE2"]
    assert detail["pcd_precision"] == "MONTH"
    assert REAL["nextPageToken"].startswith("ZVNj")


def test_step06_real_fixture_not_readout_or_entry():
    study = REAL["studies"][0]
    candidate, detail, _ = CT.parse_study(study)
    assert "readout" not in candidate.event_type.lower()
    assert candidate.review_status == "PENDING"
    risks = CT.extract_risk_candidates([detail])
    assert risks == []
    proposals = M.propose_mapping(detail["sponsor"],
                                  [(None, None, "hospital",
                                    ["병원 sponsor, 상장 미확인"])])
    assert M.requires_review(proposals[0])
    assert proposals[0].issuer_id is None


def test_step06_day_month_missing_dates():
    day = {"protocolSection": {
        "identificationModule": {"nctId": "NCT1"},
        "statusModule": {"overallStatus": "RECRUITING",
                         "primaryCompletionDateStruct": {
                             "date": "2025-06-15"}}}}
    candidate, _, issues = CT.parse_study(day)
    assert issues == []
    assert candidate.date_precision == D.DatePrecision.EXACT_DATE
    nodate = {"protocolSection": {
        "identificationModule": {"nctId": "NCT2"},
        "statusModule": {"overallStatus": "RECRUITING"}}}
    candidate, _, issues = CT.parse_study(nodate)
    assert candidate is None
    assert any("WATCH_ONLY" in i for i in issues)
    bad = {"protocolSection": {
        "identificationModule": {"nctId": "NCT3"},
        "statusModule": {"overallStatus": "RECRUITING",
                         "primaryCompletionDateStruct": {"date": 202506}}}}
    candidate, _, issues = CT.parse_study(bad)
    assert candidate is None and issues
    noid = {"protocolSection": {"identificationModule": {}}}
    candidate, _, issues = CT.parse_study(noid)
    assert candidate is None and issues


def test_step06_empty_malformed_and_partial():
    from runup.data.http import HttpResponse

    empty_result, _ = CT.collect_from_body(b'{"studies": []}', NOW)
    assert empty_result.status == D.CollectionStatus.EMPTY_CONFIRMED
    bad_result, _ = CT.collect_from_body(b"not json", NOW)
    assert bad_result.status == D.CollectionStatus.FAILED
    noshape, _ = CT.collect_from_body(b'{"total": 5}', NOW)
    assert noshape.status == D.CollectionStatus.FAILED

    good_body = json.dumps(
        {"studies": [REAL["studies"][0]], "nextPageToken": "tok1"}).encode()
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow")
        return HttpResponse(status=200, headers=(), body=good_body,
                            elapsed_seconds=0.1)

    result, details = CT.collect({}, NOW, _policy(), transport=flaky)
    assert result.status == D.CollectionStatus.OK
    assert len(details) == 1 and result.next_cursor == "tok1"

    mixed = json.dumps({"studies": [REAL["studies"][0],
                                    {"protocolSection": {}}],
                        "nextPageToken": "tok2"}).encode()
    partial, kept = CT.collect_from_body(mixed, NOW)
    assert partial.status == D.CollectionStatus.PARTIAL
    assert len(kept) == 1 and partial.next_cursor == "tok2"


def test_step06_cursor_advances_only_after_commit(tmp_path):
    conn = connect(tmp_path / "ct.sqlite3")
    migrate(conn)
    good_body = json.dumps({"studies": [REAL["studies"][0]]}).encode()
    result, details = CT.collect_from_body(good_body, NOW,
                                           document_id="doc-1")
    assert result.status == D.CollectionStatus.OK
    assert all(c.document_id == "doc-1" for c in result.items)
    with transaction(conn):
        repo.start_collection_run(conn, "ct-run-1", "clinicaltrials",
                                  NOW.isoformat())
        assert repo.get_cursor(conn, "ct-run-1") == ""
        repo.advance_cursor(conn, "ct-run-1", "tok-next")
        repo.finish_collection_run(conn, "ct-run-1", NOW.isoformat(),
                                   "OK", cursor="tok-next")
    assert repo.get_cursor(conn, "ct-run-1") == "tok-next"
    for candidate in result.items:
        assert validate(candidate) == []
    conn.close()


def test_step06_risk_only_from_verified_set():
    detail = {"nct_id": "NCT9", "overall_status": "SOMETHING_NEW"}
    assert CT.extract_risk_candidates([detail]) == []
    assert CT.extract_risk_candidates(
        [detail], terminal_statuses={"SOMETHING_NEW"})[0]["severity"] \
        == "REVIEW"


def test_step06_source_policy_and_live_unverified():
    effective = R.resolve_effective_policy("clinicaltrials")
    assert effective["capability"] == "SUPPORTED_MANUAL"
    health = R.health_record("clinicaltrials", "LIVE_UNVERIFIED")
    assert validate(health) == []
    assert health.coverage == "LIVE_UNVERIFIED"
