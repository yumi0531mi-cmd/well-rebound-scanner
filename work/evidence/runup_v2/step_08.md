# V2 Step 08 증거 — FDA·PDUFA·AdCom 수집기

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_07 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/fda.py` (신규): AdCom/PDUFA 분리 타입, PDUFA 근거
  필수(없으면 NEEDS_INPUT), ticker 추정 금지, 연기·취소 revision 정보,
  미확인 구조 파서 없음(UNSUPPORTED+manual 경로).
- `tests/runup_v2/unit/test_step_07_08_sec_fda.py` 중 08 부분.

## 검사
- 08 관련 4함수 통과. 타입 분리·근거 거부·ticker 미추정·연기·
  동일약물 두 날짜·미확인·coverage.
- `ruff` 통과. 실제 요청: probe 1건 TLS 차단 실패 기록.

## 잔여
- LIVE_UNVERIFIED: FDA 구조 전체 미확인(환경 TLS 차단).
- NEEDS_INPUT: PDUFA 근거(SEC/IR·manual), AdCom 일정 원천.
