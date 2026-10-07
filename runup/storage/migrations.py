"""Runup V2 versioned schema (Step 03). transactional·append-only."""
from __future__ import annotations

from datetime import UTC

SCHEMA_VERSION = 8

_MIGRATION_002 = """
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES positions(position_id),
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);
"""

_MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issuers (
    issuer_id TEXT PRIMARY KEY,
    cik TEXT,
    legal_name TEXT NOT NULL,
    sector_tags TEXT NOT NULL DEFAULT '[]',
    listing_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS securities (
    security_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL REFERENCES issuers(issuer_id),
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    equity_type TEXT NOT NULL DEFAULT '',
    listing_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    valid_from TEXT NOT NULL,
    valid_to TEXT
);
CREATE TABLE IF NOT EXISTS universe_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    market_cap TEXT,
    as_of TEXT NOT NULL,
    available_at TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    classification_evidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mapping_reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    issuer_id TEXT NOT NULL,
    security_id TEXT,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_documents (
    document_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT '',
    blob_path TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    time_quality TEXT NOT NULL DEFAULT '',
    http_status INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, payload_hash)
);
CREATE TABLE IF NOT EXISTS document_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES source_documents(document_id),
    observed_at TEXT NOT NULL,
    cursor TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    cursor TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS event_candidates (
    candidate_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_documents(document_id),
    evidence_span TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    raw_date_text TEXT NOT NULL DEFAULT '',
    date_precision TEXT NOT NULL DEFAULT 'UNKNOWN',
    review_status TEXT NOT NULL DEFAULT 'PENDING',
    available_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalyst_revisions (
    revision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    revision_seq INTEGER NOT NULL,
    issuer_ids TEXT NOT NULL DEFAULT '[]',
    security_ids TEXT NOT NULL DEFAULT '[]',
    program_id TEXT,
    event_type TEXT NOT NULL DEFAULT '',
    phase TEXT,
    date_precision TEXT NOT NULL DEFAULT 'UNKNOWN',
    start TEXT,
    end TEXT,
    timezone TEXT,
    source_document_ids TEXT NOT NULL DEFAULT '[]',
    available_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    risk_class TEXT NOT NULL DEFAULT '',
    mapping_status TEXT NOT NULL DEFAULT '',
    importance_class TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT,
    reviewed_at TEXT,
    UNIQUE (event_id, revision_seq)
);
CREATE TABLE IF NOT EXISTS event_reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES catalyst_revisions(revision_id),
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_notices (
    notice_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'REVIEW',
    source_document_ids TEXT NOT NULL DEFAULT '[]',
    available_at TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS price_bar_revisions (
    security_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    open TEXT,
    high TEXT,
    low TEXT,
    close TEXT,
    volume INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    basis TEXT NOT NULL DEFAULT 'UNKNOWN',
    available_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    is_final INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (security_id, session_date, source_id, revision)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    effective_session TEXT NOT NULL,
    ratio TEXT,
    gross TEXT,
    net TEXT,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    available_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_profiles (
    profile_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    profile_name TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    values_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_profile (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    profile_id TEXT NOT NULL REFERENCES config_profiles(profile_id),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    profile_hash TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'RUNNING',
    input_revisions TEXT NOT NULL DEFAULT '[]',
    finished_at TEXT,
    errors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS feature_snapshots (
    feature_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    feature_version TEXT NOT NULL DEFAULT '',
    bar_hash TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL DEFAULT '',
    components_json TEXT NOT NULL DEFAULT '[]',
    issues_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS setup_snapshots (
    setup_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    event_ids TEXT NOT NULL DEFAULT '[]',
    as_of TEXT NOT NULL,
    score TEXT,
    qualified INTEGER NOT NULL DEFAULT 0,
    expires_session TEXT NOT NULL DEFAULT '',
    invalidated_at TEXT,
    reasons TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS trigger_snapshots (
    trigger_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    ref_pivot_id TEXT NOT NULL DEFAULT '',
    as_of TEXT NOT NULL,
    valid_until TEXT,
    eligible INTEGER NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id),
    security_id TEXT NOT NULL,
    event_ids TEXT NOT NULL DEFAULT '[]',
    as_of TEXT NOT NULL,
    config_hash TEXT NOT NULL DEFAULT '',
    feature_hash TEXT NOT NULL DEFAULT '',
    setup_state TEXT NOT NULL DEFAULT 'WATCH',
    trigger_id TEXT,
    strength TEXT,
    exhaustion TEXT,
    entry_eligible INTEGER NOT NULL DEFAULT 0,
    exit_action TEXT NOT NULL DEFAULT 'HOLD',
    target_sell_fraction TEXT NOT NULL DEFAULT '0',
    reasons TEXT NOT NULL DEFAULT '[]',
    health TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    executed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    allocation_id TEXT,
    UNIQUE (command_id)
);
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL,
    issuer_id TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '',
    qty_remaining TEXT NOT NULL,
    entry_qty_total TEXT NOT NULL,
    qty_sold TEXT NOT NULL DEFAULT '0',
    buy_reserved TEXT NOT NULL DEFAULT '0',
    sell_reserved TEXT NOT NULL DEFAULT '0',
    avg_entry_price_ex_fee TEXT NOT NULL,
    cost_basis_remaining TEXT NOT NULL,
    realized_pnl TEXT NOT NULL DEFAULT '0',
    initial_stop TEXT,
    trailing_stop TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    revision INTEGER NOT NULL DEFAULT 1,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_events (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    position_id TEXT,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    settled_delta TEXT NOT NULL,
    unsettled_delta TEXT NOT NULL,
    qty_delta TEXT NOT NULL,
    cost_basis_delta TEXT NOT NULL,
    realized_delta TEXT NOT NULL,
    external_flow_delta TEXT NOT NULL,
    reversal_of TEXT,
    UNIQUE (command_id)
);
CREATE TABLE IF NOT EXISTS settlement_commands (
    command_id TEXT PRIMARY KEY,
    amount TEXT NOT NULL,
    confirmed_date TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    expected_ledger_revision TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS allocations (
    allocation_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    decision_id TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL DEFAULT '',
    budget TEXT NOT NULL,
    qty TEXT NOT NULL,
    entry_reference TEXT NOT NULL,
    initial_stop_reference TEXT NOT NULL,
    reservation_usd TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    reference_quote_id TEXT,
    UNIQUE (command_id)
);
CREATE TABLE IF NOT EXISTS withdrawal_periods (
    period_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    cumulative_net TEXT NOT NULL,
    processed_before TEXT NOT NULL,
    processed_base TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    command_id TEXT NOT NULL,
    monthly_net TEXT,
    tax_earmark_delta TEXT,
    profit_earmark_delta TEXT,
    PRIMARY KEY (period_id, revision)
);
CREATE TABLE IF NOT EXISTS alert_outbox (
    alert_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    event_ids TEXT NOT NULL DEFAULT '[]',
    decision_ids TEXT NOT NULL DEFAULT '[]',
    payload_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    transport_receipt TEXT,
    UNIQUE (idempotency_key)
);
CREATE TABLE IF NOT EXISTS worker_leases (
    worker_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    fencing_token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_receipts (
    command_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    ledger_revision TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalyst_event_available
    ON catalyst_revisions(event_id, available_at);
CREATE INDEX IF NOT EXISTS idx_price_security_session
    ON price_bar_revisions(security_id, session_date, available_at);
CREATE INDEX IF NOT EXISTS idx_ledger_recorded
    ON ledger_events(recorded_at);
"""

_MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS allocation_events (
    allocation_id TEXT PRIMARY KEY REFERENCES allocations(allocation_id),
    event_ids TEXT NOT NULL DEFAULT '[]'
);
"""


_MIGRATION_004 = """
CREATE TABLE IF NOT EXISTS source_cursors (
    source_id TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at TEXT NOT NULL
);
ALTER TABLE withdrawal_periods ADD COLUMN recorded_at TEXT;
"""

_MIGRATION_005 = """
CREATE TABLE IF NOT EXISTS runup_results (
    run_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, profile_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runup_setup_state (
    security_id TEXT NOT NULL, as_of TEXT NOT NULL, profile_hash TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(security_id,as_of,profile_hash)
);
CREATE TABLE IF NOT EXISTS runup_overlays (
    overlay_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL
);
"""

_MIGRATION_006 = """
ALTER TABLE alert_outbox ADD COLUMN payload TEXT NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS runup_alert_state (
    security_id TEXT PRIMARY KEY, semantic_key TEXT NOT NULL, observed_at TEXT NOT NULL
);
"""

_MIGRATION_007 = """
-- Add recorded_at to positions for as-of reconstruction (idempotent)
-- Check if column exists first
"""

def _migration_007_positions_recorded_at(conn):
    """Add recorded_at column to positions if not exists, then backfill from ledger history."""
    # Check if recorded_at column exists
    cols = [row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "recorded_at" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN recorded_at TEXT")
    # Backfill recorded_at from ledger_events: for each position, use the latest
    # recorded_at from its ledger events. Positions with no events get far-past.
    conn.execute("""
        UPDATE positions
        SET recorded_at = COALESCE((
            SELECT MAX(recorded_at)
            FROM ledger_events
            WHERE ledger_events.position_id = positions.position_id
        ), '1970-01-01T00:00:00+00:00')
        WHERE recorded_at IS NULL
    """)

_MIGRATION_008 = """
CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    last_success TEXT, last_error TEXT, coverage TEXT NOT NULL,
    revision INTEGER NOT NULL
);
"""

MIGRATIONS = [(1, _MIGRATION_001), (2, _MIGRATION_002),
              (3, _MIGRATION_003), (4, _MIGRATION_004), (5, _MIGRATION_005),
              (6, _MIGRATION_006), (7, _migration_007_positions_recorded_at),
              (8, _MIGRATION_008)]


def current_version(conn) -> int:
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_versions").fetchone()
    except Exception:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn) -> int:
    """보류 migration을 순서대로 적용. 각 migration은 자체 transaction."""
    from datetime import datetime

    from runup.storage.database import transaction

    applied = current_version(conn)
    for version, sql_or_fn in sorted(MIGRATIONS):
        if version <= applied:
            continue
        with transaction(conn):
            if callable(sql_or_fn):
                sql_or_fn(conn)
            else:
                statements = [part.strip() for part in sql_or_fn.split(";")]
                for statement in statements:
                    if statement:
                        conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_versions(version, applied_at) "
                "VALUES (?, ?)",
                (version,
                 datetime.now(UTC).isoformat()))
        applied = version
    return applied
