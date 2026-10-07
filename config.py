"""User-tunable scanner and validation settings.

Rates are fractions.  Changing a value creates a new strategy version and never
retroactively changes a stored backtest result.
"""

# Qualification targets
TARGET1_HIT_RATE_GOAL = 0.80
TARGET1_HIT_RATE_FLOOR = 0.70
# User-approved Oct 2 contract; never rewrite historical five-symbol metrics.
MIN_UNIQUE_ENTRIES_PER_SESSION = 2
TARGET_ACTIONABLE_SYMBOLS = 3
ACCESS_CRITERIA_VERSION = "2026-10-02-min2-target3"
LIVE_DISPLAY_MIN_SYMBOLS = 2
LIVE_DISPLAY_DEFAULT_SYMBOLS = 3
LIVE_DISPLAY_MAX_SYMBOLS = 10
# Display/universe views only; never weaken entry, cost or risk gates.
US_DISPLAY_MIN_PRICE = 0.01
US_PENNY_PRICE_CEILING = 1.0  # Exclusive: dedicated sub-dollar view.
DISPLAY_SURGE_MIN_CHANGE_PCT = 7.0
KR_DISPLAY_SURGE_MAX_CHANGE_PCT = 20.0
MIN_TRADES_PER_MARKET = 50
BACKTEST_LOOKBACK_DAYS = 60
BACKTEST_MIN_DAYS = 2
BACKTEST_MAX_DAYS = 60
BACKTEST_DEFAULT_TOP_N = 30
BACKTEST_MIN_TOP_N = 5
BACKTEST_MAX_TOP_N = 100

# Common causal execution/risk contract
ENTRY_VALID_BARS = 3
ENTRY_MAX_PREMIUM_ATR = 0.25
TARGET1_POSITION_WEIGHT = 0.50
MAX_HARD_STOP_LOSS_PCT = 0.015
MINIMUM_NET_RR = 1.0

# Explicit cost assumptions; replace with verified account values when known.
KR_BUY_FEE = 0.00015
KR_SELL_FEE = 0.00015
KR_SELL_TAX_RESERVE = 0.002
KR_SLIPPAGE = 0.001
US_BUY_FEE = 0.0025
US_SELL_FEE = 0.0025
US_SELL_LEVY_RESERVE = 0.0001
US_REGULAR_SLIPPAGE = 0.001
US_EXTENDED_SLIPPAGE = 0.002

# Data sufficiency and live cadence
WARMUP_BARS = 900
STRUCTURAL_WINDOW_BARS = 3000
STRUCTURAL_CONTEXT_SEED_BARS = 900
HISTORY_INITIAL_READY_BARS = 180
HISTORY_WARM_TARGET_BARS = 3000
HISTORY_WARMUP_QUEUE_LIMIT = 4
# Provisional evaluation floor for every session: at least the 1-minute
# scalping minimum. Below this, not even provisional analysis opens.
PROVISIONAL_MIN_BARS = 30
# Fresh ranking discovery is reused this long; rotation still advances over
# the cached pool while no KIS ranking call is spent.
DISCOVERY_CACHE_SECONDS = 180
# KIS documented five-minute gainers: one page per US exchange, regular only.
# Discovery allocation, not an entry-gate relaxation or a proven alpha factor.
US_MOMENTUM_DISCOVERY_ENABLED = True
US_MOMENTUM_DISCOVERY_SOURCE = "KIS 5분 가격급등"
US_MOMENTUM_DISCOVERY_MINX = "3"
US_MOMENTUM_DISCOVERY_ROWS = 20
US_MOMENTUM_ANALYSIS_RESERVE = 20
# Background warmup scheduling yields for a cycle once KIS calls pass this.
# Official evaluation is never throttled; tune after October metering.
KIS_CYCLE_SOFT_BUDGET_CALLS = 400
# Provider readings are separate from estimated KIS response bytes. No secrets.
RESOURCE_USAGE_PATH = ".scanner_data/provider-usage.json"
RESOURCE_USAGE_MAX_AGE_SECONDS = 3600
RESOURCE_USAGE_MAX_BYTES = 65536
# Conservative internal targets, NOT account entitlement assertions.
# key: (unit, scope kind, internal target, cumulative monthly counter)
RESOURCE_BUDGETS = {
    "render_egress": ("bytes", "workspace", 3_000_000_000, True),
    "render_hours": ("hours", "workspace", 600, True),
    "render_build": ("minutes", "workspace", 350, True),
    "cockroach_ru": ("RU", "cluster", 35_000_000, True),
    "cockroach_storage": ("bytes", "cluster", 7 * 1024**3, False),
}
KIS_RATE_LIMIT_COOLDOWN_SECONDS = 61
# UI status polling is not a market-data clock.  Keep state-only websocket
# deltas slow enough to avoid turning an idle browser tab into egress load.
UI_STATUS_POLL_SECONDS = 30
# Fast quotes are independent of the closed-bar engine and heavy detail cards.
LIVE_QUOTE_REFRESH_OPTIONS_SECONDS = (1, 3, 5)
LIVE_REST_QUOTE_REFRESH_SECONDS = 1
LIVE_REST_QUOTE_MAX_AGE_SECONDS = 5
LIVE_DETAIL_REFRESH_SECONDS = 30
# Paper outcomes are resolved from closed one-minute bars, not sampled quotes.
# Rotate a bounded batch once per minute so many open paper cases cannot fan
# out into an unbounded burst of REST requests.
VALIDATION_TRACKING_REFRESH_SECONDS = 60
VALIDATION_CASES_PER_REFRESH = 12
BACKTEST_MAX_STORED_BARS = 32000
DURABLE_PREFETCH_SYMBOLS_PER_QUERY = 20
# Bound SQL fan-out and repeated 32k-row retention scans. Between successful
# sweeps, fewer than WRITE_ROWS additional rows can accumulate per symbol.
DURABLE_UPSERT_BATCH_ROWS = 250
DURABLE_RETENTION_INTERVAL_SECONDS = 3600
DURABLE_RETENTION_WRITE_ROWS = 1000
SCANNER_CYCLE_SECONDS = 60.0
DAY_FLOW_TIMEFRAME_MINUTES = 30
SCANNER_REQUEST_DEADLINE_MARGIN_SECONDS = 8.0
CANDIDATE_SNAPSHOT_INTERVAL_SECONDS = 300
CANDIDATE_FALLBACK_MAX_AGE_SECONDS = 8 * 60 * 60
MAX_COMPLETED_BAR_AGE_SECONDS = 600.0

# Rejection-only causal strengthening gates
BULLISH_CLOSE_LOCATION_MIN = 0.65
RELATIVE_VOLUME_MIN = 1.25
NOISE_DISTANCE_MIN_ATR = 0.50
TARGET_DISTANCE_MAX_ATR = 6.0
TARGET_DISTANCE_STRICT_MAX_ATR = 4.0

# Walk-forward probability model (features are observed at signal time only)
PROBABILITY_MIN_TRAINING_TRADES = 30
PROBABILITY_L2_PENALTY = 1.0
PROBABILITY_MODEL_VERSION = "causal-logit-v2-resolved"
PROBABILITY_FEATURE_FIELDS = (
    "score",
    "persistence",
    "evidence_confidence",
    "pattern_fatigue",
    "net_swing_pct",
    "atr_pct",
    "volume_ratio_3m",
    "volatility_z",
    "trend_persistence",
    "move_capacity_ratio",
)

