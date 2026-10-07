# API 계약 작성 기준 V2
이 부록은 Step02가 API_CONTRACT.md를 작성할 때 반드시 포함할 field 계약이다.
이후 구현은 임의 dict/context를 추가해 계약을 우회하지 않는다. 새 field 필요 시 충돌·추가 이유를 먼저 보고한다.

## 공통 입력과 책임
EvaluationClock: as_of UTC aware,session_date,previous_session,next_session,market_open/close,calendar_version.
ConfigSnapshot: schema_version,profile_name,profile_id,config_hash,values(117개 검증된 immutable 설정).
SourceHealthSnapshot: source_id,scope,capability,last_success,last_failure,coverage,status,revision.
MarketContext: clock,security,issuer,UniverseObservation,관련 source health,CatalystRevision 목록,RiskNotice 목록,
 CorporateAction 목록,최초 risk boundary,forced_exit_deadline,importance,gate_issues.
BarContext: clock,security,source_id,basis,완성 PriceBar 목록,benchmark 목록,bar_hash,completeness,issues.
Pivot: pivot_id,kind,session_date,price,confirmed_session,available_at,basis.
PivotSet: confirmed highs/lows,HL/HH FeatureValue,reference_high_known_previous_session,reference_low,issues.
SetupContext: MarketContext,ConfigSnapshot,FeatureSnapshot,PivotSet,previous SetupSnapshot?.
TriggerContext: SetupContext,유효 SetupSnapshot,직전/현재 확정 C와MA,ATRprev,RVOL/CLV,consumed generation 목록.
ScoreContext: ConfigSnapshot,FeatureSnapshot,직전 FeatureSnapshot,PivotSet,frozen trigger ref?,현재/직전 확정 bar.
StopContext: ConfigSnapshot,PositionProjection,QuoteObservation?,FeatureSnapshot?,PivotSet?,CorporateAction 목록,clock.
ExitContext: StopContext,ScoreResult?,MarketContext,기존 누적 target,활성 sell reservation 목록.
LedgerProjection: revision,settled_cash,unsettled_cash,reservations,profit/tax earmarks,positions,realized_P,
 net_capital_floor,processed_H,monthly_realized,issues,reconciliation_status.
NAVSnapshot: as_of,USD NAV,각 보유의 known marketvalue/price source/시간,external_other_exposure,issues.
AllocationContext: clock,ConfigSnapshot,유효 Decision/Trigger 목록,issuer/sector/security metadata,
 fresh QuoteObservation 목록,initial stop references,NAVSnapshot,LedgerProjection,관련 MarketContext.
SettlementContext: clock,ConfigSnapshot,LedgerProjection,NAVSnapshot,NY period,confirmed fee/tax policy,previous period revision.

## 엔진 출력
GateResult: passed bool,issues(list),evidence_ids. missing 자료는 Issue이고 정상 false와 다르다.
SetupResult: state WATCH/SETUP/INVALID/UNAVAILABLE,score?,components,generation_id?,expires_session?,reasons,snapshot.
TriggerResult: eligible bool,trigger_id?,generation_id?,ref_pivot_id?,valid_until?,reasons,snapshot.
ScoreResult: score?,components(component별 FeatureValue),status,reasons,input_hash.
StopResult: initial_stop?,trailing_stop?,hard_stop?,structure_stop?,quote_quality,
 action NONE/POSSIBLE_STOP/FULL,reasons,known_at.
ExitDecision: action HOLD/PARTIAL/FULL/REVIEW,누적 target,추가 qty,qty_basis,priority_reasons,parallel_review,
 decision_id,input_hash. FULL target라도 quote 부족으로 실행가격은 unknown일 수 있다.
AllocationProposal: proposal_id,security/issuer/sector,decision/trigger/config/quote/ledger/NAV/event revision,
 qty,budget,cost/risk estimate,reserve_estimate,expires_at,reasons,status.
SettlementProposal: period/revision,P,H,M,new,available_cash,headroom,processed_base,
 proposed profit/tax earmarks,config/ledger/NAV revisions,status,reasons.
금액 출력은 Decimal이다. score/feature float는 finite 검증한다.
propose/evaluate는 DB 변경 없이 결정적 결과를 반환한다. 명시 입력이 같으면 같은 결과/ID다.

## Command와 권한
모든 쓰기 서비스는 command_id,payload_hash,actor_id,server-verified auth_context,occurred_at/recorded_at을 받는다.
UI disabled는 권한 검사가 아니다. public read model 호출과 command 실행 권한을 분리한다.
ReviewCommand: candidate/revision ID,approve/reject,정밀도/날짜/timezone/issuer/program/type/class,
 근거 document IDs,reviewer,expected_revision.
