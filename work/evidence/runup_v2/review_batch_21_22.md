# 자체 검토 — batch 21·22 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 6건 보완 후 통과)
- 범위: portfolio/positions·rollover·ranking + migration v3 + test 9건.
  단타·dirty·117 규격·전략수치 불변.

## trace
- A1 결제·예약 전이: confirm/cancel/expire + 중복·초과 거부
- A2 부분·취소·만기: 미체결만 해제 + consume 정합
- A3 split: 비율·효력·근거 검증 + 전량 불변 + 멱등 + 미래 거부
- A4 배당·flow·close: adjust 경로 + 외부flow 분리 + 전량 close 슬롯
- B1 순위·동점·제거·슬롯: 가중·tie-break·ticker 제거·max5
- B2 caps·비용·risk: NAV·발행·섹터·fee·slippage·risk 수량
- B3 proposal/approve 분리 + latest 재검증(transaction)
- B4 중복·CASH_WAIT·만료: 상태 전이 + 빈 목록

## 발견·보완
1. 날짜 문자열 cutoff 비교 실패 → 일자 끝 시각 정규화.
2. 승인 반환이 갱신 전 행 → 갱신 후 재조회.
3. 이중 승인 동일 현금 사용 → RESERVED 합계 재검증.
4. 제안 이벤트 기준 미보존 → allocation_events 테이블(v3) + 비교.
5. 트랜잭션 중첩 호출 → approve 자체 transaction, 호출 분리.
6. 테스트 만료 변이가 후속 검사를 오염 → 순서 조정.

## 명령/exit
- pytest 208건(신규 9): 208 passed. ruff 통과. 실제 API 0건.
- synthetic vs live: 전부 합성 손계산.

## 잔여
- earmark 금액 차감은 Step23 확인 후 연결(현재 예약·버퍼만 차감).
- slot 해제는 전량 close 기준(서비스가 상태로 판단).
- 다음 묶음: 23·24.
