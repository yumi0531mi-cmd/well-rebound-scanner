# 자체 검토 — batch 11·12 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 3건 보완 후 통과)
- 범위: normalize·review·calendar·prices·quote_bridge + DTO 경계 변경.
  단타·dirty·117 규격 불변.

## trace (STEP 요구 → 구현 → 검사)
- A1 identity·append-only: event_identity + revision_seq →
  test_step11_identity (program·날짜 분리, seq)
- A2 precision/timezone/conflict/importance: precision_bounds 6종 +
  REVIEW/CONFLICT/importance 4단계 → bounds·watch_only·conflict 검사
- A3 earliest·deadline: risk_boundary + calendar.forced_exit_deadline →
  월요일→목요일·휴일·연말 검사
- A4 review command: provenance·ID·권한 → 6종 거부/승인 검사
- B1 KIS 일봉: kis_daily_capability 미지원 판정(분봉·현재가만 확인)
- B2 fallback·basis 분리: provider 주입 + basis passthrough + 이중조정
  없음 → split/unknown 검사
- B3 sessions/finality/revision/action: calendar expected + grace 15분 +
  revision 이력 → 진행봉·grace 경계·revision 검사
- B4 수신/체결 분리: quote_bridge age 정책 + UNKNOWN/INVALID →
  fresh/stale/unknown/future 검사

## 발견·보완
1. OHLC·경계 문자열 정밀도 보존(Date 객체 강제 대신 ISO 문자열로 통일).
2. PriceBar provider 문자열→Decimal/date 변환 누락 → fetch에서 변환.
3. 마감 시각 비교 기준 혼동(UTC vs ET) → ET 변환 후 비교로 수정.

## 명령/exit
- pytest 158건(신규 12): 158 passed. ruff 통과.
- 실제 HTTP 0건. KIS 자격 없음 → price smoke LIVE_UNVERIFIED.
- synthetic vs live: 합성 + exchange_calendars 로컬 검증.

## 잔여
- NEEDS_INPUT: KIS 자격·일봉 어댑터 확인(Step10 영역),
  SEC UA(SEC 경로만), 비용 None.
- LIVE_UNVERIFIED: 가격 소스 전체, FDA 구조.
- 다음 묶음: 13·14.
