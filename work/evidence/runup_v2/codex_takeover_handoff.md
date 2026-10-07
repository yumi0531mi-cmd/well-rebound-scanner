# 런업스캐너 — 새 AI 재개 지시
작성일: 2026-10-06 (사용자 한국시간 기준). 마지막 요청: 다른 AI가 이어서 구현한다.
이 문서는 완료 보고서가 아니라 실제 코드 상태와 다음 작업의 인계 지시다.

## 먼저 읽고 이어가기
실제 repo:
C:\Users\cj123\Documents\Codex\2026-08-27\e\work\strategy-filter-reentry
설계 패키지:
C:\Users\cj123\Documents\Codex\2026-10-05\new-chat\outputs\RUNUP_FULL_PLAN
설치된 정책:
docs/runup/autonomous/EXECUTION_POLICY.md
설계 원문:
docs/runup/v2/DESIGN_V2.md, CONFIG_CONTRACT_V2.json, API_CONTRACT_BLUEPRINT.md,
API_CONTRACT.md, VERIFICATION_MATRIX.md
검토 절차:
docs/runup/autonomous/REVIEW_PROTOCOL.md, CHECKPOINTS.md, FINAL_WEB_CHECKLIST.md
진행 파일:
PROGRESS.json의 runup_scanner.plan_v2
최신 인계:
work/evidence/runup_v2/codex_takeover_handoff.md

AGENTS.md와 PROGRESS.json을 먼저 읽고 git status를 확인한다.
같은 프로젝트에서 기존 코드와 부분 구현을 이어간다. 새 프로젝트/별도 Streamlit 앱/새 엔진을 만들지 않는다.
이 문서의 snapshot보다 실제 코드·최신 PROGRESS가 우선한다. 구현된 번호를 다시 만들지 않는다.
이전 작성일의 전체 보고·검사 개수를 전체 기능/실제 API/수익성 검증으로 바꾸지 않는다.

## 사용자 실행 승인
매번 '다음'을 기다리지 않고 31단계까지 이어가는 실행을 사용자가 요청했다.
새 AI에게 이 파일을 읽고 수행하라고 지시하면 아래 규칙으로 재개한다.
- 단계 25의 부분 구현부터 확인하여 완성한다. 25·26, 27·28, 29·30, 31 순서.
- 두 단계씩 구현 → 별도 검토 pass → 오류 수정 → 관련 검사 → 증거·진행 기록.
- 해당 묶음의 필수 검사를 통과하고 알려진 blocking 오류가 없으면 바로 다음 묶음.
- 독립 검토가 없다는 이유만으로 Codex를 기다리지 않는다. IMPLEMENTED와 ACCEPTED는 구분한다.
- 실제 오류·충돌·미입력은 숨기지 않는다. 운영 미입력은 해당 동작만 보류하고 독립 작업을 진행한다.
- 이미 승인된 전략 수치, 금지자료·secret·기존 단타 정책은 유지한다.
- 자동 주문·송금·실제 알림 발송·백테스트·최적화·push·배포는 승인 범위가 아니다.
- 기존 변경 파일을 reset/삭제하지 않는다. 변경해야 하는 함수만 보완한다.
- 최종 기능이 실제로 작동하고 필수 검사·자료가 갖춰지면 검토 bundle을 만든다.
- 미완성 필수 기능을 남겨 READY_FOR_CODEX_REVIEW라고 선언하지 않는다.
- 한도/세션 중단에 대비해 묶음마다 진행 파일과 증거를 저장한다.

