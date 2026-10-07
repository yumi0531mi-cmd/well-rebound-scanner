"""임상 sponsor → 상장 ticker 자동 연결 제안 (Step: 일정→종목 방향).

정확·별칭 일치만 REVIEW 제안으로 만든다. 부분 유사명만으로 확정하지 않으며,
승인은 사람이 한다. 근거·사유를 보존한다.
"""
from __future__ import annotations

import re

_SUFFIXES = ("INC", "CORP", "CORPORATION", "LTD", "LIMITED", "LLC", "PLC",
             "LP", "LLP", "CO", "COMPANY", "HOLDINGS", "HOLDING", "GROUP",
             "NV", "SA", "AG")


def normalize_name(text: str) -> str:
    """비교용 정규화. 법인 꼬리표만 뗀다( 업종·고유명 보존)."""
    upper = re.sub(r"[^A-Z0-9]", "", str(text or "").upper())
    changed = True
    while changed and len(upper) > 2:
        changed = False
        for suffix in _SUFFIXES:
            if upper.endswith(suffix) and len(upper) - len(suffix) >= 2:
                upper = upper[: -len(suffix)]
                changed = True
                break
    return upper


def match_sponsor(sponsor, companies):
    """companies=[(ticker, title)]. (kind, ticker, title) 또는 None.

    exact: 정규화 일치. alias: 한쪽이 다른 쪽을 포함(4자 이상).
    동점·복수는 티커 순 첫 번째(결정적). 병원·기관명은 매치되지 않는다.
    """
    want = normalize_name(sponsor)
    if len(want) < 2:
        return None
    alias_hit = None
    for ticker, title in sorted(companies, key=lambda c: c[0]):
        got = normalize_name(title)
        if not got:
            continue
        if want == got:
            return ("exact", ticker, title)
        if alias_hit is None and len(want) >= 4 and len(got) >= 4 and (
                want in got or got in want):
            alias_hit = ("alias", ticker, title)
    return alias_hit
