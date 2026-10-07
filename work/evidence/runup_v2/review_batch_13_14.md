# 자체 검토 — batch 13·14 (review_kind=SELF_REVIEW, reviewer=Muse)

- review_status: PASS (blocking findings 0건, 4건 보완 후 통과)
- 범위: engine/features·pivots + test 12건. 단타·dirty·117 규격 불변.

## trace
- A1 수식: SMA/Slope/Dryup/RVOL/ADV/Spread/TR/Wilder/RawK/Slow/CLV/wick/
  momentum/RS/Efficiency → 명세 golden 7종 손계산 대조
- A2 as_of/finality/basis/hash + unavailable 사유 보존 → cutoff·hash·
  FeatureValue 사유 검사
- A3 config snapshot만, IO/now 없음 → purity grep(해당 없음) + 기간
  변경 검사(ma10/30)
- B1 strict extrema + known_at → golden 확인시점 검사
- B2 HL/HH 최신 2개 + t-1 ref → HL true·ref 선택 검사
- B3 trigger ref vs 구조 low 분리 + 고정 ID → 양쪽 함수 검사
- B4 repaint/plateau/rolling 금지 → 3종 검사 + 미래 append 불변

## 발견·보완
1. 비정렬 입력 조용 계산 → 세션 정렬 후 계산(해시 포함)으로 변경.
2. NaN/Inf/bool 입력 침투 → 명시 ValueError 거부.
3. 마감 비교 UTC/ET 혼동·Decimal "3.0" 표기·ulp 비교 → approx·ET·정규화.
4. OHLC 문자열 전달 → fetch/엔진 경계에서 Decimal·date 변환.

## 명령/exit
- pytest 170건(신규 12): 170 passed. ruff 통과. 실제 API 0건.
- synthetic vs live: 전부 합성 손계산. live 미수행.

## 잔여
- NEEDS_INPUT 없음(엔진 단계).
- 다음 묶음: 15·16.
