# V2 BOOTSTRAP 문서 설치 증거

- 일시(UTC): 2026-10-06
- 패키지: `C:\Users\cj123\Documents\Codex\2026-10-05\new-chat\outputs\RUNUP_MUSE_V2` (version 2.0)
- 실제 프로젝트: `C:\Users\cj123\Documents\Codex\2026-08-27\e\work\strategy-filter-reentry`
- 수행: BOOTSTRAP.txt 지시만. Python 구현 변경 없음. Step01 구현 미시작.

## 설치 (원문 그대로, 37건)
- `docs/runup/v2/`: DESIGN_V2.md, CONFIG_CONTRACT_V2.json,
  API_CONTRACT_BLUEPRINT.md, SOURCE_NOTES_V2.md, VERIFICATION_MATRIX.md,
  STEP_INDEX.md (6건)
- `docs/runup/v2/prompts/`: STEP_01.txt ~ STEP_31.txt (31건)
- 전량 PACKAGE_MANIFEST.json의 bytes+sha256과 설치 전후 대조 일치.
  대표 해시(앞 12자): DESIGN_V2 `1556d6b8f583`, CONFIG_CONTRACT_V2
  `af64a7bc8fb6`, API_BLUEPRINT `fef9e2ec91cd`.

## 기존 보존
- V1 설계 5건 + integration_map.md + DECISIONS.md 삭제·덮어쓰기 없음.
- 구 Step01 IMPLEMENTED(ACCEPTED 포함)와 runup 코드·테스트·증거 그대로 보존.
- 기존 dirty(AGENTS.md, config.py, state_manager.py, wellscan/* 등) 미접촉.
- AGENTS.md 비밀정보·금지자료·배포 규칙 확인. secret·.env 미열람.

## Step00 재사용 근거
- `app.py`, `start.py`, `config.py`, `state_manager.py` 존재 확인.
- `runup_scanner.milestones.step_00`: ACCEPTED, reviewed_by=Codex,
  evidence=`work/evidence/step_00.md` (2026-10-06T02:09:23Z).
- V2 stage_00은 inherited ACCEPTED로 기록하며 original evidence와
  reviewed_by 링크를 유지. 독립 재검토·Step01 ACCEPTED 선언 없음.

## PROGRESS
- `runup_scanner.plan_v2`: version=2.0, stage_00=inherited ACCEPTED,
  stage_01~31=NOT_STARTED, next_stage=01. 기존 키·milestone 보존.
- 신규 plan_v2이므로 중복 생성 아님.

## 잔여
- STEP_01.txt 별도 전달 전 Step01 구현 금지 준수. STOP.
