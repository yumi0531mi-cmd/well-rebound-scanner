"""Read-only, per-symbol explanation; unknown evidence is never a pass."""
from config import STRATEGY_FRAME_REQUIREMENTS

from .models import ACTIVE_STRATEGIES, ALL_ENTRY_STRATEGIES, ScanResult


def strategy_gate_rows(result: ScanResult) -> list[dict[str, object]]:
    diagnostics = result.diagnostics
    assessments = diagnostics.get("classification_assessments", {})
    rejections = diagnostics.get("classification_rejections", {})
    assessments = assessments if isinstance(assessments, dict) else {}
    rejections = rejections if isinstance(rejections, dict) else {}
    rows = []
    for strategy in ALL_ENTRY_STRATEGIES:
        raw = assessments.get(strategy.value)
        details = raw if isinstance(raw, dict) else {}
        formed = isinstance(raw, dict)
        active = details.get("active", strategy in ACTIVE_STRATEGIES)
        reasons = rejections.get(strategy.value, ())
        reasons = reasons if isinstance(reasons, (tuple, list)) else ()
        missing, frames = [], []
        for minutes, required in STRATEGY_FRAME_REQUIREMENTS[strategy.value].items():
            actual = diagnostics.get(f"bars_{minutes}m")
            frames.append(f"{minutes}분 {actual if actual is not None else '?'}/{required}")
            if not isinstance(actual, (int, float)) or actual < required:
                missing.append(f"{minutes}분봉 부족/미확인")
        selected = formed and result.strategy == strategy
        if not formed:
            gate = "데이터 준비" if missing else "기법 미형성" if reasons else "평가 증거 없음"
            reason = " | ".join(str(value) for value in (reasons or missing))
        else:
            gates = (("운영 활성", active), ("상품 확인", details.get("product_valid")),
                     ("계획 비용", details.get("planned_cost_pass")),
                     ("체결구간", details.get("fill_band_pass")),
                     ("현재 비용", details.get("current_cost_pass")))
            gate = next((name for name, passed in gates if passed is not True), "")
            reason = str(details.get("block_reason") or "")
            if not gate:
                gate = "동일종목 다른 기법 선택" if not selected else result.stage.value
                reason = (f"선택기법: {result.strategy.value}" if not selected
                          else " | ".join(result.reasons))
        rows.append({
            "기법": strategy.value, "활성": bool(active), "필요 완료봉(현재/필요)": " · ".join(frames),
            "형성": formed, "상품": details.get("product_valid"),
            "계획 순손익비": details.get("planned_net_rr"), "비용통과": details.get("planned_cost_pass"),
            "체결구간": details.get("fill_band_pass"), "현재 순손익비": details.get("current_net_rr"),
            "현재비용통과": details.get("current_cost_pass"), "선택": selected,
            "최초 미통과/최종단계": gate, "전체 사유": reason or "미확인",
        })
    return rows
