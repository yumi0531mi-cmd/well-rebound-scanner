# V2 Step 12 증거 — 일봉·bridge

- 상태: IMPLEMENTED (ACCEPTED 아님, 자체 review PASS)
- 일시(UTC): 2026-10-06
- 선행: stage_11 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `runup/data/prices.py` (신규): provider 주입·OHLC 검증·세션 정렬·
  중복·basis 분리·finality grace·completeness·KIS 일봉 미지원 판정.
- `runup/data/quote_bridge.py` (신규): 수신/체결 분리·age 정책·
  STALE/UNKNOWN/INVALID.
- `tests/runup_v2/unit/test_step_12_prices.py`: 신규 6건.

## 검사
- 6함수 통과. 정상·OHLC·NaN·중복·휴장·진행봉·grace·basis·split·
  provider 부재·수신/지연 분리·stale·future.
- `ruff` 통과. 실제 price smoke 없음 → LIVE_UNVERIFIED.

## 잔여
- NEEDS_INPUT: KIS 자격·일봉 어댑터.
- LIVE_UNVERIFIED: 가격 소스 전체.

# Checkpoint 12 — 데이터 계층 연결 (SELF_CHECKED_PENDING_INDEPENDENT_REVIEW)

- PCD≠readout(PCD 표기 유지)·AdCom≠PDUFA(분리 타입)·FAA license≠launch
  (분리 타입)·abstract/data 분리(6종).
- 동일 identity·다중 event·중복 document·관측·변경이력·parser 실패 경로
  구현(저장소 revision·관측·candidate).
- unresolved mapping·월/분기/NET/UNKNOWN/conflict → 진입 보류 근거 전달
  (WATCH_ONLY/REVIEW/CONFLICT 상태).
- earliest→직전 close→buffer deadline 계산(월요일→목요일 검증).
- at(as_of) 당시 revision만, first_seen 소급 없음(저장소 at_* 검사).
- daily 세션/finality/basis/split/batch/fallback/unknown quote age 구현.
- API 부족을 empty 성공·안전 quote로 위장하지 않음(EMPTY typed 분리).
- manifest: `work/evidence/runup_v2/manifest_12.json` (102 files).
- 자격 미확인·비용 미입력은 운영 제한으로 표시.
