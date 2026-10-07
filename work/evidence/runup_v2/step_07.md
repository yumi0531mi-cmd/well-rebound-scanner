# V2 Step 07 증거 — SEC·기업 IR 수집기

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_06 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/sec_ir.py` (신규): submissions 열 정렬 검증·행 후보,
  규칙 기반 span 분류(readout/중단/희석/ATM 구분, REVIEW 고정),
  IR allowlist(빈 집합)+검토 경로, UA 전달, 정정 플래그, 파일 pagination.
- `tests/runup_v2/fixtures/source/sec_submissions_sample.*`:
  실제 구조 2건 + provenance.
- `tests/runup_v2/unit/test_step_07_08_sec_fda.py` 중 07 부분.

## 검사
- 07 관련 6함수 통과. 실제 구조·정렬·pagination·CIK·span·지시문 무시·
  실패 typed·allowlist·UA·정정·registry 정직 표시.
- `ruff` 통과. 실제 요청: 읽기 probe 1건(구조 확인). 앱 호출 0건.

## 잔여
- NEEDS_INPUT: SEC UA 미입력, IR allowlist 비어 있음.
- LIVE_UNVERIFIED: 전체 coverage·parser smoke 미수행.
