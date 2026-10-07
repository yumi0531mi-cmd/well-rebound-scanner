# V2 Step 13 증거 — 피처 엔진

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_12 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/features.py` (신규): 18종 피처 순수 계산, config
  snapshot 기간, cutoff·정렬·입력 검증, bar_hash·config_hash·사유 보존.
- `tests/runup_v2/unit/test_step_13_features.py`: 신규 6건(명세 golden).

## 검사
- 6함수 통과. RVOL·dryup·ATR seed/Wilder·RawK·SlowK/D·warmup 경계·
  zero-range·benchmark 결측·불변·hash·정렬·NaN 거부·config 기간.
- `ruff` 통과. 실제 API 0건.

## 잔여
- 없음(수익성 미검증은 전역 상태).
