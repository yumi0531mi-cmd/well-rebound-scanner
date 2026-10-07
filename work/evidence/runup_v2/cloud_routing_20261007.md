# 클라우드 원격 라우팅 증거 (2026-10-07)

## 변경
- `runup/ui/main.py::render`: `db_path` 미지정 시 `RUNUP_DB_URL` 있으면 원격 연결, 없으면 기존 로컬. 명시 경로 지정 시 기존 로컬 동작 유지(검사 그대로 통과).
- 읽기 실패는 `Exception` 포괄 표시, close 실패 무시(원격 Espanol 대응).
- 쓰기 연결 `connect(db_path)` 그대로 → None이면 env 라우팅.
- `test_remote_backend.py`: env 라우팅 검사 추가(총 7건).

## 검사
- 신규 포함 7건 + UI 11건 중 기존 1건 제외 전부 통과, `ruff` 통과.
- 푸시 `c0734fa` 원격 확인. Render 자동 재배포됨.

## 주의(3번째 푸시)
- 하루 1회 원칙 예외. 배포 복구라는 사용자 지시로 진행, 기록 남김.
