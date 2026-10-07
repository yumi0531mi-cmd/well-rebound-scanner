# 자체 검토 — batch 07·08 (review_kind=SELF_REVIEW, reviewer=Muse)

- version: V2 batch 07·08 / review_status: PASS (blocking findings 0건)
- 범위: runup/catalyst/sec_ir.py·fda.py, http.py body_sink 추가,
  tests 10건, fixture 2건. 단타·dirty·117 규격 불변.

## 요구→구현→검사 trace
- A1 발견+span: parse_submissions(열 정렬 검증)+find_spans →
  test_step07_real_fixture_columns_aligned·text_spans (실제 구조)
- A2 후보 한정: classify 4종 + REVIEW 고정 → spans/halt/ATM 분리 검사
- A3 ATM/희석 구분: DILUTION 정규식 축소 + ATM_SHELF_ONLY →
  test_step07_text_spans (offering prospectus는 ATM만)
- A4 allowlist+검토: IR_ALLOWLIST(빈 집합)+is_allowlisted_ir →
  test_step07_sec_policy (미등록 거부, NEEDS_INPUT 경로)
- UA·rate: collect user_agent 전달 + effective 2rps →
  test_step07_user_agent_passed_and_amendment_flagged
- 정정 filing: details amended 플래그 → 동일 검사
- B1 분리 타입: FDA_ADCOM vs PDUFA_TARGET → distinct 검사
- B2 근거 연결: evidence 없으면 None+이유 → refused 검사
- B3 무가정: collect UNSUPPORTED + registry SUPPORTED_MANUAL
- B4 manual·coverage: postponement + LIVE_UNVERIFIED → 검사
- 동일 약물 두 날짜: candidate_id 분리 → 검사 추가
- 연기·취소·시간미정: postponement + UNKNOWN precision → 검사

## 발견·보완 (Pass B)
1. 임상 실패 재시도가 방문 기록에 막힘 → redirect 때만 기록으로 수정.
   (http.py, Step04 회귀 11건 재통과)
2. SEC DILUTION 정규식이 ATM offering까지 탐지 → shelf/dilution 명시로 축소.
3. AdCom/PDUFA 문서 ID 빈값 → 호출자 문서 ID 전달 구조로 변경.
4. CT parse_study 벽시계 → observed_at 주입(기본 now 유지).
5. JSON 실패 원인 타입명 포함.

## 명령/exit
- pytest test 10함수: 10 passed, 0 failed.
- ruff: 통과. 실제 HTTP 0건(읽기 probe SEC 2건·FDA 1건 실패 별도 기록).
- synthetic vs live: live=SEC 구조 1건+CT 1건, 나머지 합성. 분리 표기.

## 잔여
- NEEDS_INPUT: SEC UA 미입력, IR allowlist 비어 있음, FDA 구조 미확인.
- LIVE_UNVERIFIED: SEC endpoint smoke 1건 성공이나 전체 coverage·parser
  smoke는 미수행. FDA 전체 미확인.
- 다음 묶음: 09·10.