## 현재 정확한 상태
01·02: 과거 Codex ACCEPTED 기록 유지. 이번에 전면 재검토하지 않음.
03~22: Muse IMPLEMENTED + 자체 검토 보고가 있음. 전체의 독립 검증 완료는 아니다.
23: 중단된 withdrawal 코드를 Codex가 보완. 관련 검사 통과, IMPLEMENTED.
24: worker lifecycle·lease·heartbeat·fencing·cursor 복구 구현. 관련 검사 통과, IMPLEMENTED.
25: 설정 service·daily scan·read model·risk overlay 작성, 일부 검사 통과. 작업자 handler 연결 등이 미완성.
26: dry-run outbox 작성, 자체 기능 검사 통과. 실제 판정/worker와의 연결까지 완료하지 않음.
27: 기존 app.py에 단타/런업 st.tabs 장착. 읽기 전용 런업 UI 검사 통과. 최종 UI 범위 검증은 미완료.
28: writer gate·수동 입력 일부·설정 widgets·모바일 카드 작성. 배분 폼 및 전체 command 연결 미완성.
29: operations.py와 일봉 provider 초안 작성. 운영/복구 검사는 아직 미실행.
30: 전체 통합·matrix 추적 미실행.
31: export 모듈/최종 bundle 미작성.
따라서 재개 번호는 25다. 25~29 파일이 존재한다고 해당 단계 완료로 기록하면 안 된다.

## Codex가 이번에 수정한 범위
23 관련:
- runup/portfolio/withdrawal.py
  중단된 DTO 필드 불일치 수정, 실제 WithdrawalPeriod 반환,
  P/H/M carryforward, 예약금/기존 earmark/현금 buffer/NAV floor,
  비용 미확인·stale NAV 보류, 표시된 proposal hash 재확인.
- runup/portfolio/ledger.py
  자금 command 전체 payload hash, profit/tax earmark 초과 출금 차단·해제,
  capital floor/H/earmark projection 연결, 닫힌 보유는 슬롯에서 제외.
- runup/storage/migrations.py
  4: job cursor·approval recorded_at, 5: run/readmodel/overlay 저장,
  6: outbox payload·상태 전환 저장. 현재 SCHEMA_VERSION=6.
24 관련:
- runup/services/jobs.py, __init__.py
  disabled 기본, DB lease, heartbeat 연장, stale fence commit 거부,
  handler가 network 단계와 DB commit 단계를 나누는 계약,
  cursor와 commit 원자성, 부분 실패·stop/restart.
- start.py
  기존 shared runtime 생성 후 jobs.start(runtime), 종료 cleanup 연결.
  기본 worker는 disabled. 아직 handler 등록이 없으므로 enable하지 말 것.
25~29 부분 구현:
- runup/services/config_service.py, scan.py, read_models.py
- runup/services/alerts.py, auth.py, commands.py, operations.py
- runup/ui/main.py, __init__.py
- runup/data/yahoo_daily.py
- app.py의 tab mount: 런업을 먼저 렌더하고 legacy 본문을 단타 tab container에 넣음.
  legacy statements의 AST가 mount 전과 동일한지 검사했다.
  백테스트 admin branch는 기존 위치·인증을 유지했다.
- config.py: 117개 RUNUP_CONFIG/default 변경 없음.
  별도 RUNUP_UI_POLICY.writer_grant_seconds=900만 추가.
- requirements.txt: exchange_calendars>=4.13,<5, yfinance>=1.4,<2 추가.
- tests/test_web_app_boundary.py: container 안 함수도 찾도록 AST traversal 변경,
  기존 조건/assertion은 유지. 회귀 검사 7개 통과.

기존 AGENTS/config/state/wellscan 등의 dirty는 시작 전부터 있었다.
wellscan/candidates.py, indicators.py, kis.py와 별도 단타 작업을 이번 Codex 작업으로 취급하거나 덮어쓰지 않는다.
git commit/push/deploy는 하지 않았다.

