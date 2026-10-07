# V2 Step 19 증거 — Exit

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_18 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/exit.py` (신규): 우선순위·단조 목표·floor lot·DEFER·
  FULL 잔량·재실행 무해·결정 ID.
- `tests/runup_v2/unit/test_step_19_exit.py`: 신규 5건(손계산).

## 검사
- 5함수 통과. deadline·CRITICAL·stop·break·60점→15·rerun·단조·
  DEFER·FULL·HOLD·임계값·분리.
- `ruff` 통과. 실제 API 0건.

## 잔여
- reservation·체결 가정 없음(Step22·20 영역).