# User-approved Oct2 expansion. Existing six retain arbitration precedence.
# Activation enables detection, not a claim of qualified T1 performance.
ACTIVE_STRATEGY_VALUES = (
    "박스권 반등",
    "급등 후 눌림",
    "과매도 반등",
    "가격강도 선도주 눌림 재개",
    "유동성 스윕 후 회복",
    "전일 고가 돌파 후 재지지",
    "상승추세",
    "눌림목",
    "거래량 돌파",
    "VWAP 회복",
    "변동성 수축 후 확장",
    "저점 이탈 후 회복",
    "하락쐐기 상단 돌파",
    "저거래량 1-2-3 반전",
    "불플래그 돌파",
    "VWAP 지지 반등",
    "시가 회복 반전",
    "상승갭 재지지",
    "인사이드바 돌파",
)

# Strategies with an explicit early-session entry deadline are diagnostic only.
# They cannot enter the production portfolio because the scanner must work at
# any access time while the selected market session is open.
NARROW_TIME_STRATEGY_VALUES = (
    "개장 범위 돌파 후 지지확인",
    "개장 범위 하단 반전",
    "개장 범위 직접 돌파",
)

# Minimum completed candles required by each strategy's own pattern logic.
# Omitted timeframes are deliberately not admission gates for that strategy.
STRATEGY_FRAME_REQUIREMENTS = {
    "상승추세": {15: 20, 5: 25, 3: 25},
    "눌림목": {15: 20, 5: 25, 3: 25},
    "박스권 반등": {5: 25, 3: 25},
    "거래량 돌파": {5: 25, 3: 25},
    "급등 후 눌림": {15: 20, 5: 25, 3: 25},
    "VWAP 회복": {5: 25, 3: 25},
    "과매도 반등": {5: 25, 3: 25},
    "변동성 수축 후 확장": {5: 25, 3: 25},
    "개장 범위 돌파 후 지지확인": {3: 7},
    "저점 이탈 후 회복": {3: 9},
    "개장 범위 하단 반전": {3: 7},
    "하락쐐기 상단 돌파": {15: 4, 5: 15, 3: 1},
    "저거래량 1-2-3 반전": {15: 4, 5: 12, 3: 6},
    "불플래그 돌파": {3: 12},
    "VWAP 지지 반등": {15: 4, 3: 8},
    "개장 범위 직접 돌파": {3: 7},
    "시가 회복 반전": {3: 7},
    "상승갭 재지지": {3: 7},
    "인사이드바 돌파": {15: 1, 5: 7},
    "가격강도 선도주 눌림 재개": {15: 9, 5: 14, 3: 8},
    "유동성 스윕 후 회복": {3: 10},
    "전일 고가 돌파 후 재지지": {3: 5},
}
# 1-minute scalping profiles use their own completed-bar requirements.
# Kept separate so the 22-strategy contract above never changes.
SCALP_FRAME_REQUIREMENTS = {
    "1분 눌림 진입": {1: 30},
    "1분 VWAP 회복": {1: 30},
    "1분 눌림 추매": {1: 30},
}
if len(STRATEGY_FRAME_REQUIREMENTS) != 22:
    raise RuntimeError("기법별 시간축 요구사항은 22개 전체를 포함해야 합니다")
OPENING_RANGE_READY_MINUTES = 21
OPENING_RANGE_RETEST_MAX_MINUTES = 90
OPENING_RANGE_LOW_REVERSAL_MAX_MINUTES = 75
OPENING_RANGE_BREAKOUT_MAX_MINUTES = 120


# ---------------------------------------------------------------------------
# Runup scanner central config (Step 01 V2) — DESIGN_V1_UNVALIDATED.
# 실행 가능한 초기 가설 프로필이며 최적화·승률 검증값이 아니다. VALIDATED로
# 표시하지 않는다. 모든 런업 임계값·수식 가중치·주기·수수료 가정은 이 dict 한
# 곳에서만 관리한다. runup/·UI·tests에 값을 복제하지 말고 여기서 import한다.
# V2: CONFIG_CONTRACT_V2.json의 117개 필드·기본값과 정확히 일치한다.
# ---------------------------------------------------------------------------
RUNUP_PROFILE_NAME = "DESIGN_V1_UNVALIDATED"
RUNUP_SCHEMA_VERSION = 3

RUNUP_UI_POLICY = {"writer_grant_seconds": 900}

