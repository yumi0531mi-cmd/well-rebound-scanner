# V2 Step 04 증거 — HTTP·출처 보존·source registry

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: stage_03 IMPLEMENTED(묶음 checkpoint).

## 변경 파일
- `config.py`: `RUNUP_SOURCE_POLICIES` 10건 + `resolve_source_policy()`
  추가만. 117개 설정·기본값·schema·hash 규격 불변(검증됨).
- `runup/data/http.py` (신규): GET adapter(transport 주입),
  scheme·사설/metadata/loopback 차단, redirect 재검사·loop·한도,
  size 상한(원문 미저장), timeout 전달, Retry-After 예산 초과 즉시
  RATE_LIMITED, 일시 5xx/timeout bounded 재시도, 4xx 무재시도,
  200 빈 본문 EMPTY_CONFIRMED, 증거 credential 미포함.
- `runup/data/provenance.py` (신규): 출처 결정 ID·hash·first_seen/
  available_at 증거 DTO, blob 저장, 수집 실행 기록 builder.
- `runup/data/source_registry.py` (신규): scope/capability/official URL,
  공통 속도·예산 key 참조(이름만, 숫자 복제 없음), effective 해석은
  config 실시간 참조. 전 소스 SUPPORTED_MANUAL+LIVE_UNVERIFIED,
  완료 표기 없음. SEC 접근정보 미입력 → NEEDS_INPUT(값 없음).
- `tests/runup_v2/unit/test_step_04_http.py`: 신규 11건(no-network).

## 계약
- `fetch_document(request,policy)->FetchOutcome`,
  registry capability 정확 표시. HTTP 성공과 parser 성공 분리.
- 파일 해시(앞 16자): http `0d9d085c0518fd52`,
  provenance `8b98676540d11ffb`, registry `68fda8edd4277802`,
  test `4de8291f4e1e761f`.

## 검사
- `pytest tests/runup_v2/unit/test_step_04_http.py`:
  **11 passed, 0 failed**. 200빈/200바이트/timeout/429예산초과/
  일시5xx/403무재시도/size초과/사설·loop·redirect/credential 차단/
  수집→저장→관측→cursor 순서/registry 정직 표시.
- `ruff check` 통과. 실제 HTTP 요청 0건·백테스트·push·배포 없음.
  fixture와 실제 API 확인 분리(실제 미수행).

## 잔여
- NEEDS_INPUT: SEC User-Agent/접근정보 미입력(sec만, 값 없음).
- LIVE_UNVERIFIED: 전 소스 실제 smoke 미수행.
- 다음 묶음 05·06은 별도 지시 전 시작하지 않음.
