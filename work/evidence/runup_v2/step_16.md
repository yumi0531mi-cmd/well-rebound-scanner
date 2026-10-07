# V2 Step 16 증거 — TRIGGER

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_15 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/trigger.py` (신규): frozen ref(t-1 지식)·cross·RVOL·
  CLV·extension·위험 세션·expiry·소비·재진입·결정 ID.
- `tests/runup_v2/unit/test_step_16_trigger.py`: 신규 5건.

## 검사
- 5함수 통과. 적격·경계·신규ref 제외·위험·휴일·마감·소비·재진입·
  미래·결측·리뷰·분리.
- `ruff` 통과. 실제 API 0건.

## 잔여
- 없음.
