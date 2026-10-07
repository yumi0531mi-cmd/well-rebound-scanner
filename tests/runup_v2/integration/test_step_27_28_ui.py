"""Tab render and server-gated command boundaries."""
import ast
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import config
from runup.services import auth
from runup.services.commands import Commands
from tests.runup_v2.integration.test_step_25_services import T, db


def test_grant_not_forged_expired_or_rotated(monkeypatch,tmp_path):
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN","test-server-auth-only-123456789")
    good = auth.login("test-server-auth-only-123456789",as_of=T)
    assert auth.verified(good,T)
    assert not auth.verified({"verified":True},T)
    assert not auth.verified(replace(good,actor="intruder"),T)
    assert auth.verified(good,T+timedelta(seconds=901))
    assert not auth.verified(good,T+timedelta(seconds=86401))
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN","another-server-auth-123456789")
    assert not auth.verified(good,T)


def test_every_command_denies_direct_readonly_call(tmp_path):
    conn,_ = db(tmp_path)
    service = Commands(conn,{"verified":True})
    for fn,args in ((service.prepare,()),(service.fill,(None,)),(service.capital_flow,(None,)),
                    (service.settle,(None,)),(service.reserve_sell,(None,)*5),
                    (service.cancel_sell,(None,)*3),(service.allocation,(None,)*3),
                    (service.withdrawal,(None,)*4),(service.profile,(None,)*3),
                    (service.scan,()),(service.review,(None,))):
        with pytest.raises(PermissionError):
            fn(*args)
    assert conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == 0
    conn.close()


def test_render_missing_db_does_not_create_or_start_jobs(tmp_path,monkeypatch):
    from runup.services import jobs
    path=tmp_path/"absent.db"
    before=jobs.status()
    app=AppTest.from_string(
        "from runup.ui.main import render\nrender("+repr(str(path))+")").run(timeout=20)
    assert not app.exception
    assert not path.exists()
    assert jobs.status()==before
    assert any("미준비" in message.value for message in app.info)
    assert app.checkbox(key="runup_mobile").value is False
    app.checkbox(key="runup_mobile").check().run()
    assert not app.exception


def test_render_populated_readonly_model_and_profile_widgets(tmp_path,monkeypatch):
    conn,p=db(tmp_path)
    path=tmp_path/"s.db"
    conn.close()
    app=AppTest.from_string(
        "from runup.ui.main import render\nrender("+repr(str(path))+")").run(timeout=20)
    assert not app.exception
    assert any("실현손익" in message.value for message in app.subheader)
    assert any("설정 "+p.config_hash in message.value for message in app.caption)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN","test-server-auth-only-123456789")
    app.text_input(key="runup_login_value").input("test-server-auth-only-123456789")
    app.button[0].click().run(timeout=20)
    assert not app.exception
    # Every schema field has its own prefixed input; None costs stay None.
    assert app.checkbox(key="runup_none_fee_estimate_rate").value is True
    assert app.checkbox(key="runup_value_runup_worker_enabled").value is False


def test_mount_preserves_exact_legacy_ast():
    text=Path("app.py").read_text(encoding="utf-8")
    original=Path("work/evidence/runup_v2/legacy_ui_before_mount.txt").read_text(encoding="utf-8")
    parsed=ast.parse(text)
    wrapper=next(n for n in parsed.body if isinstance(n,ast.With) and
                 "runup_tabs[0]" in ast.unparse(n.items[0].context_expr))
    assert ast.dump(ast.Module(body=wrapper.body,type_ignores=[])) == ast.dump(ast.parse(original))
    assert text.index("render_runup_tab()") < text.index("with runup_tabs[0]")
    assert config.RUNUP_CONFIG["runup_worker_enabled"] is False

