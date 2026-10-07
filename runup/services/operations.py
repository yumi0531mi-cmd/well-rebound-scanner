"""Explicit sandbox backup/restore and diagnostic operations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from runup.services import config_service, jobs
from runup.storage import connect
from runup.storage.migrations import SCHEMA_VERSION, current_version


def _new_target(destination):
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError("destination exists; overwrite forbidden")
    if not target.is_relative_to(Path.cwd().resolve()):
        raise ValueError("destination outside workspace")
    if any(p.is_symlink() for p in [Path(destination),*Path(destination).parents]):
        raise ValueError("symlink forbidden")
    if any(word in target.name.lower() for word in ("secret",".env","auth","account","token")):
        raise ValueError("sensitive destination forbidden")
    target.parent.mkdir(parents=True,exist_ok=True)
    return target


def verify_database(path):
    source = Path(path)
    if source.is_symlink():
        raise ValueError("symlink source forbidden")
    conn = sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or current_version(conn) != SCHEMA_VERSION:
            raise ValueError("database corrupt or migration required")
        profile = config_service.load(conn)
        return {"integrity":"ok","schema_version":SCHEMA_VERSION,"profile_hash":profile.config_hash}
    finally:
        conn.close()


def backup(source,destination):
    verify_database(source)
    target = _new_target(destination)
    reader = sqlite3.connect(Path(source).resolve().as_uri()+"?mode=ro",uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    verify_database(target)
    return target


def restore(backup_path,destination):
    # Always a new sandbox file; never overwrite production state.
    return backup(backup_path,destination)


def health(path):
    try:
        report = verify_database(path)
        conn = connect(path)
        try:
            from datetime import UTC, datetime

            from runup.portfolio.ledger import rebuild
            projection = rebuild(datetime.now(UTC),conn)
            report.update(worker=jobs.status(),ledger_revision=projection.revision,
                          reconciliation=projection.reconciliation_status,
                          operating_inputs=config_service.load(conn).values["tax_reserve"] is not None)
            return report
        finally:
            conn.close()
    except (ValueError,sqlite3.Error,OSError) as exc:
        return {"status":"FAILED","cause":type(exc).__name__}

