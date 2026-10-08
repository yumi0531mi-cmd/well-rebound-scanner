"""Korean UI labels (user request: all Korean)."""
import config
from runup.ui import i18n


def test_every_schema_key_has_korean_label():
    missing = [k for k in config.RUNUP_SCHEMA if not i18n.ko_config(k) or i18n.ko_config(k) == k]
    assert missing == []
    for key, label in i18n.CONFIG_KO.items():
        assert label and any("\uac00" <= c <= "\ud7a3" for c in label), key


def test_enum_roundtrip():
    assert i18n.from_ko(i18n.SIDE_KO, "매수") == "BUY"
    assert i18n.from_ko(i18n.SIDE_KO, "매도") == "SELL"
    assert i18n.from_ko(i18n.FLOW_KO, "입금") == "DEPOSIT"
    assert i18n.ko_status("PROPOSED") == "제안됨"
    assert i18n.ko_status("CONFIG_REQUIRED") == "설정 입력 필요"


def test_ko_row_and_table():
    row = {"ticker": "NVX", "status": "PROPOSED", "mystery": 1}
    shown = i18n.ko_row(row)
    assert shown == {"티커": "NVX", "상태": "제안됨", "mystery": 1}
    table = i18n.ko_table([{"ticker": "NVX", "status": "OK"}],
                          {"ticker": "티커", "status": "상태"})
    assert table == [{"티커": "NVX", "상태": "정상"}]


def test_settings_form_korean_labels(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from streamlit.testing.v1 import AppTest

    from runup.services import config_service
    from runup.storage import connect, migrate
    conn = connect(tmp_path / "k.sqlite3")
    migrate(conn)
    config_service.save(conn, dict(config.RUNUP_CONFIG), "TEST_KO_UNVALIDATED", None,
                        datetime(2024, 1, 10, 12, 0, tzinfo=UTC))
    conn.close()
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", "test-korean-only-123456789")
    path = tmp_path / "k.sqlite3"
    app = AppTest.from_string(
        "from runup.ui.main import render\nrender(" + repr(str(path)) + ")").run(timeout=30)
    assert not app.exception
    app.text_input(key="runup_login_value").input("test-korean-only-123456789")
    next(b for b in app.button if b.label == "인증").click().run(timeout=30)
    assert not app.exception
    labels = [str(e.label) if hasattr(e, "label") else "" for e in app.text_input]
    assert any("수수료율" in label for label in labels)
    assert not any(label == "fee_estimate_rate" for label in labels)