FillCommand: Fill,예상 ledger revision,지원 allocation ID?,실제 증거,정정/reconciliation 선택.
SettlementCommand: 미결제 source ledger IDs,amount,confirmed settlement date,증거,expected ledger revision.
ReserveExitCommand: position/decision IDs,qty,expiry,expected position/ledger revision.
CapitalFlowCommand: DEPOSIT/CAPITAL_WITHDRAWAL/PROFIT_WITHDRAWAL/TAX_WITHDRAWAL,amount,earmark ID?,date,evidence.
CorporateActionCommand: action ID,verified ratio/net amount,effective session,source evidence,expected revisions.
ProfileCommand: complete validated config,profile_name,expected active profile ID,schema_version.
AllocationApprovalCommand: proposal_id,expected input revisions,latest_context,command_id.
WithdrawalApprovalCommand: proposal_id,period/revision,expected input revisions,latest_context,command_id.
LedgerCommandResult: status APPLIED/REPLAY/REJECTED/RECONCILIATION_REQUIRED,command_id,ledger revision,
 event IDs,projection,issues.
쓰기 transaction은 command receipt·원장·projection·reservation을 함께 commit한다.
proposal을 만들 때 최신 소스를 읽고, 승인 transaction은 저장된 latest quote/event/profile/ledger revision과 비교한다.
DB transaction 안에서 HTTP를 호출하지 않는다. 입력 revision이 바뀌거나 age/deadline이 넘으면 거부하고 새 proposal을 요청한다.
동일 ID/같은 payload replay는 현 상태에 추가 side effect가 없다. 동일 ID/다른 payload는 conflict다.

## Lifecycle와 read model
JobContext: job_id,handler kind,clock,config snapshot,cursor,lease owner,fencing token,budget,deps.
job handler는 phase별 결과와 cursor checkpoint를 반환한다. lease fencing 검사는 commit boundary에서 한다.
ScanRun: run_id,as_of,profile/input hashes,status,decisions,health,input revisions,finished_at/errors.
RiskOverlay: security_id,observed_at,daily decision ID,event/quote revisions,stop/exit recommendation,health.
UIReadModels: overview/calendar/candidates/positions/cash/settings/health,read_model revision,
 daily as_of/risk observed_at,profile hash,source capability/coverage,issues.
권한 없는 read model에는 token·account·원문 secret을 포함하지 않는다.
on-demand 버튼은 명시 command로 job request를 등록하며 렌더 함수가 scan을 직접 시작하지 않는다.
전체 시장 모집단 커버리지 미확인 상태에서는 curated watchlist/확인된 범위를 표시한다.

## 구현 정책 추가 명확화
schema_version/profile/history의 구조적 변경은 MIGRATION_REQUIRED다.
runup_db_path 변경은 실행 중 DB 즉시 전환하지 않는다. operations에서 backup/migrate/restart 후 적용한다.
시장 cap 관측 as_of와 source 성공시간은 분리한다. 관측이 freshness 기준 밖이면 새 allocation 보류다.
source_health_max_age_hours를 UniverseObservation freshness 기본에도 적용하고 source 정책상 더 짧으면 그 제한을 쓴다.
SETUP의 이벤트 revision 변경은 재검토를 요구한다. 기존 generation은 invalidation되어 사라진 trigger를 재사용하지 않는다.
평가 ref 가격은 계산 split basis, 실제 fill은 raw다. 환산 mapping 없이 두 수치를 stop/qty 계산에 섞지 않는다.
risk overlay는 recommendation이다. manual quote에 trade time을 임의 입력해 fresh exchange quote라고 승격하지 않는다.
재시작 후 timer/queue state만으로 cash/qty를 복원하지 않는다. 원장·command receipts가 기준이다.

NAV scope는 실제로 입력·확인된 런업 운용 자금과 position 원장이다. 전체 broker 계좌 잔고를 자동 확인했다고 표시하지 않는다.
공유 계좌에서 별도 단타 자금·position이 있다면 external_other_exposure와 공유 cash 제한을 확인해야 한다.
확인되지 않은 공유 자금은 신규 allocation에 쓰지 않는다. 한 자금을 단타와 런업에서 이중 사용하지 않는다.
일봉 MAfast/slow 기간은 config ma_periods이며 ma20_*라는 기존 key 이름이 기간을 20으로 hardcode하라는 뜻은 아니다.
required source별 official hard cap/allowlist는 config.py RUNUP_SOURCE_POLICIES에 중앙화하고 registry가 참조한다.
UI read refresh는 ui_refresh_seconds로 관리한다. 런업 read refresh가 단타 fragment 주기를 변경하지 않는다.
