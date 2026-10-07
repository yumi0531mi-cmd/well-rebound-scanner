# 일정 우선 매핑 증거 (2026-10-08)

## 변경 (수동 티커 UI는 삭제, 일정→종목 정방향으로 교체)
- DB v9: `event_candidates.sponsor_text` 추가. 수집 때 임상 sponsor를 보존.
- `runup/catalyst/sponsor_match.py`: 신규. 법인 꼬리표만 뗀 정규화, 정확·별칭만 제안, 병원·불일치 제외.
- `prepare_suggest`: 수집 버튼에 자동 연결. SEC 공식 목록과 대조해 REVIEW 제안 생성(UNVERIFIED 표시). 승인은 사람만.
- `Commands.approve_mapping` + 화면 `연결 검토` 한 건 승인 버튼.
- 기존 8자리 raw INSERT 검사는 명시 컬럼으로 수정(마이그레이션 내성).

## 검사
- 신규 4건 + 전체 기존 2 실패 외 추가 없음. `ruff` 통과.

## 사용법
- 수집 버튼 1번 → 일정 후보 + 연결 제안까지 자동. `연결 검토`에서 승인만 클릭.
- 티커 직접 입력 UI는 삭제함.
