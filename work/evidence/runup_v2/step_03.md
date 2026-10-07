# V2 Step 03 증거 — SQLite·이력·transaction·profile 저장

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: 잔여 mapping-shape 수정 통과(묶음 checkpoint), stage_02 IMPLEMENTED.

## 변경 파일
- `runup/storage/database.py` (신규): WAL·FK·busy_timeout 연결,
  BEGIN IMMEDIATE transaction, bounded 재시도, exclusive-consistent
  backup/restore(비밀명·밖경로 거부, 덮어쓰기 금지).
- `runup/storage/migrations.py` (신규): §6 29개 테이블·UNIQUE·FK·index,
  versioned·transactional 적용(문장 단위 실행), 재실행 무해, drop/reset 없음.
- `runup/storage/repositories.py` (신규): 문서/관측/수집cursor/후보/
  revision append+tombstone/`at()`/가격 revision/원장 bundle 원자성/
  receipt 멱등·충돌/profile 불변+CAS/lease fencing/outbox/
  snapshot·scan insert. 금액 Decimal canonical text.
- `tests/runup_v2/integration/test_step_03_storage.py`: 신규 11건.

## 계약
- `transaction()` 컨텍스트, `repositories.at_*` 계열, command receipt
  (APPLIED/REPLAY/CONFLICT), active profile compare-and-swap.
- 파일 해시(앞 16자): database `0e1d42ab09fda166`,
  migrations `8eab0f7407514e10`, repositories `9f8b2f0724f81747`,
  test `2a315c3eeec6457d`.

## 검사
- `pytest tests/runup_v2/integration/test_step_03_storage.py`:
  **11 passed, 0 failed**. 신규DB/재오픈/마이그레이션/FK/UNIQUE/
  Decimal 정밀도/replay/conflict/rollback/미래 제외/과거 취소 미소급/
  동일원문 관측 보존/cursor 순서/backup·복원/재시도/lease/profile CAS/
  가격 revision 이력.
- `ruff check` 통과. 실제 API·백테스트·push·배포 없음. 합성 fixture만.

## 잔여
- NEEDS_INPUT 없음(비용 None은 Step 01 영역으로 유지).
