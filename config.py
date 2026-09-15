"""User-tunable scanner and validation settings.

Rates are fractions.  Changing a value creates a new strategy version and never
retroactively changes a stored backtest result.
"""

# Qualification targets
TARGET1_HIT_RATE_GOAL = 0.80
TARGET1_HIT_RATE_FLOOR = 0.70
MIN_UNIQUE_ENTRIES_PER_SESSION = 5
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
HISTORY_INITIAL_READY_BARS = 180
HISTORY_WARM_TARGET_BARS = 3000
BACKTEST_MAX_STORED_BARS = 32000
DURABLE_PREFETCH_SYMBOLS_PER_QUERY = 20
SCANNER_CYCLE_SECONDS = 60.0
SCANNER_REQUEST_DEADLINE_MARGIN_SECONDS = 8.0
CANDIDATE_SNAPSHOT_INTERVAL_SECONDS = 300
CANDIDATE_FALLBACK_MAX_AGE_SECONDS = 600.0
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
PROBABILITY_MODEL_VERSION = "causal-logit-v1"
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

# Explicit production allow-list. Implemented strategies not listed stay off.
ACTIVE_STRATEGY_VALUES = (
    "박스권 반등",
    "급등 후 눌림",
    "과매도 반등",
    "가격강도 선도주 눌림 재개",
    "유동성 스윕 후 회복",
    "전일 고가 돌파 후 재지지",
)
