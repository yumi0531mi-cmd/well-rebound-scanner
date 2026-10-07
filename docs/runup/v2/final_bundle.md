# 최종 검토 번들 (Step 31)

READY_FOR_CODEX_REVIEW. 독립 검토·배포·수익성 검증 완료를 대신 선언하지 않는다.

## 번들 위치
- `work/evidence/runup_v2/final_bundle_20261007/` (manifest.json 포함 7파일)
- 생성: `runup/export.py::build_bundle` (allowlist만, 비밀·원장 원문 제외, privacy pass)

## 내용
- `code_manifest.json`: runup 패키지·`runup_app.py`·`config.py` 파일별 sha256
- `config_contract.json`: schema 3, 실 프로필 hash, 필수 비용 설정 여부
- `source_status.json`: 실DB 표 개수(읽기 전용) + 출처 상태 5건
- `residuals.json`: NEEDS_INPUT 9건(비용 4·승인 이벤트 0·매핑 0·375px·출처 미확인·로컬 한정)
- `test_evidence.json`: 검증표 해시 + `pytest tests/runup_v2 tests/test_web_app_boundary.py` → 226 통과·2 기존 환경 실패
- `privacy.json`: 개수·해시만, 행 덤프 없음

## 범위
- 로컬 기능 완성 + 합성 검사. 실제 데이터 운영·원격 배포·수익성은 별도 상태.
- 기존 단타 코드 보존, dirty 변경 손대지 않음. 비밀·주문·송금·백테스트·배포 없음.