## 마지막 실제 검증
명령:
.venv\Scripts\python.exe -m pytest
 tests/runup_v2/integration/test_step_03_storage.py
 tests/runup_v2/integration/test_step_20_ledger.py
 tests/runup_v2/integration/test_step_21_positions.py
 tests/runup_v2/integration/test_step_23_withdrawal.py
 tests/runup_v2/integration/test_step_23_review.py
 tests/runup_v2/integration/test_step_24_jobs.py
 tests/runup_v2/integration/test_step_25_services.py
 tests/runup_v2/integration/test_step_26_alerts.py
 tests/runup_v2/integration/test_step_27_28_ui.py
 tests/test_web_app_boundary.py
 -o addopts= -q -p no:cacheprovider --basetemp work/<새로운 고유 검사 폴더>
결과: 51 passed, 4 warnings, exit0.
이는 선택된 합성 기능/AppTest/회귀 사례다. 전체 matrix 통과나 실제 시장 성과가 아니다.
경고: exchange_calendars 내부 NumPy timedelta deprecation.
py_compile app.py/start.py/commands.py/scan.py/main.py/yahoo_daily.py 통과.
수정 범위 ruff 통과(operations import 순서만 마지막에 수정).
실제 시장 데이터 API 호출/백테스트/실제 주문/송금/발송/배포: 이번 이어받기 작업에서 하지 않음.
AppTest는 앱 내부 화면·widget 검증이다. 실제 375px 브라우저 화면 캡처는 아직 없다.

## 먼저 해결해야 하는 남은 연결과 검토 항목
### 25 공통 서비스
1. jobs.start(runtime)에 source/daily/quote/event/scan/settlement handler를 실제 등록해야 한다.
   현재 RUNNING이더라도 등록된 job이 0개일 수 있다. disabled 기본을 유지하며 연결을 검사한다.
2. handler는 network를 transaction 밖에서 수행하고, fenced_commit 안에서는 저장만 수행한다.
   scan.run이 자체 transaction을 쓰므로 commit callback에서 그대로 호출하면 중첩 transaction 문제가 생긴다.
   준비 결과/commit을 분리하거나 명시 transaction 경계를 조정하고 fence 회귀를 검사한다.
3. scan.load_inputs는 코드 초안이다. 실제 DB rows→DTO 경로를 검사해야 한다.
   issuer/mapping 시점, security validity, 시장 cap 관측, source health, benchmark SPY,
   source/basis/finality/누락 세션, corporate action을 입력 계약대로 연결한다.
4. 입력 snapshot에 previous setup·consumed generation·revision을 포함하고 replay determinism을 검증한다.
   현재 run_id input hash와 previous setup 조회 사이의 누락·새 setup state 영향 가능성을 검토한다.
   service-level 인과성/prefix/미래 observation/다음 세션 trigger를 실제 fixture로 검사한다.
5. 현재 _current_positions(conn, as_of)는 모든 현재 positions rows를 읽는 기존 Muse 구현이다.
   as_of 재구축과 현재 snapshot의 경계를 엄격히 구분해야 한다. 과거 판정에 현재 보유를 소급하면 안 된다.
6. consumed generation 조회는 allocations.decision_id와 trigger_id를 직접 join하는 초안이다.
   실제 decision→trigger→generation lineage로 고쳐 같은 신호 재소비를 차단한다.
7. read_models.load의 ledger.revision은 현재 cutoff 시각 기반 문자열이다.
   cache revision에 원장 event/예약/earmark 등 실제 변경을 포함해야 한다.
8. source 미수집이면 UNAVAILABLE. 정상 무후보와 parser/DB 실패를 분리한다.
   최근 결과·last-good·partial의 원인, 설정 hash와 최신성 표시를 유지한다.
9. daily scan과 risk overlay를 실제 worker/read model에 연결한다.
   장중 위험 갱신이 daily feature/setup/trigger를 재계산하지 않아야 한다.
10. 현재 critical overlay 검사는 통과했지만 stop/quote·이벤트 deadline 전체 연결은 추가 검사 필요.

