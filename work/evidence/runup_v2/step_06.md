# V2 Step 06 증거 — ClinicalTrials 수집기

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: stage_05 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/clinical_trials.py` (신규): 확인된 endpoint·필드만
  파싱, PCD=TRIAL_COMPLETION_MARKER(일정 승격 없음), 월·일 정밀도 구분,
  미확인 상태 자동승격 없음, 중단/종료는 검증 집합에서만 risk 후보,
  pagination·cursor는 commit 후 전진, parser 확인 empty만 EMPTY_CONFIRMED.
- `tests/runup_v2/fixtures/source/ct_api_v2_sample.json` (+provenance):
  실제 읽기 응답 1건(NCT01203735, PCD 2013-02 MONTH, UNKNOWN, 병원 sponsor).
- `tests/runup_v2/unit/test_step_06_ct.py`: 신규 7건(실제 2 + 합성 5).

## 계약
- `collect(cursor,observed_at,policy)->(CollectionResult, details)`.
  03·04 공통계층 재사용, 중복 엔진 없음. SEC 차단 영향 없음.

## 검사
- `pytest test_step_06_ct`: **7 passed, 0 failed**. 실제 필드·정밀도·
  PCD 표기·risk 미승격·병원 REVIEW·empty/오형식/PARTIAL/cursor 순서·
  검증 집합·registry 정직 표시.
- `ruff check` 통과. 실제 요청: 읽기 probe 2건만(웹 조회, 키 없음).
  live smoke 미수행 → LIVE_UNVERIFIED.

## 실제 API vs fixture
- 실제: 위 probe 2건(응답 구조·필드 확인용). 앱 구현에서 실제 호출 없음.
- fixture: 실제 1건 + 합성 5건, 출처·시각·범위 분리 기록.

## 잔여
- LIVE_UNVERIFIED: endpoint smoke·전체 coverage 미확인.
- NEEDS_INPUT: sponsor↔ticker 확정(Step11), PCD 외 일정 원천(Step07~10).
