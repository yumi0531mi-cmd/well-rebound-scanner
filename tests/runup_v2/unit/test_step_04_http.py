"""V2 Step 04 — HTTP·출처 보존·source registry 검사 (no-network)."""
from datetime import UTC, datetime

import pytest

import config
import runup.domain as D
from runup.data import http as H
from runup.data import provenance as P
from runup.data import source_registry as R
from runup.domain.base import validate
from runup.storage import connect, migrate, transaction
from runup.storage import repositories as repo

NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
SECRET = "KIS_APP_KEY=SUPERSECRET123"


def _policy(**over):
    base = {"source_id": "sec", "max_attempts": 3,
            "max_response_bytes": 100, "max_redirects": 3,
            "retry_max_seconds": 30, "timeout_seconds": 5}
    base.update(over)
    return base


def _resp(status, body=b"", headers=()):
    return H.HttpResponse(status=status, headers=tuple(headers), body=body,
                          elapsed_seconds=0.01)


def test_step04_empty_200_is_confirmed_not_failed():
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(), transport=lambda *a: _resp(200, b""))
    assert outcome.outcome == "EMPTY_CONFIRMED"
    assert outcome.document is None


def test_step04_ok_document_has_provenance():
    body = b'{"hello": "world"}'
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(), transport=lambda *a: _resp(200, body))
    assert outcome.outcome == "OK"
    assert outcome.attempts == 1
    assert validate(outcome.document) == []
    assert outcome.document.payload_hash
    assert outcome.document.first_seen_at is not None
    assert SECRET not in outcome.evidence


def test_step04_timeout_then_500_then_ok_or_failed():
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow")
        if calls["n"] == 2:
            return _resp(500, b"err")
        return _resp(200, b"fine")

    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(), transport=flaky)
    assert outcome.outcome == "OK"
    assert outcome.attempts == 3
    assert calls["n"] == 3


def test_step04_long_retry_after_returns_immediately():
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(),
        transport=lambda *a: _resp(429, b"busy",
                                   headers=(("Retry-After", "3600"),)))
    assert outcome.outcome == "RATE_LIMITED"
    assert outcome.attempts == 1
    assert outcome.retry_after_seconds == 3600.0


def test_step04_4xx_does_not_retry():
    calls = {"n": 0}

    def denied(*args):
        calls["n"] += 1
        return _resp(403, b"no")

    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(), transport=denied)
    assert outcome.outcome == "FAILED"
    assert outcome.attempts == 1
    assert calls["n"] == 1


def test_step04_oversize_body_not_stored():
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(max_response_bytes=10),
        transport=lambda *a: _resp(200, b"x" * 11))
    assert outcome.outcome == "FAILED"
    assert outcome.document is None
    assert "size" in outcome.evidence


def test_step04_private_and_loop_addresses_refused():
    for url in ("http://127.0.0.1/x", "http://10.0.0.5/x",
                "http://169.254.169.254/x", "http://localhost/x",
                "ftp://example.com/x"):
        outcome = H.fetch_document(H.HttpRequest(method="GET", url=url),
                                   _policy(),
                                   transport=lambda *a: _resp(200, b"z"))
        assert outcome.outcome == "FAILED", url
        assert outcome.attempts == 0, url


def test_step04_redirect_private_loop_and_budget():
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(),
        transport=lambda *a: _resp(
            302, b"", headers=(("Location", "http://10.9.9.9/b"),)))
    assert outcome.outcome == "FAILED"

    def loop(*args):
        return _resp(302, b"", headers=(("Location",
                                        "https://example.com/a"),))

    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(), transport=loop)
    assert outcome.outcome == "FAILED"
    assert "loop" in outcome.evidence

    hops = {"n": 0}

    def chain(*args):
        hops["n"] += 1
        return _resp(302, b"", headers=((
            "Location", f"https://example.com/{hops['n']}"),))

    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a"),
        _policy(max_redirects=2), transport=chain)
    assert outcome.outcome == "FAILED"
    assert "redirect" in outcome.evidence


def test_step04_credentials_never_in_evidence():
    outcome = H.fetch_document(
        H.HttpRequest(method="GET", url="https://example.com/a",
                      headers=(("Authorization", SECRET),)),
        _policy(), transport=lambda *a: _resp(200, b"ok"))
    assert outcome.outcome == "OK"
    assert SECRET not in outcome.evidence
    assert SECRET not in str(outcome.document.url)


def test_step04_fetch_store_observe_cursor_order(tmp_path):
    conn = connect(tmp_path / "s4.sqlite3")
    migrate(conn)
    body = b"<filing>abc</filing>"
    outcome = H.fetch_document(
        H.HttpRequest(method="GET",
                      url="https://www.sec.gov/files/abc"),
        _policy(), transport=lambda *a: _resp(200, body))
    assert outcome.outcome == "OK"
    doc = P.build_source_document(
        "sec", "https://www.sec.gov/files/abc", body, 200, observed_at=NOW,
        parser_version="sec-parser-v1")
    assert validate(doc) == []
    blob = P.save_blob(body, tmp_path / "blobs", doc.payload_hash)
    assert blob.is_file()
    stored_doc = D.SourceDocument(**{**D.to_dict(doc),
                                     "blob_path": str(blob)})
    assert stored_doc.blob_path == str(blob)
    assert validate(doc) == []
    with transaction(conn):
        repo.start_collection_run(conn, "run-sec-1", "sec",
                                  NOW.isoformat())
        stored = repo.insert_document(conn, {
            **D.to_dict(stored_doc),
            "fetched_at": doc.fetched_at.isoformat(),
            "first_seen_at": doc.first_seen_at.isoformat(),
            "available_at": doc.available_at.isoformat(),
            "published_at": None})
        repo.observe_document(conn, stored, NOW.isoformat(), cursor="p1")
        repo.advance_cursor(conn, "run-sec-1", "p1")
        repo.finish_collection_run(conn, "run-sec-1", NOW.isoformat(),
                                   "OK", cursor="p1")
    assert repo.get_cursor(conn, "run-sec-1") == "p1"
    same = repo.insert_document(conn, {
        **D.to_dict(stored_doc),
        "fetched_at": doc.fetched_at.isoformat(),
        "first_seen_at": doc.first_seen_at.isoformat(),
        "available_at": doc.available_at.isoformat(),
        "published_at": None, "document_id": "other"})
    assert same == stored
    conn.close()


def test_step04_registry_honest_capabilities_and_rates():
    assert "sec" in R.list_sources()
    sec = R.get_source("sec")
    assert sec["capability"] == "SUPPORTED_MANUAL"
    assert sec["contact_configured"] is False
    assert all(R.get_source(name)["capability"] != "AUTOMATED"
               for name in R.list_sources())
    effective = R.resolve_effective_policy("sec")
    assert effective["requests_per_second"] == config.RUNUP_CONFIG[
        "sec_requests_per_second"] == 2
    assert effective["timeout_seconds"] == config.RUNUP_CONFIG[
        "http_timeout_seconds"]
    with pytest.raises(ValueError, match="unknown source"):
        R.get_source("nope")
    dump = D.canonical_json(
        {name: R.get_source(name) for name in R.list_sources()})
    assert "KIS_APP_KEY" not in dump
    assert "SUPERSECRET" not in dump
    health = R.health_record("sec", "NEEDS_INPUT")
    assert validate(health) == []
    assert health.coverage == "LIVE_UNVERIFIED"
