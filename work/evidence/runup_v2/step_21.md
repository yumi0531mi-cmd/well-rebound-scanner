# V2 Step 21 증거 — 결제·reservation·split

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_20 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/portfolio/positions.py` (신규): 결제 확정·매도 예약·취소·
  만료·체결 차감·split 변환·멱등·미래 거부.
- `runup/storage/migrations.py` (보완): reservations 테이블(v3).
- `tests/runup_v2/integration/test_step_21_positions.py`: 신규 4건.

## 검사
- 4함수 통과. 결제 1회·초과·미결제·부분·취소·만기·split 불변·
  멱등·미래·배당·close.
- `ruff` 통과. 실제 API 0건.

## 잔여
- 예약 만기 시각은 Step22 승인 expiry와 별도 관리(통합은 Step25).
