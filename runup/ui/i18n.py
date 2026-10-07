"""화면 한글 표기. 도메인 코드·DB 값은 그대로 두고 표시만 바꾼다."""

CONFIG_KO = {
    "base_currency": "기준 통화",
    "market_timezone": "시장 시간대",
    "display_timezone": "화면 시간대",
    "evaluation_mode": "평가 방식",
    "intraday_monitor": "장중 감시 방식",
    "fill_mode": "체결 기록 방식",
    "alerts_dry_run": "알림 시험발송",
    "daily_finality_grace_minutes": "일봉 확정 대기(분)",
    "quote_poll_seconds": "시세 확인 간격(초)",
    "quote_max_age_seconds": "시세 유효 기간(초)",
    "event_worker_minutes": "일정 수집 간격(분)",
    "ui_refresh_seconds": "화면 새로고침(초)",
    "risk_notice_poll_minutes": "위험공지 확인(분)",
    "source_poll_seconds": "출처 수집 간격(초)",
    "daily_poll_seconds": "일봉 수집 간격(초)",
    "scan_poll_seconds": "계산 간격(초)",
    "settlement_cadence_seconds": "정산 주기(초)",
    "event_review_ttl_hours": "검토 유효 시간",
    "http_timeout_seconds": "통신 제한 시간(초)",
    "http_max_attempts": "통신 재시도 횟수",
    "http_retry_base_seconds": "재시도 대기(초)",
    "sec_requests_per_second": "SEC 초당 요청",
    "other_source_requests_per_second": "기타출처 초당 요청",
    "max_positions": "최대 보유 수",
    "warmup_margin_sessions": "준비 봉 여유",
    "ma_periods": "이평 기간",
    "atr_period": "변동폭 기간",
    "stoch_periods": "스토캐스틱 기간",
    "rvol_period": "거래량 비율 기간",
    "adv_period": "평균거래대금 기간",
    "benchmark": "비교지수",
    "watch_horizon_calendar_days": "관찰 기간(일)",
    "entry_min_sessions_to_risk": "위험까지 최소 봉수",
    "forced_exit_buffer_sessions": "강제종료 여유 봉수",
    "pivot_left": "고저 좌측 봉수",
    "pivot_right": "고저 우측 봉수",
    "hl_buffer": "고저 여유율",
    "setup_validity_sessions": "자리 유효 봉수",
    "setup_score_threshold": "자리 점수 기준",
    "setup_dryup_max": "거래마름 상한",
    "setup_ma_spread_max": "이평 벌어짐 상한",
    "setup_slope_min": "기울기 하한",
    "stoch_oversold": "과매도 기준",
    "slope_lookback": "기울기 관찰 봉수",
    "setup_weights": "자리 가중치",
    "trigger_rvol_min": "돌파 거래량 하한",
    "trigger_close_location_min": "종가 위치 하한",
    "maximum_breakout_extension_atr": "돌파 허용 폭",
    "strength_weights": "힘 가중치",
    "strength_trend_norm": "추세 정규값",
    "strength_rs_norm": "상대강도 정규값",
    "strength_rvol_norm": "거래량 정규값",
    "rs_period": "상대강도 기간",
    "short_ma_period": "단기 이평 기간",
    "exhaustion_weights": "소진 가중치",
    "exhaustion_thresholds": "소진 단계 기준",
    "exhaustion_cumulative_targets": "소진 누적 목표",
    "exhaustion_momentum_period": "소진 탄력 기간",
    "exhaustion_momentum_min": "소진 탄력 하한",
    "exhaustion_climax_rvol": "과열 거래량",
    "exhaustion_poor_rvol": "부진 거래량",
    "exhaustion_poor_efficiency_max": "저효율 상한",
    "exhaustion_large_wick_min": "긴꼬리 하한",
    "exhaustion_close_location_max": "종가위치 상한",
    "importance_first_key_pivotal_major": "핵심 일정 중요도",
    "importance_confirmed": "확정 일정 중요도",
    "importance_routine": "일반 일정 중요도",
    "hard_stop_fraction": "손절 비율",
    "atr_stop_auto_expand_v1": "손절 자동확장",
    "risk_per_position_fraction": "종목당 위험 비율",
    "max_position_nav_fraction": "종목당 최대 비중",
    "max_issuer_fraction": "기업당 최대 비중",
    "max_sector_fraction": "분야당 최대 비중",
    "bio_pharma_mcap_usd": "바이오 시총 범위",
    "space_mcap_usd": "우주 시총 범위",
    "min_volume20": "20일 최소 거래량",
    "min_adv20_usd": "20일 최소 거래대금",
    "min_price_usd": "최소 주가",
    "rank_weights": "순위 가중치",
    "min_importance": "최소 중요도",
    "min_cash_buffer_fraction": "현금 여유 비율",
    "settlement_policy": "결제 방식",
    "allocation_expiry": "배분 유효 기간",
    "withdraw_fraction": "인출 비율",
    "settlement_cadence": "정산 주기",
    "withdrawal_mode": "인출 방식",
    "withdrawal_requires_positive_monthly_net": "이익 달에만 인출",
    "equity_floor_basis": "원금 기준",
    "fractional_lot_shares": "최소 매매 단위",
    "fee_estimate_rate": "수수료율",
    "fee_minimum": "최소 수수료(USD)",
    "slippage_estimate": "체결밀림 예상",
    "tax_reserve": "세금 적립 비율",
    "runup_db_path": "저장소 경로",
    "ma20_recovery_lookback_sessions": "이평 회복 관찰",
    "vol_dryup_short_period": "거래마름 단기",
    "vol_dryup_long_period": "거래마름 장기",
    "entry_signal_validity_sessions": "신호 유효 봉수",
    "reentry_policy": "재진입 규칙",
    "price_provider_policy": "시세 출처 규칙",
    "history_source_mix_policy": "이력 혼합 규칙",
    "quote_time_unknown_policy": "시각불명 시세 규칙",
    "quote_future_tolerance_seconds": "미래시각 허용(초)",
    "price_stale_sessions": "오래된 봉 허용",
    "http_retry_max_seconds": "재시도 최대(초)",
    "http_max_response_bytes": "최대 응답 크기",
    "http_max_redirects": "최대 이동 횟수",
    "decimal_precision": "소수 정밀도",
    "partial_sell_rounding": "분할매도 반올림",
    "feature_version": "계산 버전",
    "strategy_version": "전략 버전",
    "runup_worker_enabled": "자동 수집 켜기",
    "sqlite_busy_timeout_ms": "DB 대기 시간",
    "sqlite_max_attempts": "DB 재시도 횟수",
    "worker_lease_seconds": "작업 임대 시간",
    "worker_heartbeat_seconds": "작업 신호 간격",
    "source_health_max_age_hours": "출처 상태 유효",
    "alert_max_attempts": "알림 재시도 횟수",
    "alert_retry_base_seconds": "알림 재시도 대기",
    "source_batch_size": "수집 묶음 크기",
    "job_budget_seconds": "작업 시간 예산",
}

