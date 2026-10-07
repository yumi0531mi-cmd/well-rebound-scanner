"""Runup V2 미국 universe·issuer·mapping (Step 05).

KIS MasterCatalog는 목록 후보 자료일 뿐 sector/sponsor 확정 근거가 아니다.
전체 커버리지를 확인하기 전 완전하다고 표기하지 않는다.
"""
from __future__ import annotations

import dataclasses

from runup.domain.base import FrozenDTO

US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX", "NYSEARCA", "BATS"})
COMMON_EQUITY = "COMMON"

# KIS 제공자 코드 -> 내부 표준 거래소 코드 변환 계약.
# 조회 API(EXCD)에는 제공자 코드를 쓰고, 투자 대상 필터에는 표준 코드를 쓴다.
# DB의 exchange 값은 제공자 코드 그대로 보존한다(API 호출용).
KIS_PROVIDER_EXCHANGES = {"NAS": "NASDAQ", "NYS": "NYSE", "AMS": "AMEX"}


def to_standard_exchange(code: str) -> str:
    """제공자·표준 코드를 내부 표준 코드로 변환한다. 미상은 대문자 그대로."""
    upper = (code or "").upper()
    if upper in US_EXCHANGES:
        return upper
    return KIS_PROVIDER_EXCHANGES.get(upper, upper)


@dataclasses.dataclass(frozen=True)
class UniverseCoverage(FrozenDTO):
    coverage_id: str
    universe_id: str
    as_of: object
    source_id: str
    scope_note: str
    total_listed: int = 0
    classified: int = 0
    pending: int = 0
    stale: int = 0


UniverseCoverage.__kind_hints__ = {
    "coverage_id": "id", "universe_id": "str", "as_of": "dt",
    "source_id": "id", "scope_note": "str", "total_listed": "int>=0",
    "classified": "int>=0", "pending": "int>=0", "stale": "int>=0",
}


def sector_bucket(sector: str) -> str:
    upper = (sector or "").upper()
    if upper in ("BIO", "PHARMA", "BIOPHARMA"):
        return "BIOPHARMA"
    if upper == "SPACE":
        return "SPACE"
    return "OTHER"


def is_us_listed(security, as_of=None) -> tuple:
    """미국상장 equity 여부. (eligible, reasons). 근거 없는 추측 없음."""
    reasons = []
    standard = to_standard_exchange(security.exchange)
    if standard not in US_EXCHANGES:
        reasons.append(f"거래소 {security.exchange}(표준 {standard})는 미국 대상이 아니다")
    if security.currency != "USD":
        reasons.append(f"통화 {security.currency}는 USD가 아니다")
    if security.equity_type != COMMON_EQUITY:
        reasons.append(f"상품 {security.equity_type}는 보통주가 아니다")
    return (not reasons, reasons)


def active_at(valid_from, valid_to, as_of) -> bool:
    if as_of is None:
        return True
    if valid_from is not None and as_of < valid_from:
        return False
    if valid_to is not None and as_of >= valid_to:
        return False
    return True


def resolve_security_at(securities, ticker: str, as_of):
    """해당 시점에 유효한 security. 0건=없음, 복수=REVIEW 모호성."""
    matched = [s for s in securities
               if s.ticker == ticker
               and active_at(s.valid_from, s.valid_to, as_of)]
    if len(matched) == 1:
        return matched[0], []
    if not matched:
        return None, [f"{ticker}: 해당 시점 유효 종목 없음"]
    return None, [f"{ticker}: {len(matched)}건 중복, REVIEW 필요"]


def latest_observation(observations, as_of):
    """available_at<=as_of 중 최신. 미래 관측을 과거로 소급하지 않는다."""
    usable = [o for o in observations if o.available_at <= as_of]
    if not usable:
        return None
    return max(usable, key=lambda o: (o.available_at, o.as_of))


def is_stale(observation, as_of, max_age_seconds: float) -> bool:
    if observation is None:
        return True
    return (as_of - observation.available_at).total_seconds() > max_age_seconds


def manual_issuer_entry(issuer_id, legal_name, ticker, exchange,
                        observed_at, evidence=(), sector_tags=()):
    """근거 있는 manual 등록. 확정은 검토 단계, 여기서는 후보+근거만."""
    from runup.domain.documents import Issuer, Security

    if not evidence:
        raise ValueError("manual 등록에는 근거가 필요하다")
    issuer = Issuer(
        issuer_id=issuer_id, legal_name=legal_name,
        sector_tags=tuple(sector_tags), valid_from=observed_at,
        mapping_evidence=tuple(evidence))
    security = Security(
        security_id=f"{ticker}.{exchange}", issuer_id=issuer_id,
        ticker=ticker, exchange=exchange, currency="USD",
        equity_type=COMMON_EQUITY, listing_status="LISTED_MANUAL",
        valid_from=observed_at)
    return {"issuer": issuer, "security": security,
            "review_status": "REVIEW", "evidence": list(evidence)}


def from_master_catalog(catalog) -> list:
    """기존 MasterCatalog 행을 미검토 후보로 변환. 실패 시 NEEDS_INPUT."""
    securities = []
    for row in catalog:
        securities.append({
            "ticker": row.get("ticker", ""),
            "exchange": row.get("exchange", ""),
            "currency": row.get("currency", ""),
            "equity_type": row.get("equity_type", ""),
            "status": "UNVERIFIED_LISTING_CANDIDATE",
        })
    return securities
