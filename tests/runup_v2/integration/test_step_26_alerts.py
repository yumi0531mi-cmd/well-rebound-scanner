"""Outbox semantic rerun, redaction, dry receipts and retry persistence."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import config
import runup.domain as D
from runup.services import alerts
from tests.runup_v2.integration.test_step_25_services import T, db


def decision():
    return D.DecisionSnapshot(decision_id="d",run_id="r",security_id="s",as_of=T,
        config_hash="h",feature_hash="f",setup_state=D.SetupState.WATCH,entry_eligible=False,
        exit_action=D.ExitAction.HOLD,target_sell_fraction=Decimal("0"),
        reasons=("token=private-value",),health="OK")


def test_same_semantics_date_change_and_target_increase(tmp_path):
    conn,_ = db(tmp_path)
    d = decision()
    first = alerts.enqueue(d,conn=conn)
    assert first.accepted
    assert not alerts.enqueue(replace(d,as_of=T+timedelta(seconds=60),decision_id="new"),conn=conn).accepted
    assert "private-value" not in conn.execute("SELECT payload FROM alert_outbox").fetchone()[0]
    second = alerts.enqueue(replace(d,exit_action=D.ExitAction.PARTIAL,target_sell_fraction=Decimal(".25")),conn=conn)
    assert second.accepted
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 2
    result = alerts.dry_run(first,conn,config.RUNUP_CONFIG,T)
    assert not result.delivered and result.transport_receipt.startswith("dry-run:")
    assert alerts.dry_run(first,conn,config.RUNUP_CONFIG,T).transport_receipt == result.transport_receipt
    assert conn.execute("SELECT attempts FROM alert_outbox WHERE alert_id=?", (first.outbox_id,)).fetchone()[0] == 1
    conn.close()


def test_retry_deadletter_never_changes_ledger(tmp_path):
    conn,_ = db(tmp_path)
    outbox = alerts.enqueue(decision(),conn=conn)
    for i in range(3):
        alerts.dry_run(outbox,conn,config.RUNUP_CONFIG,T+timedelta(seconds=100*i),True)
    row = conn.execute("SELECT status,attempts FROM alert_outbox").fetchone()
    assert tuple(row) == ("DEAD_LETTER",3)
    assert conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == 0
    conn.close()

