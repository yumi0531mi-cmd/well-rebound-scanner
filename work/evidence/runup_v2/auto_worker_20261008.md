# 자동 수집 연결 증거 (2026-10-08)

## 변경
- `runup_app.py`: `maybe_start_worker()` — DB 활성 프로필 `runup_worker_enabled`이 켜져 있을 때만 작업자 시작. import 안전(`__main__` 가드).
- `runup/services/jobs.py::start`: `db_path` 인자 추가(기본 기존 동작). 원격 DB일 때 로컬 파일로 빠지지 않음.
- `tests/runup_v2/integration/test_app_worker.py`: 신규 3건(작업 묶음 완비·꺼짐 무작용·스위치 불리언).

## 검사
- 신규 3건 통과, `ruff` 통과. 푸시 `e431dcc`.

## 켜는 법(사용자)
- 화면 설정에서 `자동 수집 켜기` 체크 → 저장. PC 앱 재시작(또는 클라우드 재배포) 후부터 버튼 없이 수집됨.
- 클라우드 무료는 잠들면 멈춤. 자주 깨우려면 UptimeRobot 등록 유지.
