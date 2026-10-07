# 자체 검토 — batch 17·18 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 5건 보완 후 통과)
- 범위: engine/strength·exhaustion·stop + test 9건 + 피처 sma_short.
  단타·dirty·117 규격·전략수치 불변.

## trace
- A1 4+8 components: 명세 수식 그대로 + 단독 true/false 검사
- A2 명시 입력: shortMA·RSprev·Effprev·frozenref(price)·HL 구조
- A3 score+이유+unavailable, probability 없음
- A4 독립: strength≠exhaustion, 100-strength 변환 없음
- B1 frozenHL·수수료제외 entry·hard/initial/trailing
- B2 하향 확대 금지 + split은 Step21 영역으로 문서화
- B3 fresh trade/received/unknown 구분
- B4 시세 없어도 typed 결과 + stops 계산 유지

## 발견·보완
1. StrEnum 비교 오판단 2건 → 멤버 직접 비교.
2. sma_short·frozen ref 가격 전달 경로 부재 → 피처 추가·"id@price" 규격.
3. StopResult float 전달 → Decimal 변환(정확 비교).
4. score 경계 동등 판정(75.0 도달 표기).

## 명령/exit
- pytest 189건(신규 9): 189 passed. ruff 통과. 실제 API 0건.
- synthetic vs live: 전부 합성 손계산.

## 잔여
- split 단위 변환은 Step21, trailing ratchet용 신규 pivot은 Step22 이후
  서비스에서 전달.
- 다음 묶음: 19·20.
