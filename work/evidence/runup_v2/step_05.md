# V2 Step 05 증거 — 미국 universe·mapping

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: stage_04 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/data/universe.py` (신규): 미국상장 판정·유효기간·ticker 이력
  해석·sector bucket·시총 관측/stale·정직 coverage·manual 근거 등록·
  MasterCatalog 미검토 후보 변환.
- `runup/catalyst/mapping.py` (신규): sponsor 관계 후보만 생성(자동확정
  없음, 인공 점수 없음), 검토 기록 builder.
- `runup/storage/repositories.py` (확장): 관측·mapping 검토 저장 2종.
  migration·기존 동작 불변.
- `tests/runup_v2/unit/test_step_05_universe.py`: 신규 9건.

## 계약
- 미확인·모호 mapping은 REVIEW, ETF·비미국 제외, 시총 None·stale 0 치환
  금지, 과거 소급 금지, curated 범위만 표기.

## 검사
- `pytest test_step_05_universe`: **9 passed, 0 failed**. 다중ticker·
  유사명·CIK·rename·상장폐지·ETF·sector·시총·coverage·부분일치·병원·
  manual·저장 roundtrip.
- `ruff check` 통과. 실제 API·백테스트·push·배포 없음. 합성 fixture.

## 잔여
- NEEDS_INPUT: 상장 sponsor↔ticker 확정은 Step11 검토에서.
  LIVE_UNVERIFIED: 목록 전체 커버리지 미확인.
