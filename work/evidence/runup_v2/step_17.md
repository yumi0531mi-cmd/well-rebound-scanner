# V2 Step 17 증거 — Strength·Exhaustion

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_16 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/strength.py` (신규): 4 components 가중 평균·결측 NULL.
- `runup/engine/exhaustion.py` (신규): 8 flags·임계값 도달 표기·독립.
- `runup/engine/features.py` (보완): sma_short.
- `tests/runup_v2/unit/test_step_17_scores.py`: 신규 5건(손계산).

## 검사
- 5함수 통과. 가중 62.5·전부 100·단독 12.5·임계값 75 도달·건강 0·
  부분 87.5·결측 NULL·독립성.
- `ruff` 통과. 실제 API 0건.

## 잔여
- 없음.
