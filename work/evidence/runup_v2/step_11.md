# V2 Step 11 증거 — 정규화·검토·위험 경계

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_10 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/normalize.py` (신규): identity·정밀도 경계 6종·
  WATCH_ONLY·REVIEW/CONFLICT/importance·risk_boundary·deadline.
- `runup/catalyst/review.py` (신규): provenance·ID·권한 계약 승인.
- `runup/data/calendar.py` (신규): 세션·휴장·조기종료·DST,
  cutoff/deadline 계산.
- `runup/domain/documents.py` (보완): 경계 시작·종료 ISO 문자열 통일.
- `tests/runup_v2/unit/test_step_11_normalize.py`: 신규 6건.

## 검사
- 6함수 통과. identity·정밀도·WATCH_ONLY·timezone·mapping·conflict·
  월요일 deadline·휴일·조기종료·분기·earliest·취소·review 6종.
- `ruff` 통과. 실제 API 0건.

## 잔여
- NEEDS_INPUT 없음(검토 권한은 Step28 서버 gate에서).