RUNUP_CONFIG = {
    "base_currency": "USD",
    "market_timezone": "America/New_York",
    "display_timezone": "Asia/Seoul",
    "evaluation_mode": "DAILY_CLOSED",
    "intraday_monitor": "INTRADAY_RISK_MONITOR",
    "fill_mode": "MANUAL_FILLS",
    "alerts_dry_run": True,
    "daily_finality_grace_minutes": 15,
    "quote_poll_seconds": 60,
    "quote_max_age_seconds": 120,
    "event_worker_minutes": 30,
    "ui_refresh_seconds": 60,
    "risk_notice_poll_minutes": 5,
    "event_review_ttl_hours": 24,
    "http_timeout_seconds": 20,
    "http_max_attempts": 3,
    "http_retry_base_seconds": 1,
    "sec_requests_per_second": 2,
    "other_source_requests_per_second": 1,
    "max_positions": 5,
    "warmup_margin_sessions": 20,
    "ma_periods": (20, 60),
    "atr_period": 14,
    "stoch_periods": (14, 3, 3),
    "rvol_period": 20,
    "adv_period": 20,
    "benchmark": "SPY",
    "watch_horizon_calendar_days": 120,
    "entry_min_sessions_to_risk": 3,
    "forced_exit_buffer_sessions": 1,
    "pivot_left": 3,
    "pivot_right": 2,
    "hl_buffer": 0.005,
    "setup_validity_sessions": 10,
    "setup_score_threshold": 70,
    "setup_dryup_max": 0.50,
    "setup_ma_spread_max": 0.05,
    "setup_slope_min": 0,
    "stoch_oversold": 25,
    "slope_lookback": 5,
    "setup_weights": {"dryup": 25, "squeeze": 20, "ma20_recovery": 20,
                      "positive_slope": 10, "higher_low": 20, "stoch_turn": 5},
    "trigger_rvol_min": 1.5,
    "trigger_close_location_min": 0.70,
    "maximum_breakout_extension_atr": 1,
    "strength_weights": {"trend": 30, "structure": 25,
                         "relative_strength": 25, "volume_efficiency": 20},
    "strength_trend_norm": 0.005,
    "strength_rs_norm": 0.10,
    "strength_rvol_norm": 3,
    "rs_period": 20,
    "short_ma_period": 10,
    "exhaustion_weights": {"rapid_gain_then_deceleration": 12.5,
                           "climax_volume": 12.5,
                           "large_upper_wick": 12.5,
                           "failed_breakout": 12.5,
                           "poor_efficiency_with_high_rvol": 12.5,
                           "weakening_rs": 12.5,
                           "short_ma_loss": 12.5,
                           "structure_break": 12.5},
    "exhaustion_thresholds": (35, 55, 75),
    "exhaustion_cumulative_targets": (0.25, 0.50, 1.0),
    "exhaustion_momentum_period": 5,
    "exhaustion_momentum_min": 0.20,
    "exhaustion_climax_rvol": 3,
    "exhaustion_poor_rvol": 2.5,
    "exhaustion_poor_efficiency_max": 0.20,
    "exhaustion_large_wick_min": 0.40,
    "exhaustion_close_location_max": 0.50,
    "importance_first_key_pivotal_major": 100,
    "importance_confirmed": 75,
    "importance_routine": 25,
    "hard_stop_fraction": 0.08,
    "atr_stop_auto_expand_v1": False,
    "risk_per_position_fraction": 0.005,
    "max_position_nav_fraction": 0.20,
    "max_issuer_fraction": 0.20,
    "max_sector_fraction": 0.60,
    "bio_pharma_mcap_usd": (300_000_000, 5_000_000_000),
    "space_mcap_usd": (300_000_000, None),
    "min_volume20": 300_000,
    "min_adv20_usd": 1_000_000,
    "min_price_usd": 1,
    "rank_weights": {"strength": 0.5, "setup": 0.3, "importance": 0.2},
    "min_importance": 50,
    "min_cash_buffer_fraction": 0.10,
    "settlement_policy": "MANUAL_CONFIRMED",
    "allocation_expiry": "next_session_close",
    "withdraw_fraction": 0.50,
    "settlement_cadence": "monthly",
    "withdrawal_mode": "realized-carryforward",
    "withdrawal_requires_positive_monthly_net": True,
    "equity_floor_basis": "net capital contributions",
    "fractional_lot_shares": 1,
    # 비용 추정치: 사용자 broker 조건 입력 전 None. 0으로 가정하지 않으며
    # 신규 allocation은 CONFIG_REQUIRED다. 키 자체가 없으면 오류다.
    "fee_estimate_rate": None,
    # Worker poll intervals (seconds)
    "source_poll_seconds": 300,
    "daily_poll_seconds": 86400,
    "scan_poll_seconds": 86400,
    "settlement_cadence_seconds": 86400,
    "fee_minimum": None,
    "slippage_estimate": None,
    "tax_reserve": None,
    "runup_db_path": ".scanner_data/runup/runup.sqlite3",
    "ma20_recovery_lookback_sessions": 10,
    "vol_dryup_short_period": 5,
    "vol_dryup_long_period": 20,
    "entry_signal_validity_sessions": 1,
    "reentry_policy": "NEW_SETUP_AND_NEW_BREAKOUT_ONLY",
    "price_provider_policy": "KIS_PRIMARY_EXPLICIT_FALLBACK",
    "history_source_mix_policy": "NO_MIX",
    "quote_time_unknown_policy": "WARN_AND_BLOCK_ALLOCATION",
    "quote_future_tolerance_seconds": 5,
    "price_stale_sessions": 0,
    "http_retry_max_seconds": 30,
    "http_max_response_bytes": 10485760,
    "http_max_redirects": 3,
    "decimal_precision": 34,
    "partial_sell_rounding": "FLOOR_TO_LOT",
    "feature_version": "runup-features-v2",
    "strategy_version": "runup-strategy-v2",
    "runup_worker_enabled": False,
    "sqlite_busy_timeout_ms": 5000,
    "sqlite_max_attempts": 3,
    "worker_lease_seconds": 120,
    "worker_heartbeat_seconds": 30,
    "source_health_max_age_hours": 24,
    "alert_max_attempts": 3,
    "alert_retry_base_seconds": 5,
    "source_batch_size": 100,
    "job_budget_seconds": 30,
}


