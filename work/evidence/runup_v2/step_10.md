# V2 Step 10 증거 — SPACE·FAA 캘린더

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_09 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/catalyst/space.py` (신규): mission identity·발사/허가 분리·
  NET WATCH_ONLY·bounded 원문·scrub revision·회사 근거 연결·manual 경로.
- `tests/runup_v2/unit/test_step_09_10_conf_space.py` 중 10 부분(3함수).

## 검사
- 10 관련 3함수 통과. license≠launch·근거없는 ticker 거부·NET 처리·
  scrub·재발사 ID 안정·coverage.
- `ruff` 통과. 실제 HTTP 0건. 합성 fixture만.

## 잔여
- NEEDS_INPUT: mission 일정 원천(manual), 회사 관련 근거.
- LIVE_UNVERIFIED: NASA/FAA 전체.
