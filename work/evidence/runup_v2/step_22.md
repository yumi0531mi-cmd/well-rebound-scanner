# V2 Step 22 증거 — ranking·allocation

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_21 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/engine/ranking.py` (신규): 가중 순위·동점·ticker 제거·최소값.
- `runup/portfolio/rollover.py` (신규): 배분·승인·재검증·이벤트 기준.
- `tests/runup_v2/unit/test_step_22_rollover.py`: 신규 5건(손계산).

## 검사
- 5함수 통과. 순위·제거·배분·게이트·승인·이중·만료·현금·변경.
- `ruff` 통과. 실제 API 0건.

## 잔여
- earmark 금액 차감은 Step23 이후 연결. NEEDS_INPUT: 비용·원금.