# ---------------------------------------------------------------------------
# 중앙 schema (Step 01 V2). CONFIG_CONTRACT_V2.json의 kind/nullable/
# required/bounds/components/default/unit을 완전히 표현한다. validator는
# 이 schema를 실제로 해석하여 전 key를 검사한다. 수동 이중 목록을 두지 않는다.
# ---------------------------------------------------------------------------
RUNUP_SCHEMA = {
    "base_currency": {"required": True, "nullable": False, "kind": "enum",
                      "unit": "currency", "default": "USD", "values": ["USD"]},
    "market_timezone": {"required": True, "nullable": False, "kind": "string",
                        "unit": "tz", "default": "America/New_York",
                        "nonempty": True, "format": "iana_timezone"},
    "display_timezone": {"required": True, "nullable": False, "kind": "string",
                         "unit": "tz", "default": "Asia/Seoul",
                         "nonempty": True, "format": "iana_timezone"},
    "evaluation_mode": {"required": True, "nullable": False, "kind": "enum",
                        "unit": "mode", "default": "DAILY_CLOSED",
                        "values": ["DAILY_CLOSED"]},
    "intraday_monitor": {"required": True, "nullable": False, "kind": "enum",
                         "unit": "mode", "default": "INTRADAY_RISK_MONITOR",
                         "values": ["INTRADAY_RISK_MONITOR"]},
    "fill_mode": {"required": True, "nullable": False, "kind": "enum",
                   "unit": "mode", "default": "MANUAL_FILLS",
                   "values": ["MANUAL_FILLS"]},
    "alerts_dry_run": {"required": True, "nullable": False,
                        "kind": "boolean", "unit": "flag", "default": True,
                        "must_be": True},
    "daily_finality_grace_minutes": {"required": True, "nullable": False,
                                     "kind": "integer", "unit": "minutes",
                                     "default": 15, "minimum": 1},
    "quote_poll_seconds": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "seconds",
                            "default": 60, "minimum": 1},
    "quote_max_age_seconds": {"required": True, "nullable": False,
                               "kind": "integer", "unit": "seconds",
                               "default": 120, "minimum": 1},
    "event_worker_minutes": {"required": True, "nullable": False,
                              "kind": "integer", "unit": "minutes",
                              "default": 30, "minimum": 1},
    "ui_refresh_seconds": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "seconds",
                            "default": 60, "minimum": 1},
    "risk_notice_poll_minutes": {"required": True, "nullable": False,
                                  "kind": "integer", "unit": "minutes",
                                  "default": 5, "minimum": 1},
    "source_poll_seconds": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "seconds",
                            "default": 300, "minimum": 1},
    "daily_poll_seconds": {"required": True, "nullable": False,
                           "kind": "integer", "unit": "seconds",
                           "default": 86400, "minimum": 1},
    "scan_poll_seconds": {"required": True, "nullable": False,
                          "kind": "integer", "unit": "seconds",
                          "default": 86400, "minimum": 1},
    "settlement_cadence_seconds": {"required": True, "nullable": False,
                                   "kind": "integer", "unit": "seconds",
                                   "default": 86400, "minimum": 1},
    "event_review_ttl_hours": {"required": True, "nullable": False,
                                "kind": "integer", "unit": "hours",
                                "default": 24, "minimum": 1},
    "http_timeout_seconds": {"required": True, "nullable": False,
                              "kind": "integer", "unit": "seconds",
                              "default": 20, "minimum": 1},
    "http_max_attempts": {"required": True, "nullable": False,
                           "kind": "integer", "unit": "count",
                           "default": 3, "minimum": 1},
    "http_retry_base_seconds": {"required": True, "nullable": False,
                                 "kind": "number", "unit": "seconds",
                                 "default": 1, "exclusive_minimum": 0},
    "sec_requests_per_second": {"required": True, "nullable": False,
                                 "kind": "number", "unit": "req/s",
                                 "default": 2, "exclusive_minimum": 0,
                                 "maximum": 10},
    "other_source_requests_per_second": {"required": True, "nullable": False,
                                          "kind": "number", "unit": "req/s",
                                          "default": 1,
                                          "exclusive_minimum": 0},
    "max_positions": {"required": True, "nullable": False, "kind": "integer",
                       "unit": "slots", "default": 5, "minimum": 1},
    "warmup_margin_sessions": {"required": True, "nullable": False,
                                "kind": "integer", "unit": "sessions",
                                "default": 20, "minimum": 1},
    "ma_periods": {"required": True, "nullable": False,
                    "kind": "integer_tuple", "unit": "bars",
                    "default": [20, 60], "length": 2, "item_minimum": 1,
                    "strict_ascending": True},
    "atr_period": {"required": True, "nullable": False, "kind": "integer",
                    "unit": "bars", "default": 14, "minimum": 1},
    "stoch_periods": {"required": True, "nullable": False,
                       "kind": "integer_tuple", "unit": "bars",
                       "default": [14, 3, 3], "length": 3, "item_minimum": 1,
                       "strict_ascending": False},
    "rvol_period": {"required": True, "nullable": False, "kind": "integer",
                     "unit": "bars", "default": 20, "minimum": 1},
    "adv_period": {"required": True, "nullable": False, "kind": "integer",
                    "unit": "bars", "default": 20, "minimum": 1},
    "benchmark": {"required": True, "nullable": False, "kind": "string",
                   "unit": "symbol", "default": "SPY", "nonempty": True,
                   "format": "us_security_symbol"},
    "watch_horizon_calendar_days": {"required": True, "nullable": False,
                                     "kind": "integer",
                                     "unit": "calendar-days", "default": 120,
                                     "minimum": 1},
    "entry_min_sessions_to_risk": {"required": True, "nullable": False,
                                    "kind": "integer", "unit": "sessions",
                                    "default": 3, "minimum": 1},
    "forced_exit_buffer_sessions": {"required": True, "nullable": False,
                                     "kind": "integer", "unit": "sessions",
                                     "default": 1, "minimum": 1},
    "pivot_left": {"required": True, "nullable": False, "kind": "integer",
                    "unit": "bars", "default": 3, "minimum": 1},
    "pivot_right": {"required": True, "nullable": False, "kind": "integer",
                     "unit": "bars", "default": 2, "minimum": 1},
    "hl_buffer": {"required": True, "nullable": False, "kind": "number",
                   "unit": "ratio", "default": 0.005, "minimum": 0,
                   "maximum": 1},
    "setup_validity_sessions": {"required": True, "nullable": False,
                                 "kind": "integer", "unit": "sessions",
                                 "default": 10, "minimum": 1},
    "setup_score_threshold": {"required": True, "nullable": False,
                               "kind": "number", "unit": "points",
                               "default": 70, "minimum": 0, "maximum": 100},
    "setup_dryup_max": {"required": True, "nullable": False,
                         "kind": "number", "unit": "ratio", "default": 0.5,
                         "minimum": 0},
    "setup_ma_spread_max": {"required": True, "nullable": False,
                             "kind": "number", "unit": "ratio",
                             "default": 0.05, "minimum": 0},
    "setup_slope_min": {"required": True, "nullable": False,
                         "kind": "number", "unit": "ratio/session",
                         "default": 0},
    "stoch_oversold": {"required": True, "nullable": False, "kind": "number",
                        "unit": "points", "default": 25, "minimum": 0,
                        "maximum": 100},
    "slope_lookback": {"required": True, "nullable": False,
                        "kind": "integer", "unit": "sessions", "default": 5,
                        "minimum": 1},
    "setup_weights": {"required": True, "nullable": False,
                       "kind": "weight_map", "unit": "points",
                       "default": {"dryup": 25, "higher_low": 20,
                                   "ma20_recovery": 20, "positive_slope": 10,
                                   "squeeze": 20, "stoch_turn": 5},
                       "components": ["dryup", "higher_low", "ma20_recovery",
                                      "positive_slope", "squeeze",
                                      "stoch_turn"],
                       "minimum": 0, "finite_sum": True, "positive_sum": True,
                       "exact_components": True},
    "trigger_rvol_min": {"required": True, "nullable": False,
                          "kind": "number", "unit": "ratio", "default": 1.5,
                          "exclusive_minimum": 0},
    "trigger_close_location_min": {"required": True, "nullable": False,
                                    "kind": "number", "unit": "ratio",
                                    "default": 0.7, "minimum": 0,
                                    "maximum": 1},
    "maximum_breakout_extension_atr": {"required": True, "nullable": False,
                                        "kind": "number", "unit": "ATR",
                                        "default": 1, "exclusive_minimum": 0},
    "strength_weights": {"required": True, "nullable": False,
                          "kind": "weight_map", "unit": "points",
                          "default": {"relative_strength": 25,
                                      "structure": 25, "trend": 30,
                                      "volume_efficiency": 20},
                          "components": ["relative_strength", "structure",
                                         "trend", "volume_efficiency"],
                          "minimum": 0, "finite_sum": True,
                          "positive_sum": True, "exact_components": True},
    "strength_trend_norm": {"required": True, "nullable": False,
                             "kind": "number", "unit": "slope",
                             "default": 0.005, "exclusive_minimum": 0},
    "strength_rs_norm": {"required": True, "nullable": False,
                          "kind": "number", "unit": "return",
                          "default": 0.1, "exclusive_minimum": 0},
    "strength_rvol_norm": {"required": True, "nullable": False,
                            "kind": "number", "unit": "ratio", "default": 3,
                            "exclusive_minimum": 0},
    "rs_period": {"required": True, "nullable": False, "kind": "integer",
                   "unit": "bars", "default": 20, "minimum": 1},
    "short_ma_period": {"required": True, "nullable": False,
                         "kind": "integer", "unit": "bars", "default": 10,
                         "minimum": 1},
    "exhaustion_weights": {"required": True, "nullable": False,
                            "kind": "weight_map", "unit": "points",
                            "default": {"climax_volume": 12.5,
                                        "failed_breakout": 12.5,
                                        "large_upper_wick": 12.5,
                                        "poor_efficiency_with_high_rvol": 12.5,
                                        "rapid_gain_then_deceleration": 12.5,
                                        "short_ma_loss": 12.5,
                                        "structure_break": 12.5,
                                        "weakening_rs": 12.5},
                            "components": ["climax_volume", "failed_breakout",
                                           "large_upper_wick",
                                           "poor_efficiency_with_high_rvol",
                                           "rapid_gain_then_deceleration",
                                           "short_ma_loss", "structure_break",
                                           "weakening_rs"],
                            "minimum": 0, "finite_sum": True,
                            "positive_sum": True, "exact_components": True},
    "exhaustion_thresholds": {"required": True, "nullable": False,
                               "kind": "number_tuple", "unit": "scores",
                               "default": [35, 55, 75], "length": 3,
                               "minimum": 0, "maximum": 100,
                               "strict_ascending": True},
    "exhaustion_cumulative_targets": {"required": True, "nullable": False,
                                       "kind": "number_tuple",
                                       "unit": "fractions",
                                       "default": [0.25, 0.5, 1.0],
                                       "length": 3, "exclusive_minimum": 0,
                                       "maximum": 1, "strict_ascending": True,
                                       "last_equals": 1},
    "exhaustion_momentum_period": {"required": True, "nullable": False,
                                    "kind": "integer", "unit": "sessions",
                                    "default": 5, "minimum": 1},
    "exhaustion_momentum_min": {"required": True, "nullable": False,
                                 "kind": "number", "unit": "return",
                                 "default": 0.2, "exclusive_minimum": 0},
    "exhaustion_climax_rvol": {"required": True, "nullable": False,
                                "kind": "number", "unit": "ratio",
                                "default": 3, "exclusive_minimum": 0},
    "exhaustion_poor_rvol": {"required": True, "nullable": False,
                              "kind": "number", "unit": "ratio",
                              "default": 2.5, "exclusive_minimum": 0},
    "exhaustion_poor_efficiency_max": {"required": True, "nullable": False,
                                        "kind": "number",
                                        "unit": "ATR-multiple",
                                        "default": 0.2,
                                        "exclusive_minimum": 0},
    "exhaustion_large_wick_min": {"required": True, "nullable": False,
                                   "kind": "number", "unit": "ratio",
                                   "default": 0.4, "minimum": 0,
                                   "maximum": 1},
    "exhaustion_close_location_max": {"required": True, "nullable": False,
                                       "kind": "number", "unit": "ratio",
                                       "default": 0.5, "minimum": 0,
                                       "maximum": 1},
    "importance_first_key_pivotal_major": {"required": True,
                                            "nullable": False,
                                            "kind": "number",
                                            "unit": "points", "default": 100,
                                            "minimum": 0, "maximum": 100},
    "importance_confirmed": {"required": True, "nullable": False,
                              "kind": "number", "unit": "points",
                              "default": 75, "minimum": 0, "maximum": 100},
    "importance_routine": {"required": True, "nullable": False,
                            "kind": "number", "unit": "points",
                            "default": 25, "minimum": 0, "maximum": 100},
    "hard_stop_fraction": {"required": True, "nullable": False,
                             "kind": "number", "unit": "ratio",
                             "default": 0.08, "minimum": 0, "maximum": 1,
                             "exclusive_minimum": 0, "exclusive_maximum": 1},
    "atr_stop_auto_expand_v1": {"required": True, "nullable": False,
                                 "kind": "boolean", "unit": "flag",
                                 "default": False, "must_be": False},
    "risk_per_position_fraction": {"required": True, "nullable": False,
                                    "kind": "number", "unit": "NAV",
                                    "default": 0.005, "minimum": 0,
                                    "maximum": 1},
    "max_position_nav_fraction": {"required": True, "nullable": False,
                                   "kind": "number", "unit": "NAV",
                                   "default": 0.2, "minimum": 0,
                                   "maximum": 1},
    "max_issuer_fraction": {"required": True, "nullable": False,
                             "kind": "number", "unit": "NAV",
                             "default": 0.2, "minimum": 0, "maximum": 1},
    "max_sector_fraction": {"required": True, "nullable": False,
                             "kind": "number", "unit": "NAV",
                             "default": 0.6, "minimum": 0, "maximum": 1},
    "bio_pharma_mcap_usd": {"required": True, "nullable": False,
                             "kind": "bounds_tuple", "unit": "USD",
                             "default": [300000000, 5000000000], "length": 2,
                             "exclusive_minimum": 0, "upper_nullable": False,
                             "ordered": True},
    "space_mcap_usd": {"required": True, "nullable": False,
                        "kind": "bounds_tuple", "unit": "USD",
                        "default": [300000000, None], "length": 2,
                        "exclusive_minimum": 0, "upper_nullable": True,
                        "ordered": True},
    "min_volume20": {"required": True, "nullable": False, "kind": "integer",
                      "unit": "shares", "default": 300000, "minimum": 1},
    "min_adv20_usd": {"required": True, "nullable": False, "kind": "number",
                       "unit": "USD", "default": 1000000,
                       "exclusive_minimum": 0},
    "min_price_usd": {"required": True, "nullable": False, "kind": "number",
                       "unit": "USD", "default": 1, "exclusive_minimum": 0},
    "rank_weights": {"required": True, "nullable": False,
                      "kind": "weight_map", "unit": "weight",
                      "default": {"importance": 0.2, "setup": 0.3,
                                  "strength": 0.5},
                      "components": ["importance", "setup", "strength"],
                      "minimum": 0, "finite_sum": True, "positive_sum": True,
                      "exact_components": True},
    "min_importance": {"required": True, "nullable": False, "kind": "number",
                        "unit": "points", "default": 50, "minimum": 0,
                        "maximum": 100},
    "min_cash_buffer_fraction": {"required": True, "nullable": False,
                                  "kind": "number", "unit": "NAV",
                                  "default": 0.1, "minimum": 0,
                                  "maximum": 1},
    "settlement_policy": {"required": True, "nullable": False,
                           "kind": "enum", "unit": "policy",
                           "default": "MANUAL_CONFIRMED",
                           "values": ["MANUAL_CONFIRMED"]},
    "allocation_expiry": {"required": True, "nullable": False,
                           "kind": "enum", "unit": "session",
                           "default": "next_session_close",
                           "values": ["next_session_close"]},
    "withdraw_fraction": {"required": True, "nullable": False,
                           "kind": "number", "unit": "profit",
                           "default": 0.5, "minimum": 0, "maximum": 1},
    "settlement_cadence": {"required": True, "nullable": False,
                            "kind": "enum", "unit": "cadence",
                            "default": "monthly", "values": ["monthly"]},
    "withdrawal_mode": {"required": True, "nullable": False, "kind": "enum",
                         "unit": "mode",
                         "default": "realized-carryforward",
                         "values": ["realized-carryforward"]},
    "withdrawal_requires_positive_monthly_net": {"required": True,
                                                  "nullable": False,
                                                  "kind": "boolean",
                                                  "unit": "flag",
                                                  "default": True},
    "equity_floor_basis": {"required": True, "nullable": False,
                            "kind": "enum", "unit": "basis",
                            "default": "net capital contributions",
                            "values": ["net capital contributions"]},
    "fractional_lot_shares": {"required": True, "nullable": False,
                               "kind": "integer", "unit": "shares",
                               "default": 1, "minimum": 1},
    "fee_estimate_rate": {"required": True, "nullable": True,
                           "kind": "number", "unit": "ratio", "default": None,
                           "minimum": 0, "maximum": 1},
    "fee_minimum": {"required": True, "nullable": True, "kind": "number",
                     "unit": "currency", "default": None, "minimum": 0},
    "slippage_estimate": {"required": True, "nullable": True,
                           "kind": "number", "unit": "ratio", "default": None,
                           "minimum": 0, "maximum": 1},
    "tax_reserve": {"required": True, "nullable": True, "kind": "number",
                     "unit": "ratio", "default": None, "minimum": 0,
                     "maximum": 1},
    "runup_db_path": {"required": True, "nullable": False, "kind": "path",
                       "unit": "path",
                       "default": ".scanner_data/runup/runup.sqlite3"},
    "ma20_recovery_lookback_sessions": {"required": True, "nullable": False,
                                          "kind": "integer",
                                          "unit": "sessions", "default": 10,
                                          "minimum": 1},
    "vol_dryup_short_period": {"required": True, "nullable": False,
                                "kind": "integer", "unit": "bars",
                                "default": 5, "minimum": 1},
    "vol_dryup_long_period": {"required": True, "nullable": False,
                               "kind": "integer", "unit": "bars",
                               "default": 20, "minimum": 1},
    "entry_signal_validity_sessions": {"required": True, "nullable": False,
                                        "kind": "integer", "unit": "sessions",
                                        "default": 1, "equals": 1},
    "reentry_policy": {"required": True, "nullable": False, "kind": "enum",
                        "unit": "policy",
                        "default": "NEW_SETUP_AND_NEW_BREAKOUT_ONLY",
                        "values": ["NEW_SETUP_AND_NEW_BREAKOUT_ONLY"]},
    "price_provider_policy": {"required": True, "nullable": False,
                               "kind": "enum", "unit": "policy",
                               "default": "KIS_PRIMARY_EXPLICIT_FALLBACK",
                               "values": ["KIS_PRIMARY_EXPLICIT_FALLBACK"]},
    "history_source_mix_policy": {"required": True, "nullable": False,
                                   "kind": "enum", "unit": "policy",
                                   "default": "NO_MIX", "values": ["NO_MIX"]},
    "quote_time_unknown_policy": {"required": True, "nullable": False,
                                    "kind": "enum", "unit": "policy",
                                    "default": "WARN_AND_BLOCK_ALLOCATION",
                                    "values": ["WARN_AND_BLOCK_ALLOCATION"]},
    "quote_future_tolerance_seconds": {"required": True, "nullable": False,
                                        "kind": "integer", "unit": "seconds",
                                        "default": 5, "minimum": 0},
    "price_stale_sessions": {"required": True, "nullable": False,
                              "kind": "integer", "unit": "sessions",
                              "default": 0, "minimum": 0},
    "http_retry_max_seconds": {"required": True, "nullable": False,
                                "kind": "number", "unit": "seconds",
                                "default": 30, "exclusive_minimum": 0},
    "http_max_response_bytes": {"required": True, "nullable": False,
                                 "kind": "integer", "unit": "bytes",
                                 "default": 10485760, "minimum": 1},
    "http_max_redirects": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "count", "default": 3,
                            "minimum": 1},
    "decimal_precision": {"required": True, "nullable": False,
                           "kind": "integer", "unit": "digits",
                           "default": 34, "minimum": 28},
    "partial_sell_rounding": {"required": True, "nullable": False,
                               "kind": "enum", "unit": "policy",
                               "default": "FLOOR_TO_LOT",
                               "values": ["FLOOR_TO_LOT"]},
    "feature_version": {"required": True, "nullable": False,
                         "kind": "string", "unit": "version",
                         "default": "runup-features-v2", "nonempty": True},
    "strategy_version": {"required": True, "nullable": False,
                          "kind": "string", "unit": "version",
                          "default": "runup-strategy-v2", "nonempty": True},
    "runup_worker_enabled": {"required": True, "nullable": False,
                              "kind": "boolean", "unit": "flag",
                              "default": False},
    "sqlite_busy_timeout_ms": {"required": True, "nullable": False,
                                "kind": "integer", "unit": "milliseconds",
                                "default": 5000, "minimum": 1},
    "sqlite_max_attempts": {"required": True, "nullable": False,
                             "kind": "integer", "unit": "count",
                             "default": 3, "minimum": 1},
    "worker_lease_seconds": {"required": True, "nullable": False,
                              "kind": "integer", "unit": "seconds",
                              "default": 120, "minimum": 1},
    "worker_heartbeat_seconds": {"required": True, "nullable": False,
                                  "kind": "integer", "unit": "seconds",
                                  "default": 30, "minimum": 1},
    "source_health_max_age_hours": {"required": True, "nullable": False,
                                     "kind": "integer", "unit": "hours",
                                     "default": 24, "minimum": 1},
    "alert_max_attempts": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "count", "default": 3,
                            "minimum": 1},
    "alert_retry_base_seconds": {"required": True, "nullable": False,
                                  "kind": "number", "unit": "seconds",
                                  "default": 5, "exclusive_minimum": 0},
    "source_batch_size": {"required": True, "nullable": False,
                           "kind": "integer", "unit": "records",
                           "default": 100, "minimum": 1},
    "job_budget_seconds": {"required": True, "nullable": False,
                            "kind": "integer", "unit": "seconds",
                            "default": 30, "minimum": 1},
}


