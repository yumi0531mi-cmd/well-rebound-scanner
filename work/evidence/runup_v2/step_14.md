# V2 Step 14 증거 — 피벗 엔진

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_13 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/pivots.py` (신규): strict 확인·known_at·HL/HH·
  frozen ref 선택·구조 low 분리·미래 불변.
- `tests/runup_v2/unit/test_step_14_pivots.py`: 신규 6건(명세 golden).

## 검사
- 6함수 통과. 확인시점·HL·plateau·warmup·미확인봉·신규ref 익일·
  미래 append·구조 low·정렬·NaN 거부.
- `ruff` 통과. 실제 API 0건.

## 잔여
- 없음.
