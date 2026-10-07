# Step 31 export·최종 검토 번들 증거 (2026-10-07)

## 변경 파일
- `runup/export.py`: 신규. `build_bundle()` — 코드 해시·설정 계약·출처 상태·개수·잔여·검사근거를 allowlist 7파일로 묶는다. 원장·보유·예약·정산 행 덤프 없음, 비밀값 패턴 검사 실패 시 번들 삭제 후 오류. 목적지 덮어쓰기·workspace 밖·민감 이름 거부(`operations._new_target` 재사용).
- `docs/runup/v2/final_bundle.md`: 신규. 번들 위치·내용·범위 + READY_FOR_CODEX_REVIEW(독립 검토·배포·수익성 선언 아님).
- `tests/runup_v2/integration/test_step_31_export.py`: 신규 2건(allowlist·해시·프라이버시·덮어쓰기 거부, 경로 가드).

## 검사
- 신규 2건 통과, `ruff` 통과
- 전체 1회: 228 수집 중 226 통과, 2 실패(기존·환경: 작업 전 dirty `app.py`, basetemp workspace 밖)
- 실DB 쓰기 0건(번들은 읽기 전용 집계만)

## 번들
- `work/evidence/runup_v2/final_bundle_20261007/` (manifest.json 포함 7파일, privacy pass)
- 실DB 집계: 종목 180·후보 100·승인 0·매핑 0·일봉 825(3종목)·원장 0건

## 잔여(NEEDS_INPUT, 완화 없음)
- 비용 4종·승인 이벤트·매핑 검토·375px 실기기·출처 전체 커버리지·원격 배포·수익성 검증
