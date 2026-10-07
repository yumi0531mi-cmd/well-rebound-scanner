# 클라우드 DB 어댑터 증거 (2026-10-07)

## 변경
- `runup/storage/remote.py`: 신규. Turso/libSQL Hrana 래퍼. mapping+sequence 겸용 Row, cursor(rowcount/lastrowid), 대화형 transaction. URL·토큰 로그 금지.
- `runup/storage/database.py`: `connect()`가 `RUNUP_DB_URL` 원격이면 원격 연결(토큰 없으면 차단 오류), 아니면 기존 로컬. `transaction()` 원격 분기 추가.
- `requirements.txt`: `libsql-client>=0.3,<1` 추가(Render 빌드용).
- `tests/runup_v2/integration/test_remote_backend.py`: 신규 6건(행 접근·번역·커밋/롤백·중첩 거부·라우팅/토큰 게이트·migrate 문장 흐름). stub Hrana, 네트워크 없음.

## 검사
- 신규 6건 통과, `ruff` 통과, 전체 스위트 기존 2 실패 외 추가 없음.

## 수정(2026-10-07 야간): 공식 libsql 패키지로 교체
- 원인: `libsql-client 0.3.1`(방치됨)은 hrana2만 보내서 현 Turso가 400 거부. 네이티브 `libsql`에 3.14 휠이 없어 3.12 별도 venv에서 실물 API 검증(connect 서명·description·lastrowid·raw 트랜잭션·row_factory 없음).
- `remote.py`를 얇은 래퍼로 재작성. 토큰 키워드 전달. 검사 6건 통과. 푸시 `58934e8`.
- `requirements.txt`는 `libsql`로 교체(Render 3.12 휠 확인됨).

## 잔여(실제 Turso 필요)
- 실제 Turso DB 연결·마이그레이션·행 동작 검증 미수행(자격증명 없음). 사용자가 Turso DB 만들고 `RUNUP_DB_URL`·`RUNUP_DB_AUTH_TOKEN`을 Render에 직접 넣은 뒤 검증 필요.
- Render 런업 별도 서비스 등록·배포는 승인 후.