### 26 outbox
실제 판정 전환과 outbox enqueue/dry-run을 연결한다. 현재 단독 기능 검사만 있다.
이벤트 ID/세대와 누적 target 증가·상태 회복을 구분하며 날짜만 바뀌면 중복 알림을 만들지 않는다.
실제 transport/Telegram 발송은 추가하지 않는다. dry-run delivered=False를 유지한다.

### 27·28 UI/commands
- server grant는 기존 WELLSCAN_ADMIN_TOKEN 검증 재사용, HMAC 서명·만료·서버 재검증.
  token 값/.env를 도구·문서·로그에 노출하지 말 것.
- 모든 쓰기는 Commands를 통과해야 한다. 같은 이름의 engine/ledger low-level 함수를 UI에서 직접 호출하지 않는다.
- 수동 체결·입금/출금·결제·정산·설정 form이 작성돼 있다.
  매도 예약/취소·근거 검토 advanced JSON 경로는 초안이며 실제 submit/유효기간·evidence 사례 검사가 필요.
- 배분 proposal 생성/표시/최신 revalidation/승인 form은 아직 없다.
- capital/settle/advanced/withdraw command ID가 세션에 계속 남는 부분을 검토한다.
  같은 제출 retry는 멱등, 새 사실 입력은 새 command ID가 되도록 명시 new-intent 동작을 제공한다.
  fill에만 현재 새 입력 버튼이 있다. recorded_at의 매 retry 변경이 기존 hash와 충돌하는지도 보완한다.
- 정산 form 승인 전에 최신 profile/ledger/NAV와 displayed proposal hash를 재확인한다.
  config 변경 후 오래된 session preview가 승인되지 않게 검사한다.
- 설정117개 schema kinds는 boolean/integer/number/string/enum/path/
  integer_tuple/number_tuple/bounds_tuple/weight_map이다. 현재 UI parse는 이 종류를 따른다.
  전체 기본값 form 저장 roundtrip, nullable 비용4개, 경계/오류 표시를 검사한다.
- 카드와 table은 같은 모델을 사용하지만 분야 필터는 일부 영역만 적용돼 있다. 캘린더/후보/보유 일관성 보완.
- 초깃값/empty/partial/unknown price, 기존 1초 fragment/구독/전역 stop/admin branch 회귀 유지.
- 앱 탭 렌더는 network/scan/worker start를 하지 않는다.
- 실제 PC/375px 브라우저 레이아웃 검증과 전체 사용자 흐름은 아직 미완료.

### 기존 18~22 핵심 검토에서 보인 P0 후보
아래는 읽기 중 발견한 코드 결함 후보이며 아직 수정·재현 검사를 완료하지 않았다.
테스트가 통과했다는 이유로 무시하지 말고 먼저 작은 사례로 재현해 수정한다.
- engine/exit.py stop 비교가 min(initial_stop,trailing_stop)을 사용한다.
  더 높은 유효 stop을 적용해야 하는 계약과 맞는지 검증한다.
  stale/received-only quote로 FULL을 확정하지 않도록 stop.evaluate와 time_quality 계약을 연결한다.
- exit.py deadline 비교가 YYYY-MM-DD만 비교한다. 정확한 deadline instant·timezone 경계를 검증한다.
- portfolio/rollover.py approve는 reserved 합계를 SQLite REAL로 더한다. Decimal 정밀도로 변경해야 한다.
- rollover approve의 현금 계산은 예약 외 profit/tax earmark·NAV buffer를 충분히 차감하지 않는 경로가 있다.
  수수료 미입력/NAV stale/profile/신호/이벤트 최신 revision/동시 슬롯/issuer·sector 한도를 검토한다.
- approve 시점의 DB ledger 재구축과 stale context/cash double-spend 차단을 확인한다.
- scan/원장 미래 revision과 기업행동 basis 변화 연결은 서비스 단위 인과성 검사로 보완한다.
이 후보를 무조건 사용자 요구사항 변경으로 처리하지 않는다. 합의된 명세에 맞게 작은 버그 수정이다.