# ---------------------------------------------------------------------------
# Schema-driven validator (Step 01 V2). RUNUP_SCHEMA 한 곳을 해석한다.
# ---------------------------------------------------------------------------
_US_SECURITY_SYMBOL_RE = r"^[A-Z][A-Z0-9.\-]{0,11}$"

_RUNUP_SECRET_NAME_RES = ("env", "secret", "token", "passwd", "password",
                          "account", "kis_app")


def _runup_finite(value) -> bool:
    import math

    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _check_iana_timezone(value) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(str(value))
        return True
    except Exception:
        return False


def _check_us_symbol(value) -> bool:
    import re

    return (isinstance(value, str)
            and re.match(_US_SECURITY_SYMBOL_RE, value) is not None)


def _check_workspace_path(value) -> str | None:
    """workspace-relative nonsecret path. None이면 통과, 아니면 오류문."""
    import ntpath

    if not isinstance(value, str) or not value:
        return "runup_db_path must be a non-empty string"
    if value.startswith(("\\\\", "/", "\\")) or ntpath.isabs(value):
        return "runup_db_path must be workspace-relative (absolute 거부)"
    if len(value) > 1 and value[1] == ":":
        return "runup_db_path must be workspace-relative (drive 거부)"
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return "runup_db_path must stay inside the workspace (.. 거부)"
    lowered = value.lower()
    if any(token in lowered for token in _RUNUP_SECRET_NAME_RES):
        return "runup_db_path must not look like a secret file"
    if any(p.startswith(".") and p not in (".scanner_data",) and len(p) > 1
           and p[1:2] not in ("",) for p in ()):
        return "unreachable"
    return None


