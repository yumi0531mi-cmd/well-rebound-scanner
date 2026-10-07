# V2 Step 01 증거 — 설정·schema·진행 복구 확정

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: 기존 Step00 ACCEPTED + V2 bootstrap 완료. 구 Step01 구현을 읽고
  덮어쓰기 없이 V2 부족분만 수정.

## 변경 파일
- `config.py`: allocation_expiry 기본값 V2 명시 변경
  (`next_session_close`), 26개 필드 추가, RUNUP_SCHEMA 117건 완성,
  교차 검증(ma 오름차순·dryup 순서·heartbeat<lease·retry 상한·validity==1),
  unknown/missing 키 오류(Nullable 누락 포함), hash payload에
  schema_version=2, `resolve_config_snapshot()` 추가(DB 없음).
  기존 단타 설정·dirty untouched.
- `state_manager.py`: 기존 함수·signature 보존, plan_v2 stages 전용
  `get_plan_stage`/`set_plan_stage` 추가(V2 상태, ACCEPTED는 reviewed_by 필수).
- `runup/__init__.py`: 변경 없음.
- `tests/runup_v2/unit/test_step_01_config.py`: 신규 11건.
- `tests/unit/test_runup_config_review.py`: V2 unknown=ERROR에 맞게 기대값 1건 수정.

## 계약
- set(RUNUP_CONFIG)==set(RUNUP_SCHEMA)==set(contract.rules)==117건.
  defaults 계약 일치. hash `c893e6b5…49c0f0`. 기본 설정 errors 0,
  config_required 4건(비용 None).
- 파일 해시: config.py `baaa74ec…a3cfda40`,
  state_manager.py `af508bf3…b32eca77`,
  test_step_01_config.py `b2afb24d…12244cb9e3529e`.

## 검사
- `pytest test_step_01_config + 기존 runup config 3종 + test_config_state`:
  **51 passed, 0 failed**.
- `ruff check` 5파일 통과. 백테스트·최적화·실제 API·push·배포 없음.
- fixture(합성)만 사용. 수익성 미검증.

## 잔여
- 비용 4건 None → allocation CONFIG_REQUIRED 유지(사용자 broker 입력 필요).
- NEEDS_INPUT 없음. 다음은 Step 02(도메인·계약), 선행 ACCEPTED 후 진행.

## Repair (REVIEW S01~S09 반영, 2026-10-06)
- 상태 유지: IMPLEMENTED. ACCEPTED 선언 없음. Step 03 미시작.
- targets 마지막=1·전부>0 강제, runup_db_path workspace-relative·비밀파일명
  거부, V2 정책 bools(alerts_dry_run True·atr_expand False) 강제,
  profile명 UNVALIDATED suffix를 hash·resolve·validate에서 검사,
  canonical payload={schema_version,profile_name,config}로 명세 일치,
  canonical_config_json 공통 함수, snapshot 중첩 불변 freeze + 명시 export,
  artifact root 이탈·절대경로·secret명·symlink 거부(읽지 않음),
  plan stage 문자열/dict 정규화, validator 예외 누출 차단(sorted key=str·
  순서·중복 안전 비교), RUNUP_SCHEMA 계약 어휘화 + 스키마 구동 검증,
  IANA timezone·US symbol 포맷, timezone은 고정값으로 제한하지 않음.
- 기본 설정 errors 0·config_required 4건 유지. hash 변경(값·payload 규격):
  `d7efef061186f9ff60d244769cc324b8a46479321368651f032766d8456fea5f`.
- 검사: repair 12건 포함 Step 01 범위 전량 통과(아래 전체 결과 참조).
  `ruff check` 통과. 백테스트·최적화·실제 API·push·배포 없음.
