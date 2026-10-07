"""Runup V2 도메인 enum (Step 02). 명세에 값이 나열된 것만 enum이다."""
from enum import StrEnum


class DatePrecision(StrEnum):
    EXACT_DATETIME = "EXACT_DATETIME"
    EXACT_DATE = "EXACT_DATE"
    WINDOW = "WINDOW"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    UNKNOWN = "UNKNOWN"
    NO_EARLIER_THAN = "NO_EARLIER_THAN"


class CollectionStatus(StrEnum):
    OK = "OK"
    EMPTY_CONFIRMED = "EMPTY_CONFIRMED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    RATE_LIMITED = "RATE_LIMITED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    REVIEW = "REVIEW"


class FeatureStatus(StrEnum):
    VALID = "VALID"
    UNDEFINED = "UNDEFINED"
    INSUFFICIENT = "INSUFFICIENT"
    STALE = "STALE"


class SetupState(StrEnum):
    WATCH = "WATCH"
    SETUP = "SETUP"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class ExitAction(StrEnum):
    HOLD = "HOLD"
    PARTIAL = "PARTIAL"
    FULL = "FULL"
    REVIEW = "REVIEW"


class StopAction(StrEnum):
    NONE = "NONE"
    POSSIBLE_STOP = "POSSIBLE_STOP"
    FULL = "FULL"


class LedgerCommandStatus(StrEnum):
    APPLIED = "APPLIED"
    REPLAY = "REPLAY"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class FillSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"


class CapitalFlowType(StrEnum):
    DEPOSIT = "DEPOSIT"
    CAPITAL_WITHDRAWAL = "CAPITAL_WITHDRAWAL"
    PROFIT_WITHDRAWAL = "PROFIT_WITHDRAWAL"
    TAX_WITHDRAWAL = "TAX_WITHDRAWAL"
