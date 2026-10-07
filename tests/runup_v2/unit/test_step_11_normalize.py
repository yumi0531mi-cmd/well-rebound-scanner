"""V2 Step 11 — 정규화·검토·위험 경계 검사."""
from datetime import UTC, datetime

import runup.domain as D
from runup.catalyst import normalize as N
from runup.catalyst import review as R
from runup.data import calendar as cal
from runup.domain.base import validate

T0 = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def _candidate(**over):
    base = dict(candidate_id="c1", document_id="d1",
                evidence_span="Phase 3 readout met endpoint",
                event_type="READOUT_CANDIDATE", raw_date_text="2024-06-15",
                date_precision=D.DatePrecision.EXACT_DATE,
                review_status="PENDING", available_at=T0)
    base.update(over)
    return D.EventCandidate(**base)


_MAPPING = {"issuer_id": "i1", "status": "VERIFIED",
            "security_ids": ["s1"]}
_REVIEW = {"decision": "APPROVED", "reviewer": "human",
          "evidence_ids": ["d1"], "reviewed_at": T0}


def test_step11_identity_and_revision_append_only():
    first = N.normalize(_candidate(), _MAPPING, _REVIEW, T0,
                        revision_seq=0)
    assert not isinstance(first, D.DomainIssue)
    assert validate(first) == []
    other_day = N.normalize(_candidate(raw_date_text="2024-06-16"),
                            _MAPPING, _REVIEW, T0, revision_seq=0)
    assert other_day.event_id != first.event_id
    other_prog = N.normalize(
        _candidate(program_id="prog-b"), _MAPPING, _REVIEW, T0,
        revision_seq=0)
    assert other_prog.event_id != first.event_id
    assert first.revision_id != other_day.revision_id
    assert first.revision_seq == 0


def test_step11_precision_bounds_and_watch_only():
    month = N.normalize(_candidate(raw_date_text="2024-06",
                                   date_precision=D.DatePrecision.MONTH),
                        _MAPPING, _REVIEW, T0)
    assert month.start == "2024-06-01" and month.end == "2024-07-01"
    quarter = N.normalize(_candidate(raw_date_text="2024-Q2",
                                     date_precision=D.DatePrecision.QUARTER),
                          _MAPPING, _REVIEW, T0)
    assert quarter.start == "2024-04-01" and quarter.end == "2024-07-01"
    unknown = N.normalize(
        _candidate(raw_date_text="언젠가",
                   date_precision=D.DatePrecision.UNKNOWN),
        _MAPPING, _REVIEW, T0)
    assert unknown.status == "WATCH_ONLY"
    no_tz = N.normalize(
        _candidate(raw_date_text="2024-06-15T14:30",
                   date_precision=D.DatePrecision.EXACT_DATETIME),
        _MAPPING, _REVIEW, T0)
    assert no_tz.status == "REVIEW"
    assert validate(no_tz) == []
    exact = N.normalize(
        _candidate(raw_date_text="2024-06-15T14:30",
                   date_precision=D.DatePrecision.EXACT_DATETIME,
                   timezone="America/New_York"),
        _MAPPING, _REVIEW, T0)
    assert exact.status == "PENDING"


def test_step11_unreviewed_mapping_conflict_reject():
    bad_mapping = N.normalize(_candidate(), {"status": "PENDING"}, _REVIEW,
                              T0)
    assert isinstance(bad_mapping, D.DomainIssue)
    conflict = N.normalize(_candidate(), _MAPPING, {"decision": "CONFLICT",
                                                    "reviewer": "human",
                                                    "evidence_ids": ["d1"]},
                           T0)
    assert conflict.status == "CONFLICT"
    rejected = N.normalize(_candidate(), _MAPPING,
                           {"decision": "REJECTED"}, T0)
    assert isinstance(rejected, D.DomainIssue)
    assert N.normalize(None, _MAPPING, _REVIEW, T0).code == "MISSING"


def test_step11_monday_buffer_deadlines_and_calendar():
    assert cal.is_trading_session("2024-07-03")
    assert not cal.is_trading_session("2024-07-04")
    result, issues = cal.forced_exit_deadline("2024-07-08", 1)
    assert issues == []
    assert result["cutoff_close"] == "2024-07-05"
    assert result["deadline_close"] == "2024-07-03"
    result, _ = cal.forced_exit_deadline("2024-01-08", 1)
    assert result["cutoff_close"] == "2024-01-05"
    assert result["deadline_close"] == "2024-01-04"
    early = cal.session_close("2024-11-29")
    assert early is not None
    assert early.tz_convert("America/New_York").hour == 13
    assert cal.month_start(2024, 1) == "2024-01-01"
    assert cal.month_end_exclusive(2024, 12) == "2025-01-01"
    assert cal.quarter_start(2024, 4) == "2024-10-01"


def test_step11_risk_boundary_earliest_and_unbounded():
    early = N.normalize(_candidate(raw_date_text="2024-05-01",
                                   date_precision=D.DatePrecision.EXACT_DATE),
                        _MAPPING, _REVIEW, T0)
    late = N.normalize(_candidate(raw_date_text="2024-06-01",
                                  date_precision=D.DatePrecision.EXACT_DATE,
                                  program_id="p2"),
                       _MAPPING, _REVIEW, T0)
    result, issues = N.risk_boundary([late, early], T0, 1)
    assert issues == []
    assert result["event_id"] == early.event_id
    assert result["cutoff_close"] == "2024-04-30"
    none_result, reasons = N.risk_boundary([], T0, 1)
    assert none_result is None and reasons
    cancelled = N.normalize(
        _candidate(raw_date_text="2024-04-01",
                   date_precision=D.DatePrecision.EXACT_DATE),
        _MAPPING, _REVIEW, T0)
    import dataclasses

    cancelled = dataclasses.replace(cancelled, status="CANCELLED")
    result, _ = N.risk_boundary([cancelled, late], T0, 1)
    assert result["event_id"] == late.event_id


def test_step11_review_command_contract():
    auth = {"verified": True, "permissions": ("review",),
            "actor_id": "human"}
    command = {"command_id": "cmd-1", "candidate_id": "c1",
               "revision_id": "r1", "decision": "APPROVED",
               "evidence_document_ids": ["d1"],
               "expected_revision": "rev-3"}
    record, issue = R.approve_review(command, auth, "rev-3")
    assert issue is None and record["status"] == "REVIEWED"
    assert record["reviewer"] == "human"
    _, conflict = R.approve_review(command, auth, "rev-9")
    assert conflict is not None and "revision" in conflict.path
    _, noauth = R.approve_review(
        command, {"verified": False, "permissions": ()}, "rev-3")
    assert noauth is not None
    _, noperm = R.approve_review(
        command, {"verified": True, "permissions": ()}, "rev-3")
    assert noperm is not None
    _, noev = R.approve_review({**command, "evidence_document_ids": []},
                               auth, "rev-3")
    assert noev is not None
    _, bad = R.approve_review({**command, "decision": "MAYBE"}, auth,
                              "rev-3")
    assert bad is not None
