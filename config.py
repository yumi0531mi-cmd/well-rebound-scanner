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
