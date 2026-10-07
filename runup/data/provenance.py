"""Runup V2 출처 증거 (Step 04). HTTP 성공과 parser 성공을 분리한다."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path


def build_source_document(source_id, url, body: bytes, http_status: int,
                          observed_at=None, parser_version="runup-http-v2",
                          media_type="", published_at=None):
    """수집 증거 DTO. observed_at이 first_seen/available_at이다."""
    from runup.domain.documents import SourceDocument

    now = observed_at or datetime.now(UTC)
    digest = hashlib.sha256(bytes(body)).hexdigest()
    return SourceDocument(
        document_id=f"{source_id}-{digest[:16]}",
        source_id=str(source_id), url=str(url),
        fetched_at=now, published_at=published_at, first_seen_at=now,
        available_at=now, payload_hash=digest,
        media_type=str(media_type or ""), blob_path="",
        parser_version=str(parser_version), time_quality="UNKNOWN",
        http_status=int(http_status))


def save_blob(body: bytes, blob_dir, payload_hash: str) -> Path:
    """원문 bytes 저장. 경로·secret 검사는 호출자(storage)가 담당."""
    target_dir = Path(blob_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{payload_hash}.bin"
    target.write_bytes(bytes(body))
    return target


def collection_record(run_id, source_id, started_at, finished_at=None,
                      status="RUNNING", cursor="", error="") -> dict:
    return {"run_id": run_id, "source_id": source_id,
            "started_at": started_at, "finished_at": finished_at,
            "status": status, "cursor": cursor, "error": error}
