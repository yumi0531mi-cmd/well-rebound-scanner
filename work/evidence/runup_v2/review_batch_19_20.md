# 자체 검토 — batch 19·20 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 4건 보완 후 통과)
- 범위: engine/exit·portfolio/ledger + test 10건. 단타·dirty·117 불변.

## trace
- A1 우선순위: deadline·CRITICAL → stop 관측 → break → exhaustion →
  HOLD, 최강 1개 + 전원인 기록
- A2 단조·Q0·예약: 이전 목표 유지 + 공식 그대로 + 잔량만 권고
- A3 floor lot·FULL 잔량·DEFER: Q0=1·25%→0, FULL→잔량 전부
- A4 무변경: 순수 반환, reservation은 command 영역으로 분리 문서화
- B1 매수·매도·자본·비용·배당 + projection 원자 기록
- B2 원가 해제·전량 0·settled/unsettled/realized/external 분리
- B3 reversal+재입력·초과 사실 보존·조정 경로
- B4 중복·충돌·rollback·quote-fill 분리(시그니처에 quote 없음)

## 발견·보완
1. 이전 목표 잔량 소실(신호 없을 때 0으로 리셋) → 이전 목표 유지.
2. 임계값·목표 개수 불일치 조용 절단 → REVIEW 명시.
3. 비용·배당 기록 경로 부재 → record_adjustment + 검사.
4. 초과 매수 조용 실패 → 사실 기록 + RECONCILIATION_REQUIRED.

## 명령/exit
- pytest 199건(신규 10): 199 passed. ruff 통과. 실제 API 0건.
- synthetic vs live: 전부 합성 손계산(101/47/40.4/6.6/60.6 일치).

## 잔여
- split 단위 변환·예약 차감은 Step21·22, earmark 정산은 Step23.
- 다음 묶음: 21·22.
