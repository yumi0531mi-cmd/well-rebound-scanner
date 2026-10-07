# V2 Step 15 증거 — SETUP

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_14 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/setup.py` (신규): hardgate·6 flags·가중 score·latch·
  generation·만료 10세션·무효 3종·dryup 증거.
- `runup/engine/features.py` (보완): close·slow_k_prev·ma20_cross_age.
- `tests/runup_v2/unit/test_step_15_setup.py`: 신규 5건(85점 손계산).

## 검사
- 5함수 통과. score·threshold·가중치0·결측·게이트·래치·만료·무효.
- `ruff` 통과. 실제 API 0건.

## 잔여
- profile 변경 무효화는 서비스 책임.