STATUS_KO = {
    "OK": "정상",
    "PARTIAL": "일부 완료",
    "EMPTY_CONFIRMED": "비어 있음 확인",
    "FAILED": "실패",
    "PROPOSED": "제안됨",
    "REVIEW": "검토 필요",
    "REVIEWED": "검토됨",
    "APPROVED": "승인됨",
    "REJECTED": "거부됨",
    "APPLIED": "적용됨",
    "REPLAY": "재확인됨",
    "CONFIRMED": "확정됨",
    "CANCELLED": "취소됨",
    "RESERVED": "예약됨",
    "OPEN": "보유 중",
    "CLOSED": "종료됨",
    "HOLD": "유지",
    "WATCH": "관찰",
    "WATCH_ONLY": "관찰만",
    "SETUP": "자리 잡힘",
    "UNAVAILABLE": "자료 없음",
    "PENDING": "대기 중",
    "CONFIG_REQUIRED": "설정 입력 필요",
    "NEEDS_INPUT": "입력 필요",
    "SCAN_NOT_RUN": "계산 안 함",
    "PROFILE_REQUIRED": "설정 없음",
    "QUOTE_UNKNOWN": "시세 없음",
    "QUOTE_ONLY": "시세만",
    "FRESH": "신선함",
    "STALE": "오래됨",
    "UNKNOWN": "모름",
    "DISABLED": "꺼짐",
    "RUNNING": "동작 중",
    "STOPPED": "멈춤",
    "STALE_WORKER": "신호 끊김",
    "EMPTY": "비어 있음",
    "FULL": "전량",
    "SELL": "매도",
    "BUY": "매수",
}


