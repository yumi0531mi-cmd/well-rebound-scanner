"""V2 Step 09·10 — 학회·SPACE 캘린더 검사 (합성만)."""
from datetime import UTC, datetime

import runup.domain as D
from runup.catalyst import conferences as C
from runup.catalyst import space as S
from runup.data import source_registry as R
from runup.domain.base import validate

NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def _edition():
    return C.ConferenceEdition(org="ASCO", edition="2024-annual",
                               evidence_url="https://www.asco.org/meetings")


def test_step09_release_types_kept_separate():
    edition = _edition()
    kinds = set()
    for release in ("CONFERENCE_TITLE", "ABSTRACT_RELEASE", "LBA_RELEASE",
                    "POSTER", "ORAL", "DATA_RELEASE"):
        event = C.conference_event(edition, release, "2024-06-01",
                                   "EXACT_DATE", NOW, document_id="d1")
        assert validate(event) == []
        kinds.add(event.event_type)
    assert len(kinds) == 6
    try:
        C.conference_event(edition, "KEYNOTE", "2024-06-01", "EXACT_DATE",
                           NOW)
        raise AssertionError("unknown release type accepted")
    except ValueError:
        pass
    try:
        C.conference_event(C.ConferenceEdition(org="NOPE", edition="x"),
                           "POSTER", "2024-06-01", "EXACT_DATE", NOW)
        raise AssertionError("unknown org accepted")
    except ValueError:
        pass


def test_step09_generic_only_company_cannot_enter():
    edition = _edition()
    title = C.conference_event(edition, "CONFERENCE_TITLE", "2024-06-01",
                               "EXACT_DATE", NOW, document_id="d1")
    linked, issues = C.link_issuer(title.candidate_id, "i1", [])
    assert linked is None and issues
    linked, issues = C.link_issuer(title.candidate_id, "i1",
                                   ["회사 발표 확인 IR-2024-01"],
                                   reviewer="human")
    assert issues == [] and linked["status"] == "LINKED_PENDING_REVIEW"


def test_step09_abstract_earlier_and_title_vs_data():
    edition = _edition()
    abstract = C.conference_event(edition, "ABSTRACT_RELEASE", "2024-05-01",
                                  "EXACT_DATE", NOW, document_id="d1",
                                  program_id="prog-a")
    main = C.conference_event(edition, "ORAL", "2024-06-03", "EXACT_DATE",
                              NOW, document_id="d1", program_id="prog-b")
    assert abstract.candidate_id != main.candidate_id
    assert C.earliest_data_release([abstract, main]) is abstract
    title_only = [C.conference_event(edition, "CONFERENCE_TITLE",
                                     "2024-06-01", "EXACT_DATE", NOW,
                                     document_id="d1")]
    assert C.earliest_data_release(title_only) is None
    no_tz = C.conference_event(edition, "POSTER", "Q2 2024", "QUARTER",
                               NOW, document_id="d1")
    assert no_tz.timezone is None
    assert no_tz.date_precision == D.DatePrecision.QUARTER
    assert validate(no_tz) == []


def test_step09_registry_and_manual_path():
    for org in ("AACR", "ASCO", "ESMO", "ASH"):
        entry = R.get_source(org.lower())
        assert entry["capability"] == "SUPPORTED_MANUAL"
    result, _ = C.collect({}, NOW, {"source_id": "asco"})
    assert result.status == D.CollectionStatus.UNSUPPORTED
    assert result.coverage == "LIVE_UNVERIFIED"


def test_step10_license_is_not_launch():
    mission = S.MissionIdentity(mission_id="m1", vehicle="F9",
                                operator="SpaceX",
                                evidence=["mission page 2024-01-10"])
    assert validate(mission) == []
    licensed = S.faa_license_event("lic-7", "2024-2030", NOW,
                                   document_id="d1")
    assert licensed.event_type == S.LICENSE_EVENT
    assert validate(licensed) == []
    launched = S.launch_event(mission, "2024-03-01", "EXACT_DATE", NOW,
                              document_id="d1")
    assert launched.event_type == S.LAUNCH_EVENT
    assert launched.event_type != licensed.event_type
    assert "2030" not in launched.raw_date_text


def test_step10_net_unbounded_watch_only_bounded_earliest():
    mission = S.MissionIdentity(mission_id="m2")
    vague = S.mission_event(mission, "NET 2024", "UNKNOWN", NOW,
                            document_id="d1")
    assert vague.review_status == "WATCH_ONLY"
    bounded = S.mission_event(mission, "2024-03", "MONTH", NOW,
                              document_id="d1")
    assert bounded.review_status == "PENDING"
    assert bounded.raw_date_text == "2024-03"
    assert validate(bounded) == []


def test_step10_company_link_scrub_cancel_and_coverage():
    linked, issues = S.link_company("launch-m1-x", "i-space", [])
    assert linked is None and issues
    linked, issues = S.link_company("launch-m1-x", "i-space",
                                    ["발사 계약 IR-9"])
    assert issues == [] and linked["status"] == "LINKED_PENDING_REVIEW"
    scrub = S.scrub_revision("launch-m1-x", "NET 2024-04", "날씨", NOW)
    assert scrub["needs_review"] is True
    again = S.mission_event(S.MissionIdentity(mission_id="m1"), "2024-05",
                            "MONTH", NOW, document_id="d2")
    assert again.candidate_id == "mission-m1"
    result, _ = S.collect({}, NOW, {"source_id": "nasa"})
    assert result.status == D.CollectionStatus.UNSUPPORTED
    health = R.health_record("nasa", "LIVE_UNVERIFIED")
    assert validate(health) == []
    assert S.MissionIdentity(mission_id="m1") != S.MissionIdentity(
        mission_id="m2")
