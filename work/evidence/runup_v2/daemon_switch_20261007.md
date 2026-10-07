# 단타 데몬 on/off 스위치 증거 (2026-10-07)

## 변경
- `start.py::daemon_enabled()`: `WELLSCAN_DAEMON_ENABLED=0/false/no/off`면 레거시 데몬 미기동, UI는 그대로. 기본값 켜짐(기존 동작 유지).
- `render.yaml`: `WELLSCAN_DAEMON_ENABLED` 키 등록(값 없음, 사용자가 대시보드에서 입력).
- `tests/test_daemon_switch.py`: 신규 2건(기본 켜짐·끄면 데몬만 제외).

## 검사
- 신규 2건 + 기존 진입점 검사 1건: 3 passed. `ruff` 신규 파일 통과(`start.py` 기존 import 정렬 1건은 손대지 않음).

## 사용자가 끄고 켜는 곳
- 클라우드: Render 대시보드 → 서비스 → Environment → `WELLSCAN_DAEMON_ENABLED`에 `0`(끄기) / 삭제 또는 `1`(켜기)
- PC: `setx WELLSCAN_DAEMON_ENABLED "0"` 후 bat 재실행. 삭제하려면 `setx` 빈값 불가이므로 설정→계정→환경 변수에서 지움
