# 자체 검토 — batch 15·16 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 3건 보완 후 통과)
- 범위: engine/setup·trigger + test 10건 + 피처 3종 추가. 단타·dirty·
  117 규격·전략수치 불변.

## trace
- A1 hardgate+6 flags: gate 7종 + flags + score → score·gate 검사
- A2 latch/generation: 신규 generation + 동일입력 동일ID + 연장 없음
- A3 만료·무효: 10세션 경계·구조·risk·event 변경 → 4종 검사
- A4 dryup 증거: reasons dryup@session + trigger 독립 거래량 판정
- B1 6종 gate: MA·HL·cross·RVOL·CLV·extension·위험 세션 → 경계 검사
- B2 expiry: 다음 close·deadline 중 이른 값 → 검사
- B3 분리·결정 ID: 주문 필드 없음 + 결정적 ID → 검사
- B4 소비·재진입: consumed 거부 + 새 generation + 새 cross → 검사

## 발견·보완
1. StrEnum 비교 오판단(`str(x)=="..."` 상시 False) → 멤버 직접 비교.
2. 게이트 CRITICAL이 래치 무효화를 가림 → risk 최신이면 INVALID 우선.
3. squeeze 미정의 → MA 수렴 해석 명시(코드·증거·계약 문서).
4. stoch/cross 이력 부재 → 피처에 slow_k_prev·ma20_cross_age·close 추가.

## 명령/exit
- pytest 180건(신규 10): 180 passed. ruff 통과. 실제 API 0건.
- synthetic vs live: 전부 합성 손계산.

## 잔여
- profile 변경 무효화는 서비스 책임(저장 hash 없음, 문서화).
- 다음 묶음: 17·18.