def _ordered_distinct(values) -> bool:
    try:
        as_list = list(values)
        return (as_list == sorted(as_list)
                and len(set(as_list)) == len(as_list))
    except TypeError:
        return False


def _apply_rule(key, rule, value, errors, required) -> None:
    kind = rule["kind"]
    if value is None:
        if rule.get("nullable"):
            required.append(f"{key} is None: broker input required "
                            f"(CONFIG_REQUIRED, do not assume 0)")
        else:
            errors.append(f"missing required setting: {key} (None 불가)")
        return
    if kind != "boolean" and isinstance(value, bool):
        errors.append(f"{key} must not be a bool")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{key} must be a bool")
        elif "must_be" in rule and value is not rule["must_be"]:
            errors.append(f"{key} must be {rule['must_be']} "
                          f"(V2 policy)")
    elif kind == "integer":
        if not isinstance(value, int):
            errors.append(f"{key} must be an integer")
        elif (("minimum" in rule and value < rule["minimum"])
              or ("equals" in rule and value != rule["equals"])):
            errors.append(f"{key} out of range "
                          f"(minimum={rule.get('minimum')}, "
                          f"equals={rule.get('equals')})")
    elif kind == "number":
        if not _runup_finite(value):
            errors.append(f"{key} must be a finite number")
        elif ((rule.get("minimum") is not None and value < rule["minimum"])
              or (rule.get("maximum") is not None and value > rule["maximum"])
              or (rule.get("exclusive_minimum") is not None
                  and not value > rule["exclusive_minimum"])
              or (rule.get("exclusive_maximum") is not None
                  and not value < rule["exclusive_maximum"])):
            errors.append(f"{key} out of range")
    elif kind == "enum":
        if value not in rule.get("values", []):
            errors.append(f"{key} must be one of {rule.get('values')}")
    elif kind == "string":
        if not isinstance(value, str) or (
                rule.get("nonempty") and not value):
            errors.append(f"{key} must be a non-empty string")
        elif rule.get("format") == "iana_timezone" and not _check_iana_timezone(
                value):
            errors.append(f"{key} must be a valid IANA timezone")
        elif rule.get("format") == "us_security_symbol" and not _check_us_symbol(
                value):
            errors.append(f"{key} must be a US security symbol")
    elif kind == "path":
        problem = _check_workspace_path(value)
        if problem is not None:
            errors.append(problem)
    elif kind == "weight_map":
        if not isinstance(value, dict) or not value:
            errors.append(f"{key} must be a non-empty mapping")
            return
        expected = set(rule.get("components", []))
        actual = set(value)
        try:
            missing = sorted(expected - actual, key=str)
            unknown = sorted(actual - expected, key=str)
        except TypeError:
            errors.append(f"{key} has incomparable component keys")
            return
        for name in missing:
            errors.append(f"{key} is missing required component: {name}")
        for name in unknown:
            errors.append(f"{key} has unknown component: {name}")
        total = 0.0
        for name in sorted(actual, key=str):
            weight = value[name]
            if not _runup_finite(weight) or weight < rule.get("minimum", 0):
                errors.append(f"{key}.{name} must be a finite number "
                              f">= {rule.get('minimum', 0)}")
            else:
                total += weight
        import math

        if not math.isfinite(total) or total <= 0:
            errors.append(f"{key} sum must be a finite positive number")
    elif kind in ("integer_tuple", "number_tuple", "bounds_tuple"):
        if (not isinstance(value, (list, tuple))
                or len(value) != rule.get("length")):
            errors.append(f"{key} must be a tuple of length "
                          f"{rule.get('length')}")
            return
        items = list(value)
        if kind == "integer_tuple":
            if any(not isinstance(v, int) or v < rule.get("item_minimum", 1)
                   for v in items):
                errors.append(f"{key} items must be ints "
                              f">= {rule.get('item_minimum', 1)}")
                return
            if rule.get("strict_ascending") and not _ordered_distinct(items):
                errors.append(f"{key} must be strictly ascending")
        elif kind == "number_tuple":
            if any(not _runup_finite(v) for v in items):
                errors.append(f"{key} items must be finite numbers")
                return
            if rule.get("minimum") is not None and any(
                    v < rule["minimum"] for v in items):
                errors.append(f"{key} items below minimum")
                return
            if rule.get("maximum") is not None and any(
                    v > rule["maximum"] for v in items):
                errors.append(f"{key} items above maximum")
                return
            if rule.get("exclusive_minimum") is not None and any(
                    not v > rule["exclusive_minimum"] for v in items):
                errors.append(f"{key} items must be above "
                              f"{rule['exclusive_minimum']}")
                return
            if rule.get("strict_ascending") and not _ordered_distinct(items):
                errors.append(f"{key} must be strictly ascending")
                return
            if ("last_equals" in rule
                    and items[-1] != rule["last_equals"]):
                errors.append(f"{key} last value must be "
                              f"{rule['last_equals']}")
        elif kind == "bounds_tuple":
            lower, upper = items[0], items[1]
            if (not _runup_finite(lower)
                    or not lower > rule.get("exclusive_minimum", 0)):
                errors.append(f"{key} lower bound invalid")
                return
            if upper is None:
                if not rule.get("upper_nullable"):
                    errors.append(f"{key} upper must not be None")
                return
            if not _runup_finite(upper) or upper <= 0:
                errors.append(f"{key} upper bound invalid")
                return
            if rule.get("ordered") and not lower <= upper:
                errors.append(f"{key} must satisfy lower <= upper")
    else:
        errors.append(f"{key}: unknown schema kind {kind!r}")


