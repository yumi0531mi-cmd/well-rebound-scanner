# 보유 위험·화면 연결 증거 (2026-10-07, 3번 작업)

## 변경 파일
- `runup/services/read_models.py`: 공통 read model 확장(기존 Dashboard 변경 없음)
  - `display_times`: KST·ET 읽기 표시 + UTC 상세(파싱 실패 시 원문)
  - `candidate_cards`: 티커/기업/분야/이벤트/D-day/상태/진입가능/차단사유 요약, 긴 ID·hash는 detail로 분리
  - `calendar_rows`: 승인(유형·정밀도·시작·상태·문서 URL·revision 이력·티커)과 대기(식별자·제목·출처·유형·날짜·정밀도·티커 미연결) 구분
  - `latest_overlays`/`position_risk`: 보유별 QUOTE_ONLY 최신(수신·거래 시각 분리), 당일 exit, `자동 감시 아님` 명기
  - 수정한 실제 버그: 승인 문서 ID가 pack 문자열이라 URL이 항상 비던 경로
- `runup/ui/main.py`: 첫 화면 카드(요약 표 + 상세 expander), 모바일 식별 제목(후보 티커·이벤트·상태 / 대기 NCT·유형·제목), 현금 `수동 원장·실제 계좌 미연결` 명기, 작업자 정직 표시(DISABLED≠감시 중), `_rows` 제목 중복 방지
- `tests/runup_v2/integration/test_risk_screen.py`: 신규 6건(시각·카드·캘린더·보유연쇄·순수 overlay·AppTest 렌더/모바일/문구)

## 검사
- 신규 6건 + UI 기존 4건(마운트 제외): 10 passed
- `tests/runup_v2` 전체: 215 passed, 2 failed(기존·환경 문제로 제외하고 동일)
- `ruff check` 3파일 통과

## 실제 DB 대조(읽기 전용, 변경 없음)
- 보유 0건·승인 이벤트 0건·비용 None이므로 화면은 전부 차단·대기·0 원장 표시가 정상
- QUOTE_ONLY 오버레이 0건 → 보유 위험 `QUOTE_UNKNOWN`, 배분 승인에 미확인 시세 사용 없음 유지

## 잔여
- 375px 실기기 확인은 미수행(NEEDS_INPUT, 장비 없음). AppTest 클릭·토글·폼 상호작용으로만 검증, CSS 존재로 통과 처리 안 함
- 다음: 4. 30단계 실제 통합 검사와 검증표 작성