### 29 운영·무료 일봉 수집
- operations.py는 new-target backup/restore/health 초안. corrupt/backup failure/profile mismatch/
  workspace 탈출/symlink/새 sandbox restore 검사를 추가한다. production overwrite 금지.
- runup/data/yahoo_daily.py는 yfinance adapter 초안이며 fetch/provider 최종 연결과 parser 검사가 필요.
  yf.Ticker.history(auto_adjust=False,back_adjust=False,repair=False,keepna=True,actions=True,
  rounding=False,raise_errors=True,timeout=...)를 사용하고 metadata.currency=USD 확인.
  Yahoo Close는 split-adjusted, Adj Close(배당 포함)를 섞지 않는 의도다. 실제 basis를 추가 검증할 것.
  Yahoo는 비공식 공급 경로이며 KIS 공식 성과 검증 자료라고 표시하지 않는다.
- 가격 provider는 사용자가 새로 등록한 미국 equity와 benchmark에도 연결해야 한다.
  매 요청을 같은 session/revision/source로 덮어쓰지 말고 원문과 최초 available_at 이력을 보존한다.
- yfinance는 가상환경에 실제 1.7.0 설치 완료. 기존 exchange_calendars는 4.13.2.
  .venv에는 pip 모듈이 없다. uv 사용:
  uv --cache-dir work/uv-runup-cache pip install --python .venv\Scripts\python.exe <의존성>
- 기본 sandbox 네트워크로 PyPI 연결 실패 후 해당 무료 패키지 설치만 승인된 권한으로 수행했다.
  다른 API 접속·데이터 수집 성공으로 취급하지 않는다.
- 공식 참조: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
  실제 설치 PriceHistory.history signature 확인 완료. 실제 시장 smoke는 미실행.
- requirements와 운영 문서의 호환성, ephemeral cloud disk와 durable data 보존을 구분해 기록한다.

### 수집 계층의 운영 상태
CT 실제 샘플을 Muse가 남겼지만 이번 코드 인계 시점에 실제 API coverage를 재검증하지 않았다.
SEC 식별 User-Agent, IR allowlist, 학회/SPACE 수동 근거 등의 미입력·LIVE_UNVERIFIED 기록을 보존한다.
현재 FDA/학회/SPACE collect에는 UNSUPPORTED/manual 경로가 남아 있다.
필수 manual ingest/review 경로가 화면/worker/DB로 작동해야 기능 구현으로 인정한다.
Universe/ticker/issuer/sector 검증을 추측으로 자동 승인하지 않는다.
ClinicalTrials PCD는 readout가 아니다. AdCom/PDUFA, FAA license/launch를 섞지 않는다.
최초 인지 시각·취소·일정 변경·NET/월/분기를 임의 특정 날짜로 바꾸지 않는다.
HTTP real_transport는 urllib의 기본 redirect/body 처리 경로를 추가 검토해야 한다.
제안했던 NoRedirect/DNS·size 보호 patch는 적용하지 못했다. 실제 파일 기준으로 검사/보완한다.

## 마무리 30·31
VERIFICATION_MATRIX의 각 ID→실제 검사명·기대값 근거·결과를 trace에 연결한다.
필요할 때 전체 기능 suite를 한 번 실행하고 실패를 해당 구현 단계에서 수정한다.
테스트 개수만으로 matrix 전량 통과를 선언하지 않는다.
현재 자료 미입력/실제 접속/최종 배포/수익성은 별도 상태다.
실제 계좌·비밀정보·auth cache·DB 전체를 export하지 않는다.
최종 자료는 허용된 코드 manifest,설정 계약,합성 cycle,검사,UI 증거,출처 상태,잔여 목록이다.
기능이 미완성이면 최종 보고에 정확히 남긴다. 배포 및 수익 검증 완료라고 쓰지 않는다.

