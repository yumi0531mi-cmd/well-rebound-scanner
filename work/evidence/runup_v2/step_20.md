# V2 Step 20 증거 — 원장

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_19 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/portfolio/ledger.py` (신규): 체결·자본·비용·배당·reversal·
  재구축·멱등·충돌·원자 기록.
- `tests/runup_v2/integration/test_step_20_ledger.py`: 신규 5건(손계산).

## 검사
- 5함수 통과. 101/47/40.4/6.6/60.6·전량 0·fee·초과·float·통화·
  replay·conflict·reversal·재구축 일치·비용·배당·초과 조정.
- `ruff` 통과. 실제 API 0건.

## 잔여
- split·예약·earmark 정산은 Step21·22·23.
