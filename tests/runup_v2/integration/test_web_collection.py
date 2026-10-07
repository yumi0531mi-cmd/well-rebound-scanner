"""Synthetic integration checks, never strategy performance evidence."""
from datetime import UTC, datetime
from types import SimpleNamespace
import json

import pytest
import config
from runup.data.http import HttpResponse
from runup.services import collection, config_service, handlers, operations, scan
from runup.storage import connect, migrate
from runup.storage.database import transaction
from wellscan.models import Candidate, Market, TradingSession

T = datetime(2025, 6, 30, 22, tzinfo=UTC)


def database(tmp_path):
    conn = connect(tmp_path/'test.db')
    migrate(conn)
    config_service.initialize(conn)
    return conn


def test_candidate_foreign_keys_us_only_and_replay(tmp_path):
    conn = database(tmp_path)
    us = Candidate('ABC','Example',10,1,100,1000,market=Market.US,exchange='NAS')
    kr = Candidate('123456','Korean',10,1,100,1000)
    with transaction(conn):
        collection.persist_us_candidates(conn, [us,kr], T)
        collection.persist_us_candidates(conn, [us,kr], T)
    assert conn.execute('SELECT COUNT(*) FROM securities').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM universe_observations').fetchone()[0] == 1
    assert conn.execute('SELECT equity_type FROM securities').fetchone()[0] == 'UNKNOWN'
    conn.close()


def test_live_shape_clinical_persist_and_duplicate(tmp_path):
    body = json.dumps({'studies':[{'protocolSection':{
        'identificationModule':{'nctId':'NCT001','briefTitle':'Example'},
        'statusModule':{'primaryCompletionDateStruct':{'date':'2025-09'}},
        'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Example'}},
        'designModule':{'phases':['PHASE2']}}}], 'nextPageToken':'page2'}).encode()
    fetch = lambda *args: HttpResponse(status=200,body=body)
    commit,cursor = collection.prepare_clinical(T, transport=fetch)
    conn = database(tmp_path)
    with transaction(conn): commit(conn)
    with transaction(conn): commit(conn)
    assert cursor == 'page2'
    row = conn.execute('SELECT * FROM event_candidates').fetchone()
    assert row['date_precision'] == 'MONTH'
    assert row['review_status'] == 'PENDING'
    assert row['available_at'] == T.isoformat()
    assert conn.execute('SELECT COUNT(*) FROM catalyst_revisions').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM event_candidates').fetchone()[0] == 1
    conn.close()


def test_failed_clinical_does_not_advance_or_succeed(tmp_path):
    commit,cursor = collection.prepare_clinical(T,'old',lambda *a: HttpResponse(status=403))
    conn = database(tmp_path)
    with transaction(conn): commit(conn)
    assert cursor == 'old'
    row = conn.execute('SELECT * FROM source_health').fetchone()
    assert row['status'] == 'FAILED' and row['last_success'] is None
    conn.close()


def test_scan_and_settlement_inside_fenced_transaction(tmp_path):
    conn = database(tmp_path)
    runtime = SimpleNamespace(runup_db_path=tmp_path/'test.db')
    for fn in (handlers._scan_handler,handlers._settlement_handler,handlers._event_handler):
        commit,cursor = fn(runtime,T,None)
        with transaction(conn): commit(conn)
        assert cursor
    assert conn.execute('SELECT status FROM runup_results').fetchone()[0] == 'UNAVAILABLE'
    conn.close()


def test_backup_restore_integrity_and_overwrite(tmp_path):
    conn = database(tmp_path)
    conn.close()
    source=tmp_path/'test.db'
    backup=operations.backup(source,tmp_path/'backup.db')
    restored=operations.restore(backup,tmp_path/'restored.db')
    assert operations.verify_database(restored)['integrity'] == 'ok'
    with pytest.raises(ValueError): operations.backup(source,backup)
    assert operations.health(restored)['reconciliation']
