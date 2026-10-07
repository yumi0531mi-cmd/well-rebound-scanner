# V2 Step 09 증거 — 학회·초록 캘린더

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_08 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/conferences.py` (신규): 4학회 registry·6종 공개
  구분·program 포함 ID·근거 연결·earliest data release·manual 경로.
- `tests/runup_v2/unit/test_step_09_10_conf_space.py` 중 09 부분(4함수).

## 검사
- 09 관련 4함수 통과. generic 단독 ENTRY 불가·초록 우선·title 구분·
  timezone 보존·program 분리·registry 정직 표시.
- `ruff` 통과. 실제 HTTP 0건. 합성 fixture만.

## 잔여
- NEEDS_INPUT: 일정 원천(manual), issuer 연결 근거.
- LIVE_UNVERIFIED: 4학회 전체.
