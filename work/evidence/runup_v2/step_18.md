# V2 Step 18 증거 — Stop

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_17 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/stop.py` (신규): frozenHL·hard·initial·trailing·
  quote quality·FULL/POSSIBLE/NONE.
- `tests/runup_v2/unit/test_step_18_stop.py`: 신규 4건(손계산).

## 검사
- 4함수 통과. 94.525/92·FULL·무효·HL없음·원가/체결가·trail·
  stale/future/unknown/시세없음.
- `ruff` 통과. 실제 API 0건.

## 잔여
- split 변환 Step21, 신규 pivot ratchet은 서비스 전달 시.