def _check_profile_name(profile, errors) -> None:
    if (not isinstance(profile, str) or not profile
            or not profile.endswith("UNVALIDATED")):
        errors.append("runup profile name must be a non-empty string "
                      "ending with UNVALIDATED")


def validate_runup_config(cfg=None) -> dict:
    """Schema-driven 검증. {"errors", "config_required"} 안정 순서 반환.

    입력을 변경하지 않는다. 부정 입력은 결정적 errors로 보고하며
    TypeError·OverflowError를 누출하지 않는다. 결측 비용을 0으로 바꾸지 않는다.
    """
    target = RUNUP_CONFIG if cfg is None else cfg
    errors: list = []
    required: list = []
    if not isinstance(target, dict):
        return {"errors": ["runup config must be a mapping"],
                "config_required": required}
    for unknown in sorted(set(target) - set(RUNUP_SCHEMA), key=str):
        errors.append(f"unknown runup setting: {unknown}")
    for key in sorted(RUNUP_SCHEMA):
        rule = RUNUP_SCHEMA[key]
        if key not in target:
            errors.append(f"missing required setting: {key}")
            continue
        try:
            _apply_rule(key, rule, target[key], errors, required)
        except (TypeError, OverflowError, ValueError) as exc:
            errors.append(f"{key} failed validation ({type(exc).__name__})")
    _check_profile_name(RUNUP_PROFILE_NAME, errors)
    values = target
    try:
        if ("ma_periods" in values
                and isinstance(values["ma_periods"], (list, tuple))
                and len(values["ma_periods"]) == 2
                and all(isinstance(v, int) and not isinstance(v, bool)
                        for v in values["ma_periods"])
                and not values["ma_periods"][0] < values["ma_periods"][1]):
            errors.append("ma_periods must be strictly ascending (fast < slow)")
        if all(k in values and _runup_finite(values[k])
               for k in ("vol_dryup_short_period", "vol_dryup_long_period")):
            if not (values["vol_dryup_short_period"]
                    <= values["vol_dryup_long_period"]):
                errors.append("vol_dryup_short_period must be "
                              "<= vol_dryup_long_period")
        if all(k in values and _runup_finite(values[k])
               for k in ("worker_heartbeat_seconds", "worker_lease_seconds")):
            if not (values["worker_heartbeat_seconds"]
                    < values["worker_lease_seconds"]):
                errors.append("worker_heartbeat_seconds must be "
                              "< worker_lease_seconds")
        if all(k in values and _runup_finite(values[k])
               for k in ("http_retry_base_seconds", "http_retry_max_seconds")):
            if not (values["http_retry_base_seconds"]
                    <= values["http_retry_max_seconds"]):
                errors.append("http_retry_base_seconds must be "
                              "<= http_retry_max_seconds")
        for trio in (("importance_first_key_pivotal_major",
                      "importance_confirmed", "importance_routine"),):
            if all(k in values and _runup_finite(values[k]) for k in trio):
                first, confirmed, routine = (values[k] for k in trio)
                if not first >= confirmed >= routine:
                    errors.append("importance scores must satisfy "
                                  "first >= confirmed >= routine")
    except (TypeError, OverflowError, ValueError):
        errors.append("cross-field validation failed on malformed values")
    return {"errors": errors, "config_required": required}