def ko_config(key: str) -> str:
    return CONFIG_KO.get(key, key)


def ko_status(value) -> str:
    text = str(value or "")
    return STATUS_KO.get(text, STATUS_KO.get(text.upper(), text))


def ko_table(rows: list, columns: dict) -> list:
    """표시용 행 목록. columns={원래키: 한글제목}, 값 상태는 한글 변환."""
    out = []
    for row in rows:
        shown = {}
        for key, title in columns.items():
            value = row.get(key, "")
            if key in ("status", "review_status", "health") or key.endswith("_status"):
                value = ko_status(value)
            shown[title] = value() if callable(value) else value
        out.append(shown)
    return out


KEY_KO = {
    "ticker": "티커",
    "exchange": "거래소",
    "standard_exchange": "표준 거래소",
    "listing_status": "상장 상태",
    "source_id": "출처",
    "status": "상태",
    "review_status": "검토 상태",
    "health": "건강",
    "event_id": "이벤트",
    "event_type": "유형",
    "date_precision": "날짜 정밀도",
    "start": "시작",
    "tickers": "티커들",
    "document_urls": "문서",
    "revisions": "변경 횟수",
    "reviewed_by": "검토자",
    "candidate_id": "후보",
    "title": "제목",
    "source": "출처",
    "source_url": "출처 주소",
    "date": "날짜",
    "available": "확인 시각",
    "security_id": "종목",
    "qty": "수량",
    "overlay_price": "최근 시세",
    "received": "수신 시각",
    "trade": "체결 시각",
    "daily_exit": "당일 판단",
    "daily_target": "당일 목표",
    "risk_note": "위험 안내",
    "company": "기업",
    "sector": "분야",
    "events": "이벤트 수",
    "nearest_event": "가까운 일정",
    "dday": "디데이",
    "scope": "범위",
    "capability": "수집 방식",
    "coverage": "적용 범위",
    "last_success": "마지막 성공",
    "last_error": "마지막 오류",
    "revision": "개정",
    "market_cap": "시총",
    "as_of": "기준 시각",
    "available_at": "확인 시각",
    "budget": "예산",
    "reservation_usd": "예약금(USD)",
    "entry_reference": "진입 기준",
    "expires_at": "만료 시각",
    "decision_id": "판정",
    "config_hash": "설정",
    "command_id": "제출",
    "settled_cash": "결제 현금",
    "unsettled_cash": "미결제",
    "realized_total": "누적 실현손익",
}


def ko_row(row: dict) -> dict:
    """행 키를 한글로. 모르는 키·값 구조는 그대로 둔다."""
    shown = {}
    for key, value in dict(row).items():
        title = KEY_KO.get(key, key)
        if key in ("status", "review_status", "health") or key.endswith("_status"):
            value = ko_status(value)
        shown[title] = value
    return shown


SIDE_KO = {"BUY": "매수", "SELL": "매도"}
FLOW_KO = {"DEPOSIT": "입금", "CAPITAL_WITHDRAWAL": "원금 출금",
           "PROFIT_WITHDRAWAL": "이익 출금", "TAX_WITHDRAWAL": "세금 출금"}
EXIT_KO = {"HOLD": "유지", "PARTIAL": "일부 매도", "FULL": "전량 매도", "REVIEW": "검토"}


def from_ko(mapping: dict, label: str) -> str:
    for key, title in mapping.items():
        if title == label:
            return key
    return label
