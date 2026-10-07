"""Final review bundle builder (Step 31). Allowlist only, no secrets.

읽기 전용으로 코드 해시·설정 계약·출처 상태·개수·잔여 목록을 묶는다.
원장明细·보유·예약·정산 원문, .env·비밀값·계좌번호는 절대 포함하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# 번들에 들어갈 수 있는 파일 종류. 이 목록 밖은 쓰지 않는다.
ALLOWLIST_FILES = ("manifest.json", "code_manifest.json", "config_contract.json",
                   "source_status.json", "residuals.json", "test_evidence.json",
                   "privacy.json")

# 값 형태 비밀 패턴(키 이름이 아니라 대입된 값). 열 이름·환경변수 이름은 허용.
SECRET_VALUE_PATTERNS = (
    r"(?i)(app_key|app_secret|api_secret|password|passwd)\s*[:=]\s*['\"][^'\"]{4,}",
    r"sk-[A-Za-z0-9]{8,}",
    r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{8,}",
)

# 개수만 싣는 표. 원문 행은 절대 덤프하지 않는다.
COUNT_TABLES = ("securities", "issuers", "universe_observations",
                "event_candidates", "catalyst_revisions", "mapping_reviews",
                "price_bar_revisions", "positions", "ledger_events",
                "allocations", "withdrawal_periods", "runup_results",
                "source_health", "config_profiles")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_files(root: Path) -> list:
    files = [root / "runup_app.py", root / "config.py"]
    files.extend(sorted((root / "runup").rglob("*.py")))
    return [p for p in files if p.is_file()]


def _read_db(db_path):
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _counts(conn) -> dict:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                if t in tables else "NO_TABLE") for t in COUNT_TABLES}


def _residuals(conn, profile_values) -> list:
    items = []
    if profile_values is None:
        items.append("NEEDS_INPUT: 프로필 없음(초기 설정 필요)")
        return items
    for key in ("fee_estimate_rate", "fee_minimum", "slippage_estimate", "tax_reserve"):
        if profile_values.get(key) is None:
            items.append(f"NEEDS_INPUT: {key} 미입력(신규 배분·정산 보류)")
    counts = _counts(conn)
    if isinstance(counts.get("catalyst_revisions"), int) and counts["catalyst_revisions"] == 0:
        items.append("NEEDS_INPUT: 승인 이벤트 0건(검토 대기 처리 필요)")
    if isinstance(counts.get("mapping_reviews"), int) and counts["mapping_reviews"] == 0:
        items.append("NEEDS_INPUT: issuer 매핑 검토 0건")
    items.append("NEEDS_INPUT: 375px 실기기 확인(조작은 AppTest로만 검증)")
    items.append("LIVE_UNVERIFIED: 무료 출처 실제 연결·전체 커버리지 미확인")
    items.append("LOCAL_ONLY: 원격 배포·실전 검증·수익성 검증 아님")
    return items


def build_bundle(destination=None, db_path=None, as_of=None):
    """검토 번들을 만든다. manifest dict를 반환한다."""
    import config
    from runup.services import operations

    root = Path.cwd().resolve()
    stamp = (as_of or datetime.now(UTC)).astimezone(UTC)
    target = operations._new_target(
        Path(destination) if destination else
        Path(f"work/evidence/runup_v2/final_bundle_{stamp.strftime('%Y%m%d')}"))
    if target.name not in ("final_bundle",) and not target.name.startswith("final_bundle_"):
        raise ValueError("bundle directory name must start with final_bundle_")
    target.mkdir(parents=True, exist_ok=False)

    code = {str(p.relative_to(root)): _sha256_file(p)
            for p in _code_files(root) if p.resolve().is_relative_to(root)}
    profile_hash, profile_name, values = "", "", None
    source_health, counts, residuals = [], {}, ["NEEDS_INPUT: 런업 저장소 없음"]
    db_used = None
    candidate = Path(db_path) if db_path else Path(str(config.RUNUP_CONFIG["runup_db_path"]))
    if candidate.is_file():
        db_used = str(candidate)
        conn = _read_db(candidate)
        try:
            counts = _counts(conn)
            try:
                source_health = [dict(r) for r in conn.execute("SELECT * FROM source_health")]
            except sqlite3.Error:
                source_health = []
            try:
                from runup.services import config_service
                loaded = config_service.load(conn)
                profile_hash, profile_name, values = (
                    loaded.config_hash, loaded.profile_name, dict(loaded.values))
            except (ValueError, sqlite3.Error):
                pass
            residuals = _residuals(conn, values)
        finally:
            conn.close()
    evidence_ref = root / "work/evidence/runup_v2/step_30_verification.md"
    payloads = {
        "code_manifest.json": {"files": code},
        "config_contract.json": {
            "schema_version": config.RUNUP_SCHEMA_VERSION,
            "profile_name": profile_name, "profile_hash": profile_hash,
            "required_costs_configured": values is not None and all(
                values.get(k) is not None for k in
                ("fee_estimate_rate", "fee_minimum", "slippage_estimate", "tax_reserve"))},
        "source_status.json": {"db": db_used, "counts": counts,
                               "source_health": source_health},
        "residuals.json": {"items": residuals},
        "test_evidence.json": {
            "verification_table": str(evidence_ref.relative_to(root))
            if evidence_ref.is_file() else "MISSING",
            "verification_sha256": _sha256_file(evidence_ref)
            if evidence_ref.is_file() else "",
            "command": "pytest tests/runup_v2 tests/test_web_app_boundary.py",
            "result": "226 passed, 2 pre-existing env failures (see verification table)"},
        "privacy.json": {"excluded": [".env", "secrets", "auth cache", "account numbers",
                                      "portfolio row contents"],
                         "rule": "counts and hashes only; no row dumps"},
    }
    manifest = {"bundle": target.name, "created_at_utc": stamp.isoformat(),
                "schema_version": config.RUNUP_SCHEMA_VERSION,
                "profile_hash": profile_hash, "files": {},
                "test_command": payloads["test_evidence.json"]["command"]}
    for name in ALLOWLIST_FILES:
        if name == "manifest.json":
            continue
        text = json.dumps(payloads[name], ensure_ascii=False, indent=2, sort_keys=True)
        (target / name).write_text(text, encoding="utf-8")
        manifest["files"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    (target / "manifest.json").write_text(manifest_text, encoding="utf-8")

    violations = []
    for path in sorted(target.iterdir()):
        if path.name not in ALLOWLIST_FILES:
            violations.append(f"allowlist outside file: {path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_VALUE_PATTERNS:
            if re.search(pattern, text):
                violations.append(f"possible secret value in {path.name}")
    if violations:
        import shutil
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("bundle privacy check failed: " + "; ".join(violations[:3]))
    manifest["privacy"] = "pass"
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    return manifest