def _reject_nonfinite_runup(value, path="$"):
    """NaN/Infinity와 지원하지 않는 객체를 명시적으로 거부한다."""
    import math
    from collections.abc import Mapping

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, int):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_runup(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_nonfinite_runup(item, f"{path}[{index}]")
        return
    raise TypeError(f"unsupported runup config value at {path}: "
                    f"{type(value).__name__}")


def canonical_config_json(cfg=None, profile_name=None) -> str:
    """hash·resolver 공통 canonical 직렬화. {schema_version,profile_name,config}."""
    import json

    target = RUNUP_CONFIG if cfg is None else cfg
    profile = RUNUP_PROFILE_NAME if profile_name is None else profile_name
    if not isinstance(target, dict):
        raise TypeError("runup config must be a mapping")
    _check_profile_name(profile, errors := [])
    if errors:
        raise ValueError("; ".join(errors))
    _reject_nonfinite_runup(target, "$.config")
    payload = {"schema_version": RUNUP_SCHEMA_VERSION,
               "profile_name": profile, "config": target}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def runup_config_hash(cfg=None, profile_name=None) -> str:
    """Canonical sha256. 의미상 invalid 설정은 ValueError로 거부한다.

    정상 프로필의 비용 None(CONFIG_REQUIRED)은 허용한다.
    """
    import hashlib

    target = RUNUP_CONFIG if cfg is None else cfg
    verdict = validate_runup_config(target)
    if verdict["errors"]:
        raise ValueError("refusing to hash invalid runup config: "
                         + "; ".join(verdict["errors"][:5]))
    return hashlib.sha256(
        canonical_config_json(target, profile_name).encode("utf-8")
    ).hexdigest()


def _freeze_runup_value(value):
    """중첩 포함 불변 변환. dict→proxy, list→tuple, set은 거부."""
    from types import MappingProxyType

    if isinstance(value, dict):
        return MappingProxyType(
            {k: _freeze_runup_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_runup_value(v) for v in value)
    if isinstance(value, (set, frozenset)):
        raise TypeError("set은 스냅샷 값으로 쓸 수 없다")
    if isinstance(value, tuple):
        return tuple(_freeze_runup_value(v) for v in value)
    return value


def _thaw_runup_value(value):
    """codec/storage용 명시 export. 새 값을 만들고 원본은 바꾸지 않는다."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {k: _thaw_runup_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_runup_value(v) for v in value]
    return value


def resolve_config_snapshot(values, profile_name=None) -> dict:
    """완전한 사용자 값을 불변 스냅샷으로 해석. DB를 사용하지 않는다.

    values 내부는 중첩까지 freeze되며 외부 수정이 전파되지 않는다.
    codec/storage는 export_snapshot_values로 명시 thaw한다.
    """
    import copy

    if not isinstance(values, dict):
        raise TypeError("config snapshot values must be a mapping")
    profile = RUNUP_PROFILE_NAME if profile_name is None else profile_name
    verdict = validate_runup_config(values)
    if verdict["errors"]:
        raise ValueError("invalid config snapshot values: "
                         + "; ".join(verdict["errors"][:5]))
    frozen = _freeze_runup_value(copy.deepcopy(values))
    return {
        "schema_version": RUNUP_SCHEMA_VERSION,
        "profile_name": profile,
        "config_hash": runup_config_hash(values, profile),
        "values": frozen,
    }


def export_snapshot_values(snapshot) -> dict:
    """스냅샷 값의 명시 export. 새 plain 값을 반환하고 원본은 그대로 둔다."""
    if not isinstance(snapshot, dict) or "values" not in snapshot:
        raise ValueError("config snapshot이 아니다")
    return _thaw_runup_value(snapshot["values"])


# ---------------------------------------------------------------------------
# Runup source policies (Step 04 V2). 소스별 공식 근거·capability·scope와
# 공통 속도·예산 key 참조만 둔다. 숫자는 RUNUP_CONFIG에서 읽으며 복제하지
# 않는다. credential 값은 두지 않고 환경변수 이름만 참조한다. 117개 설정·
# 기본값·schema·hash 규격은 변경하지 않는다.
# ---------------------------------------------------------------------------
RUNUP_SOURCE_POLICIES = {
    "clinicaltrials": {
        "official_url": "https://clinicaltrials.gov/data-api/api",
        "capability": "SUPPORTED_MANUAL",
        "scope": "registered trial metadata",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "sec": {
        "official_url": "https://www.sec.gov/search-filings/"
                        "edgar-application-programming-interfaces",
        "capability": "SUPPORTED_MANUAL",
        "scope": "filings discovery metadata",
        "rate_key": "sec_requests_per_second",
        "official_ceiling_rps": 10,
        "contact_env": "RUNUP_SEC_USER_AGENT",
        "contact_configured": False,
    },
    "fda": {
        "official_url": "https://www.fda.gov/advisory-committees/"
                        "advisory-committee-calendar",
        "capability": "SUPPORTED_MANUAL",
        "scope": "advisory committee calendar",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "asco": {
        "official_url": "https://www.asco.org/meetings",
        "capability": "SUPPORTED_MANUAL",
        "scope": "meeting and abstract policy per year",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "aacr": {
        "official_url": "https://www.aacr.org/meeting/",
        "capability": "SUPPORTED_MANUAL",
        "scope": "abstract and data release dates per year",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "esmo": {
        "official_url": "https://www.esmo.org/meeting-calendar",
        "capability": "SUPPORTED_MANUAL",
        "scope": "meeting scope and release dates",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "ash": {
        "official_url": "https://www.hematology.org/meetings",
        "capability": "SUPPORTED_MANUAL",
        "scope": "meeting schedule and abstracts",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "nasa": {
        "official_url": "https://www.nasa.gov/launches/",
        "capability": "SUPPORTED_MANUAL",
        "scope": "mission schedule and updates",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "faa": {
        "official_url": "https://www.faa.gov/space",
        "capability": "SUPPORTED_MANUAL",
        "scope": "licenses (not launch dates)",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
    "kis_daily": {
        "official_url": "https://github.com/koreainvestment/"
                        "open-trading-api/blob/main/examples_llm/"
                        "overseas_stock/dailyprice/dailyprice.py",
        "capability": "SUPPORTED_MANUAL",
        "scope": "existing app adapter reuse (unconfirmed until Step10/12)",
        "rate_key": "other_source_requests_per_second",
        "contact_env": None,
    },
}


def resolve_source_policy(source_id):
    """정책 effective 값. 숫자는 RUNUP_CONFIG 실시간 참조(복제 없음)."""
    try:
        policy = RUNUP_SOURCE_POLICIES[source_id]
    except KeyError:
        raise ValueError(f"unknown source: {source_id}") from None
    values = RUNUP_CONFIG
    return {
        "source_id": source_id,
        "official_url": policy["official_url"],
        "capability": policy["capability"],
        "scope": policy["scope"],
        "requests_per_second": values[policy["rate_key"]],
        "timeout_seconds": values["http_timeout_seconds"],
        "max_attempts": values["http_max_attempts"],
        "retry_base_seconds": values["http_retry_base_seconds"],
        "retry_max_seconds": values["http_retry_max_seconds"],
        "max_response_bytes": values["http_max_response_bytes"],
        "max_redirects": values["http_max_redirects"],
    }
