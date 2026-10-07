"""Independent runup web app. No legacy scanner UI or daemon is started."""
import streamlit as st


def worker_enabled() -> bool:
    """DB 활성 프로필의 자동 수집 스위치. 없으면 코드 기본값."""
    try:
        import config
        from runup.services import config_service
        from runup.storage import connect
        from runup.storage.repositories import get_active_profile
        conn = connect()
        try:
            if get_active_profile(conn) is None:
                return bool(config.RUNUP_CONFIG.get("runup_worker_enabled", False))
            return bool(config_service.load(conn).values.get("runup_worker_enabled", False))
        finally:
            conn.close()
    except Exception:
        return False


def worker_handlers() -> dict:
    """수집 작업 묶음. 이름·주기·함수만 둔다."""
    import config
    from runup.services.handlers import _daily_handler, _event_handler, _quote_handler, _scan_handler, _settlement_handler, _source_handler
    return {
        "source": (config.RUNUP_CONFIG.get("source_poll_seconds", 300), _source_handler),
        "daily": (config.RUNUP_CONFIG.get("daily_poll_seconds", 86400), _daily_handler),
        "quote": (config.RUNUP_CONFIG.get("quote_poll_seconds", 60), _quote_handler),
        "event": (config.RUNUP_CONFIG["risk_notice_poll_minutes"] * 60, _event_handler),
        "scan": (config.RUNUP_CONFIG.get("scan_poll_seconds", 86400), _scan_handler),
        "settlement": (config.RUNUP_CONFIG.get("settlement_cadence_seconds", 86400),
                        _settlement_handler),
    }


def maybe_start_worker():
    """스위치가 켜져 있을 때만 수집 작업자를 시작한다. 로그인은 필요 없다."""
    from runup.services import jobs
    if not worker_enabled():
        return {"state": "DISABLED"}
    from wellscan.scanner_service import shared_runtime_components
    status = jobs.start(shared_runtime_components(), db_path=None)
    try:
        jobs.register_handlers(worker_handlers())
    except Exception:
        pass
    return status


def main():
    from runup.ui.main import render
    st.set_page_config(page_title="런업스캐너", page_icon="📅", layout="wide")
    maybe_start_worker()
    render()


if __name__ == "__main__":
    main()
