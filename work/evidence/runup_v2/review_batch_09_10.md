# 자체 검토 — batch 09·10 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 1건 보완 후 통과)
- 범위: conferences.py·space.py·test 7건. 단타·dirty·117 규격 불변.

## trace
- A1 registry+generic calendar: ORG_URLS 4종+Edition+collect UNSUPPORTED
- A2 6종 분리: RELEASE_TYPES + test 6종
- A3 근거 연결만: link_issuer evidence 필수 + generic 단독 ENTRY 불가
- A4 manual 경로: 명시 입력 builders, 자동 fetch 주장 없음
- B1 mission identity 보존: MissionIdentity + mission_event
- B2 license/launch 분리 + ticker 근거 필수
- B3 NET unbounded WATCH_ONLY·bounded 원문 보존·scrub revision
- B4 manual+health: collect UNSUPPORTED + registry LIVE_UNVERIFIED

## 발견·보완
1. 동일날짜 두 프로그램 candidate_id 충돌 → program 포함으로 수정,
   분리 검사 추가.

## 명령/exit
- pytest 146건(신규 7): 146 passed. ruff 통과. 실제 HTTP 0건.
- synthetic vs live: 전부 합성+공식 URL 참조. live smoke 없음.

## 잔여
- NEEDS_INPUT: 학회·SPACE 일정 원천(manual), issuer 연결 근거.
- LIVE_UNVERIFIED: 4학회+NASA/FAA 전체.
- 다음 묶음: 11·12.
