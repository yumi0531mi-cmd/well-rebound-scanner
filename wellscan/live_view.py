"""Small, escaped live-price payload; no history, persistence or API calls."""
from html import escape
from math import isfinite

from .models import ScanResult, Stage
from .policy import Costs


def entry_readiness_text(result: ScanResult) -> str:
    """Explain an engine result without inventing a price or promoting a signal."""
    if result.final_buy:
        return "진입 조건 충족 · 체결 확정 아님"
    reason = " · ".join(result.reasons[:2])
    if not reason:
        reason = result.levels.basis or "기법 형성 근거 미확인"
    label = "진입가 도달 대기" if result.stage == Stage.ENTRY_WAIT else "진입 불가"
    return f"{label} · {reason}"


def target_return_text(result: ScanResult) -> str:
    """Use frozen engine costs only; missing costs remain unknown, not zero."""
    entry, target = result.levels.entry, result.levels.target1
    if not all(isinstance(x, (int, float)) and isfinite(x) and x > 0 for x in (entry, target)):
        return "T1 미산출"
    gross = (target / entry - 1) * 100
    values = [result.diagnostics.get(f"cost_{key}") for key in ("buy_fee", "sell_fee", "sell_tax", "slippage")]
    source = result.diagnostics.get("cost_source")
    try:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("cost source missing")
        costs = Costs(*values, source=source)
        return f"T1 가격폭 {gross:+.2f}% / 비용후 {costs.net_return(entry, target):+.2f}% (가정)"
    except (TypeError, ValueError):
        return f"T1 가격폭 {gross:+.2f}% / 비용 미확인"


def live_price_table(rows: list[tuple[str, str, str, str, str, str]]) -> str:
    headers = ("종목", "현재가", "진입가 / T1", "Hard Stop", "현재 조건", "수신 KST / 경로")
    head = "".join(f"<th>{title}</th>" for title in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div style="overflow-x:auto"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
