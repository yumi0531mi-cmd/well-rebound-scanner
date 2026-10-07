# 런업스캐너 설계·구현 계약 V2.0
작성: 2026-10-06 KST. 설계: Codex. 구현: Muse. 이 문서와 CONFIG_CONTRACT_V2.json이 새 단계의 기준이다.
대상은 미국 상장 BIO·PHARMA·SPACE 주식이다. 기존 Streamlit 앱에 런업 탭을 추가한다.
확정 일봉으로 진입·추세를 평가하고 장중에는 위험을 감시한다. 체결·결제·출금은 수동 사실 입력이다.
고정 날짜 매수, +50% 수익 상한, 자동 주문·이체는 구현하지 않는다. 초기 숫자는 검증 전 가설이다.
기존 단타의 70~80% 목표·KIS 분봉 검증 요구는 유지하되 새 런업 전략의 검증된 성과로 옮겨 적지 않는다.

## 0. 실제 저장소와 재개
실제 루트: C:\Users\cj123\Documents\Codex\2026-08-27\e\work\strategy-filter-reentry
기존 Step00은 실제 앱 조사 후 ACCEPTED다. docs/runup/integration_map.md와 실제 코드를 대조하고 재사용한다.
Step01은 보완 구현 중이며 ACCEPTED가 아니다. 최신 코드를 먼저 읽고 V2 부족분만 수정한다.
기존 PROGRESS.json의 모든 키·기존 milestone·dirty 변경을 보존한다.
V2 진행은 runup_scanner.plan_v2={version,stages,next_stage,updated_at_utc}에 별도로 기록한다.
stage_00은 기존 Step00을 검증하여 재사용했다는 링크만 기록한다. 단계 번호의 과거 의미를 바꾸지 않는다.
각 단계의 상태는 NOT_STARTED/IMPLEMENTED/NEEDS_INPUT/BLOCKED/ACCEPTED다.
Muse는 IMPLEMENTED까지만 선언한다. ACCEPTED는 사용자 또는 Codex가 실제 구현·검사 증거를 검토한 뒤 기록한다.
문서 저장·테스트 개수·reviewed_by 문자열만으로 자동 승인하지 않는다. 선행 단계 ACCEPTED 전 다음 구현 금지.
기존 앱·정상 기능·비밀정보·금지 자료·단타 기준·배포 규칙을 보존한다. reset/revert/push/deploy 하지 않는다.
작업 전 현재 dirty 목록과 수정 허용 파일의 해시를 기록한다. 기존 변경이 있는 파일은 필요한 함수만 수정한다.

## 1. 완료와 검사 범위
각 단계는 입력·출력, 실패, 경계, 재시작, 중복 실행을 검증하고 멈춘다.
합성 데이터의 원장 산술·기능 검사는 구현 검사다. 실제 시장 수익률·백테스트로 부르지 않는다.
시장 전략 백테스트·최적화는 별도 사용자 요청 전 시작하지 않는다. 단위·통합·기존 기능 회귀 검사는 수행한다.
실제 API 확인은 소량의 읽기 요청만 허용한다. 실제 주문·송금·Telegram 발송·배포는 실행하지 않는다.
접속 성공, 파싱 성공, 수집 범위, 실시간성, 전략 수익성은 서로 다른 증거다.
실제 소스 접근이 안 되면 SUPPORTED_MANUAL/UNSUPPORTED와 원인을 표시한다. fixture로 접속 성공을 대체하지 않는다.
필요 비용·운용원금·출처 검토가 없으면 해당 동작만 NEEDS_INPUT으로 둔다. 빈 후보를 만들기 위해 조건을 낮추지 않는다.

## 2. 모듈과 기존 자원
config.py: 설정 기본값·schema·검증·hash·profile 해석의 단일 기준.
state_manager.py: 기존 load/save signature 유지, runup 진행·artifact 검사·손상 복구.
runup/domain/models.py: 불변 DTO·enum·DomainIssue. DB/HTTP/Streamlit import 금지.
runup/storage/{database,migrations,repositories}.py: SQLite·transaction·이력·projection.
runup/data/{http,provenance,universe,prices,calendar,quote_bridge}.py: 외부자료·출처·가격·시장시간.
runup/catalyst/{clinical_trials,sec_ir,fda,conferences,space,normalize,review}.py: 사실 후보와 검토.
runup/engine/{features,pivots,setup,trigger,strength,exhaustion,stop,exit,ranking}.py: 순수 계산.
runup/portfolio/{ledger,positions,rollover,withdrawal}.py: 체결·결제·예약·자금 순환·정산.
runup/services/{config_service,scan,jobs,alerts,export}.py: 조립·command·worker·증거 export.
runup/ui/{overview,catalysts,positions,cash,settings,health}.py: 공통 read model 표시와 입력 form.
의존은 UI→서비스→저장소/adapter/순수 엔진→domain이다. 엔진이 전역 config·DB·네트워크·현재 시각을 직접 읽지 않는다.
시험은 tests/runup_v2/unit,integration,ui에 둔다. synthetic fixture와 실제 소스 snapshot을 구분한다.
기존 앱은 top-level app.py, Streamlit 1.41.1이다. 새 버전 전용 API를 추정하거나 전체 업그레이드하지 않는다.
기존 shared_runtime_components().client와 KIS 공유 limiter, wellscan/sessions.py의 시장 calendar를 재사용한다.
QuoteBook/RealtimeHub의 단타 1초 정책을 바꾸지 않는다. 별도 KIS client·인증 worker를 만들지 않는다.
기존 HistoryCache는 분봉 중심이다. 일봉 계약 확인 전 일봉 지원을 완료했다고 선언하지 않는다.
app.py는 화면 연결 단계, start.py는 lifecycle 단계에서만 수정한다. 다른 기존 파일 변경이 필요하면 작은 diff와 이유를 보고한다.

## 3. 설정 계약과 profile
CONFIG_CONTRACT_V2.json은 117개 필드의 타입·기본값·단위·범위·null·가중치 key 집합을 정의한다.
RUNUP_CONFIG/RUNUP_SCHEMA/contract.rules의 key 집합은 정확히 같다. unknown key와 missing key는 오류다.
비용 4개는 None을 허용하지만 CONFIG_REQUIRED다. 그 key 자체가 없는 것은 오류다. None을 0으로 채우지 않는다.
bool을 숫자로 받지 않는다. 모든 숫자와 가중치 합계는 finite이며 NaN/Inf/overflow를 거부한다.
가중치는 정확한 component key 집합, 각 값>=0, finite sum>0이다. 누락 component 재정규화 금지.
tuple 길이·순서·경계와 enum을 계약대로 검사한다. 중앙 schema와 별개의 불완전한 검증 key 목록을 만들지 않는다.
validator는 입력을 변경하지 않으며 errors/config_required를 안정 순서로 반환한다. TypeError를 숨기고 정상 처리하지 않는다.
hash=SHA256(canonical JSON {schema_version,profile_name,config}); sort_keys, compact separators, allow_nan=false.
의미상 잘못된 설정은 hash 단계에서도 거부한다. 비용 None인 유효 설정은 hash 가능하다.
profile_name은 UNVALIDATED로 끝나야 한다. 설정 hash가 전략 검증이나 성과를 증명하지 않는다.
UI에서 config.py를 쓰지 않는다. config.py가 모든 해석·검증을 담당하고 사용자 값은 DB의 불변 완전 profile snapshot에 저장한다.
기본 profile은 config.py에서 생성한다. active profile ID는 1개이며 모든 계산은 동일 snapshot/hash를 사용한다.
별도 YAML/env 전략값·UI 수식 금지. schema가 다르면 MIGRATION_REQUIRED이며 silent merge하지 않는다.
기존 기본 수치를 유지한다. V2의 명시 변경: allocation 만료=다음 세션 종가, 부분매도 수량=floor-to-lot.
그 이유: 확정 일봉 신호를 다음 장에서 실행하려면 장 시작 즉시 만료할 수 없고, 부분수량 올림은 1주를 조기 전량매도한다.
재진입·provider·quote 시각·worker·HTTP·SQLite 등 추가 26개 항목도 JSON에 완전히 정의돼 있다.
가격·수량·비용을 입력할 때 USD, Decimal 문자열을 사용한다. 운용원금과 비용 입력 전 allocation을 보류한다.
모든 key 삭제/null/잘못된 타입, 수치 경계/NaN/Inf/bool, 모든 tuple·enum·weight 변형을 검사한다.
hash는 key 순서 불변·내용/profile/schema 변화 민감·invalid 거부·원본 불변을 검사한다.

## 4. 공통 자료형과 상태
DTO는 frozen dataclass 또는 동등한 불변 타입이다. 수정은 새 revision 또는 명시 command다.
money/qty/fill price는 Decimal(str), precision=34 기본값. float 금액·NaN/Inf·bool 수량을 거부한다.
가격 계산은 finite float 가능하다. timestamp는 UTC aware, 시장일은 session_date, 화면은 Asia/Seoul이다.
naive timestamp에 임의 timezone을 붙이지 않는다. 일봉 날짜를 실제 체결 시각처럼 쓰지 않는다.
CollectionResult[T]={status,items,errors,next_cursor,evidence_ids,coverage,observed_at}.
status=OK/EMPTY_CONFIRMED/PARTIAL/FAILED/UNSUPPORTED/RATE_LIMITED. parser 오류·인증 실패는 EMPTY_CONFIRMED가 아니다.
DomainIssue={code,path,message,severity,evidence_ids}; UI는 enum code로 판단한다.
FeatureValue={value nullable,status VALID/UNDEFINED/INSUFFICIENT/STALE,reason}.
정상 false와 자료 없음은 다르다. 여러 원인을 모두 유지한다.
command_id는 호출자가 만든 한 제출의 안정 ID다. 같은 ID+같은 payload는 기존 결과, 다른 payload는 IDEMPOTENCY_CONFLICT다.

## 5. DTO 최소 필드
SourceDocument: document_id,source_id,url,fetched_at,published_at?,first_seen_at,available_at,payload_hash,media_type,blob_path,parser_version,time_quality,http_status.
Issuer: issuer_id,CIK? 10자리 문자열,legal_name,sector_tags,valid_from,valid_to?,mapping_evidence.
Security: security_id,issuer_id,ticker,exchange,currency,equity_type,listing_status,valid_from,valid_to?.
UniverseObservation: security_id,market_cap,as_of,available_at,source_id,classification_evidence,status.
EventCandidate: candidate_id,document_id,evidence_span,issuer_candidates,program_id?,event_type,raw_date_text,date_precision,start/end?,timezone?,review_status,available_at.
CatalystRevision: event_id,revision_id,revision_seq,issuer/security_ids,program_id,event_type,phase?,date_precision,start/end,timezone,source_document_ids,available_at,status,risk_class,mapping_status,importance_class,reviewed_by/at?,supersedes?.
RiskNotice: notice_id,security_id,reason,severity CRITICAL/REVIEW,source_document_ids,available_at,review_status.
PriceBar: security_id,session_date,O/H/L/C/V,currency,source_id,basis,available_at,fetched_at,is_final,revision.
QuoteObservation: security_id,last,bid/ask?,received_at,trade_at?,time_quality,source_id,session,delay_known,age,status.
CorporateAction: action_id,security_id,type SPLIT/DIVIDEND,effective_session,ratio/gross/net?,evidence_ids,available_at.
FeatureSnapshot: feature_id,security_id,as_of,feature_version,bar_hash,config_hash,components,issues.
SetupSnapshot: setup_id,generation_id,security_id,event_ids,as_of,score,qualified,expires_session,invalidated_at?,reasons.
TriggerSnapshot: trigger_id,generation_id,security_id,ref_pivot_id,as_of,valid_until,eligible,reasons.
DecisionSnapshot: decision_id,run_id,security_id,event_ids,as_of,config_hash,feature_hash,setup_state,trigger_id?,strength/exhaustion?,entry_eligible,exit_action,target_sell_fraction,reasons,health.
PositionProjection: position_id,security_id,issuer_id,sector,qty_remaining,Q0,qty_sold,buy_reserved/sell_reserved,avg_entry_price_ex_fee,cost_basis_remaining,realized_pnl,initial_stop/trailing_stop?,status,revision.
Fill: fill_id,command_id,position_id,security_id,side,qty,price,fee,currency,executed_at,recorded_at,evidence,allocation_id?.
LedgerEvent: event_id,command_id,type,position_id?,occurred_at,recorded_at,settled_delta,unsettled_delta,qty_delta,cost_basis_delta,realized_delta,external_flow_delta,reversal_of?.
Allocation: allocation_id,command_id,security_id,decision_id,config_hash,reference_quote_id,budget,qty,entry_reference,initial_stop_reference,reservation_usd,expires_at,status.
WithdrawalPeriod: period_id,revision,monthly_net,P,H_before,H_after,processed_base,tax_earmark_delta,profit_earmark_delta,status,command_id.
Outbox: alert_id,idempotency_key,event/decision_ids,payload_hash,status,attempts,next_attempt_at,transport_receipt?.
?는 명시적 nullable이다. field별 단위와 validation을 API_CONTRACT.md에 작성하고 arbitrary dict로 계약을 우회하지 않는다.
ID는 UUID 또는 출처 기반 결정적 key이며 원본과 연결된다. Feature/Trigger ID에는 시간·버전·입력 hash가 포함된다.

## 6. SQLite와 복구
DB=.scanner_data/runup/runup.sqlite3. 기존 DB와 분리하고 FK/WAL/busy timeout/bounded retry를 적용한다.
tables: schema_versions,issuers,securities,universe_observations,mapping_reviews,source_documents,document_observations,collection_runs,
event_candidates,catalyst_revisions,event_reviews,risk_notices,price_bar_revisions,corporate_actions,config_profiles,
scan_runs,feature_snapshots,setup_snapshots,trigger_snapshots,decision_snapshots,fills,positions,ledger_events,
settlement_commands,allocations,withdrawal_periods,alert_outbox,worker_leases,command_receipts.
Decimal은 canonical text, timestamp는 UTC ISO다. money SUM을 SQLite REAL로 계산하지 않는다.
UNIQUE: event_id/revision_seq; source/payload hash; source/security/session/bar revision; command_id; alert key; period/revision.
같은 원문은 재사용하고 수집 관측은 따로 기록한다. revision은 append-only, 취소는 tombstone revision이다.
at(as_of)는 available_at<=as_of 중 최신 revision이다. 미래 취소를 과거 판단에 적용하지 않는다.
Fill·원장·projection·reservation 변경과 command receipt는 한 transaction으로 commit한다.
source cursor는 저장 성공 뒤에만 전진한다. 외부 HTTP·sleep은 DB transaction 밖이다.
실패는 rollback. 원장 재구축 결과가 현재 projection과 같아야 한다. backup은 SQLite consistent snapshot이다.
PROGRESS는 기존 atomic 저장을 보존한다. 손상 원본의 exclusive backup 실패도 보고하고 정상처럼 덮어쓰지 않는다.
manifest는 repo 내부 허용 경로·type·hash를 검사한다. ../,외부 symlink,.env,auth cache,secret 파일은 거부한다.
PROGRESS 자체 hash를 자기 안에 쓰지 않는다. schema migration은 versioned·transactional이며 임의 drop/reset 금지다.

## 7. 자료 수집과 출처
무료 소스만 필수 경로로 사용한다. HTTP GET, source allowlist, redirect/주소/크기/timeout/총 retry budget 검사.
외부 URL은 사용자 입력이라도 private/loopback/metadata 주소를 차단한다. 각 redirect와 최종 접속 IP를 검사한다.
retry는 network/timeout/429/일시 5xx만. 긴 Retry-After는 기다리지 않고 RATE_LIMITED+next_attempt를 반환한다.
SEC는 식별 User-Agent와 공통 제한을 적용한다. credential 값은 출력·문서·로그에 남기지 않는다.
registry={source_id,official_url,adapter_version,capability,rate_policy,scope,last_success,last_failure,coverage}.
rate_policy는 config.py의 RUNUP_SOURCE_POLICIES에서 관리한다. 각 제한은 공식 상한과 검증된 RUNUP_CONFIG 공통값 중 더 엄격한 값이다.
소스별 운영 상한·allowlist·환경변수 이름은 중앙 source policy에 두고 adapter에 숫자/credential을 복제하지 않는다.
capability=AUTOMATED/SUPPORTED_MANUAL/UNSUPPORTED. manual 입력도 URL·원문·검토 기록이 필수다.
ClinicalTrials.gov는 등록 임상 metadata다. PCD는 TRIAL_COMPLETION_MARKER이며 발표일로 바꾸지 않는다.
SEC submissions는 filing 발견 경로다. IR/filing 원문 근거로 readout/PDUFA 후보를 만든다.
FDA calendar는 AdCom 일정이다. 완전한 미래 PDUFA API를 가정하지 않는다.
학회 title/abstract/LBA/poster/oral/data release를 분리한다. 기업/프로그램 발표근거 없는 전체 학회는 generic calendar다.
SPACE는 NASA/회사 mission과 FAA 허가를 분리한다. license 유효일을 발사일로 사용하지 않는다.
출처가 닫히거나 형식이 바뀌면 typed failure와 manual review 경로를 제공한다.
기관명·약물·sponsor·ticker의 부분 문자열 일치만으로 mapping 확정 금지. issuer/CIK/security 관계와 근거를 검토한다.
수집 건강도는 필요한 종목·출처 범위별로 평가한다. 관련 없는 소스 실패가 전 종목을 정상/실패로 왜곡하지 않는다.
소스 endpoint/필드/접근 가능성은 공식 자료와 실제 읽기 응답으로 확인한다. 지원하지 않는 기능은 명시한다.

## 8. 날짜 정밀도와 이벤트 안전판
precision=EXACT_DATETIME/EXACT_DATE/WINDOW/MONTH/QUARTER/UNKNOWN/NO_EARLIER_THAN.
날짜만 있는 일정은 해당 현지일 00:00을 earliest bound로 삼는다. 월/분기는 구간 시작 00:00, end는 exclusive다.
timezone 불명은 REVIEW_REQUIRED다. ET/CT 표기는 원문을 보존하고 IANA/DST로 변환한다.
event identity=issuer/program/type/occurrence. 같은 날 서로 다른 프로그램·초록·본 발표를 하나로 합치지 않는다.
출처 충돌은 CONFLICT→검토→새 revision이다. 근거 없는 confidence 수치를 생성하지 않는다.
중요도 기본: routine25, 확정 readout/regulatory/conference data75, first/key pivotal/major project100.
importance_first_key_pivotal_major>=importance_confirmed>=importance_routine 순서를 유지한다.
중요도에는 factual class·근거·검토를 보존한다. 사후 주가 상승으로 바꾸지 않는다.
연결된 모든 위험 이벤트 중 가장 이른 boundary를 사용한다. 초록 공개가 본 발표보다 빠르면 초록이 우선이다.
cutoff=boundary보다 strictly 이전인 마지막 market close. deadline=cutoff 세션에서 buffer 세션만큼 이전 close.
buffer1, 월요일 위험이면 cutoff 금요일, deadline 목요일 close다. deadline 도달 시 FULL 청산 권고다.
bounded WINDOW/MONTH/QUARTER는 최초 가능일 기준으로 평가한다. UNKNOWN/unbounded NET는 WATCH_ONLY다.
PCD만 있는 종목은 발표일이 확인될 때까지 ENTRY 불가다.
확인된 임상 중단/hold/촉매 취소/중대희석은 CRITICAL→FULL 권고. 단순 ATM 등록 존재만으로 중대희석 확정 금지.
연기·출처 충돌·severity 불명은 REVIEW+새 진입 보류다. 이미 알려진 deadline/stop은 계속 평가한다.
현재 처음 본 사실을 published_at으로 소급하여 과거에 알려졌던 자료로 만들지 않는다.
조기 발표·gap·휴장·정지 위험은 남는다. 발표 전 청산이 손실을 완벽히 차단한다고 표시하지 않는다.

## 9. 일봉·가격 basis·quote
검증된 KIS 일봉 adapter 우선. yfinance는 명시 선택한 개인용 history fallback이며 전체 batch source를 표시한다.
provider를 필드별로 섞거나 조용히 fallback하지 않는다. source/basis별 snapshot과 lineage를 기록한다.
OHLC>0, L<=min(O,C)<=max(O,C)<=H, V>=0, finite, USD, session 정렬·중복 제거를 검증한다.
calendar의 expected sessions로 completeness를 검사한다. 누락봉 ffill/가짜 0 volume 금지.
시장 close+finality grace 이후 확정된 봉만 전략에 사용한다. 진행봉은 참고 표시만 한다.
최신 사용 가능한 final session이 없으면 STALE다. 휴장일은 누락 session으로 세지 않는다.
basis=RAW/SPLIT_ADJUSTED/TOTAL_RETURN/UNKNOWN을 구분한다. basis 불명은 계산/ENTRY 보류다.
계산은 일관된 split-only OHLCV로 한다. 이미 조정된 provider를 다시 조정하지 않는다.
실제 fill은 당시 raw 가격·수량이고 확인된 split 효력 발생 시 position/ref 단위를 변환한다.
과거 as_of에 알려지지 않은 action을 적용한 현재 조정봉을 historical 검증 자료라고 부르지 않는다.
기존 KIS current_price timestamp는 수신 시각이다. trade_at 없는 quote를 거래소 실시간 체결로 부르지 않는다.
received age와 trade age를 별도로 검사한다. 미래 timestamp가 tolerance를 넘으면 INVALID다.
가격 지연/체결 시각이 불명이면 POSSIBLE_STOP+REVIEW, 신규 allocation 보류다.
확인된 fresh quote가 stop 아래면 FULL 권고. 시세만으로 실제 체결을 만들지 않는다.
known event deadline/CRITICAL은 가격 결측과 무관하게 권고한다. 감시 worker가 없으면 보호 중이라고 표시하지 않는다.

## 10. Feature 수식
입력=(bars,benchmark,as_of,config snapshot). 외부 현재 시각이나 전역값 대신 명시 입력을 사용한다.
SMA_n=최근 n개 C 평균. Slope=(SMA_fast_t-SMA_fast_(t-k))/(k*SMA_fast_(t-k)).
Dryup=mean(V 최근 short)/mean(V 최근 long). RVOL=V_t/mean(V 직전 n개,t 제외).
ADV=mean(C*V 최근 n개). MASpread=abs(SMAfast-SMAslow)/SMAfast.
TR 첫 봉=H-L; 이후 max(H-L,abs(H-Cprev),abs(L-Cprev)).
ATR seed=첫 n TR 평균; 이후 ((n-1)*ATRprev+TR)/n인 Wilder 방식이다.
RawK=100*(C-lowestL(n))/(highestH(n)-lowestL(n)); SlowK=SMA(kSmooth,RawK); SlowD=SMA(dSmooth,SlowK).
CLV=(C-L)/(H-L); upperwick=(H-max(O,C))/(H-L); momentum_n=C_t/C_(t-n)-1.
RS_n=같은 session security 수익률-benchmark 수익률. alignment 부족은 unavailable이다.
Efficiency=(C_t-Cprev)/ATRprev. 분모 0/부족봉/NaN/basis 불명은 typed unavailable이다.
clip(x,0,1)은 수학 정의다. 기간·임계값·정규화 상수는 모두 config 필드에서 가져온다.
available_at>as_of 또는 미완성 봉은 제외한다. hash에 bar revision/basis/config/feature version을 포함한다.
golden: RVOL current900, prior[100,200,300]→4.5. Dryup [100,100,50,50],short2,long4→2/3.
ATR n3,TR[2,2,3]→7/3; 다음 TR3→23/9. RawK C10,low6,high14→50.
RawK[20,40,60]의 SlowK3→40; SlowK[30,40,50]의 SlowD3→40.

## 11. 인과적 pivot과 돌파
pivot p는 좌 l/우 r 범위의 strict extreme이다. 동률 plateau는 pivot이 아니다. known_at=p+r 세션이다.
as_of까지 확인된 pivot만 반환한다. HL=최신 두 confirmed low 중 마지막>직전, HH도 동일 방식이다.
warmup 뒤 pivot 없음은 정상 false, warmup 부족은 INSUFFICIENT다.
t 돌파 ref는 t-1까지 알려진 최신 high다. Cprev<=ref && C_t>ref를 확인한다.
오늘 확인된 새 ref는 내일부터 사용한다. ref 변경 자체를 돌파로 만들지 않는다.
failed_breakout은 선택 trigger의 frozen ref 아래로 확정 종가가 돌아온 상태다. ref를 나중 고점으로 바꾸지 않는다.
미래 봉 append가 과거 known_at/HL/ref/판정을 바꾸지 않는 prefix invariance를 검사한다.
golden Low[5,2,4,3,5],l1/r1: low2는 index2에 확인,low3은 index4에 확인,index4부터 HL true다.
rolling low를 confirmed pivot으로 부르거나 전체 기간 pivot을 과거에 넣지 않는다.

## 12. SETUP·TRIGGER 상태
hardgate: 미국 대상종목, 승인 mapping/이벤트, 관련 health fresh, 중요도>=min, bounded 실제 촉매, watch horizon.
시총·거래량·ADV·가격 필터는 timestamp 있는 관측값이다. 과거 시총을 현재 값으로 대체하지 않는다.
SETUP flags: dryup<=max; squeeze<=max; 기간 내 MAfast 회복 후 현재 위; slope>=min; HL; SlowKprev<=oversold && SlowK_t>prev.
MA 회복은 Cprev<=MAprev && C>MA인 cross가 lookback 내 알려졌는지 평가한다.
score=100*sum(w*flag)/sum(w). 필요한 feature 누락 시 score NULL, 재정규화 금지.
기본 threshold70, latch10 세션. 형성 session s부터 s+10번째 session close 전까지 유효하다.
qualified 전환 시 generation을 만든다. 동일 상태의 매일 재평가로 latch 만료를 연장하지 않는다.
구조 붕괴·취소·expiry·profile 변경은 generation invalidation이다. event 날짜 수정은 재검토 후 새 generation이다.
TRIGGER: 현재 MA 위,confirmed HL,직전 ref cross,RVOL>=min,CLV>=min,(C-ref)/ATRprev<=max.
SETUP 때 거래량 소진과 돌파 당일 거래량 증가를 같은 날 요구하지 않는다.
신호 session close부터 최초 위험 boundary 전 close인 미래 세션 개수가 entry_min_sessions_to_risk 이상이어야 한다.
다음 실제 거래 시각은 forced exit deadline보다 strictly 이르다. 과확장 신호는 LATE_ENTRY_BLOCKED다.
신호 유효기간은 다음 regular session close까지이며 deadline이 더 빠르면 그때 끝난다.
다음 장의 fresh quote·현재 이벤트·원장·노출을 재확인해야 ALLOCATABLE이다.
trigger_id=generation/ref/signal_session/config hash. 같은 입력은 같은 ID, 소비한 generation 재사용 금지.
재진입은 기존 SETUP invalid/expired 뒤 새 generation+새 cross가 필요하다.
WATCH/SETUP/TRIGGER/ENTRY_ELIGIBLE/ALLOCATABLE/실제 OPEN을 구분한다.
golden SETUP [dryup1,HL1,recovery1,slope0,squeeze1,stoch0]→85점.

## 13. Strength·Exhaustion
Strength components:
trend=clip(slope/strength_trend_norm,0,1);
structure=(HH+HL)/2;
relative_strength=clip(RS/strength_rs_norm,0,1);
volume_efficiency=C>Cprev이면 clip(RVOL/strength_rvol_norm,0,1), 아니면 0.
설정 가중치 평균*100. 이름 volume_efficiency는 V2에서 위 수식으로 고정하며 검증된 매집량을 뜻하지 않는다.
Exhaustion 8 flags:
rapid_gain_then_deceleration: momentum>=min && Efficiency_t<Efficiency_prev;
climax_volume: RVOL>=climax;
large_upper_wick: wick>=min && CLV<=max;
failed_breakout: frozen ref 아래 확정 close;
poor_efficiency_with_high_rvol: RVOL>=poor && abs(Efficiency)<=max;
weakening_rs: RS<=0 && RS<RSprev;
short_ma_loss: C<shortMA;
structure_break: C<최신 confirmed HL low*(1-hl_buffer).
가중 평균*100이다. Strength의 100-score로 대체하지 않는다. 필요한 component 누락 시 score NULL이다.
threshold35/55/75는 누적 매도25/50/100%와 연결한다. 임계값과 같으면 해당 단계다.
수익률 숫자만으로 매도하지 않는다. 점수는 승률/수익률 확률이 아니다.

## 14. Stop·Exit·부분매도
avg_entry_price_ex_fee는 실제 가격 가중 평균, cost_basis는 수수료 포함 원가다.
initial structural=진입 때 frozen HL low*(1-hl_buffer); hard=entry_reference*(1-hard_stop_fraction).
valid structural<entry이고 hard<entry일 때 initial_stop=max(structural,hard). 구조 ref 없음/stop>=entry면 allocation 보류다.
보유 trailing=max(previous trailing,valid latest structural,hard). 자동으로 아래로 넓히지 않는다. split 때만 단위 변환한다.
Exit는 known event deadline/CRITICAL FULL,확인된 hard stop FULL,구조 붕괴 FULL,Exhaustion target,HOLD 중 최대 강도다.
score 결측도 알려진 위험을 지우지 않는다. data review는 별도 flag로 병기한다.
target=max(previous target,new target). Q0=누적 실제 buy 수량을 현재 split basis로 환산한 값이다.
partial desired=floor_to_lot(Q0*target). target=1이면 실제 잔량 전부다.
추가 권고=max(0,min(qty_remaining,desired-qty_sold-qty_sell_reserved)).
Q0=1,lot1,target25%→0/SMALL_LOT_DEFER; FULL→1. ceil로 전량매도하지 않는다.
Q0=100,sold25,reserved10,target50%→추가15. 재실행으로 예약·체결이 중복되지 않는다.
권고만으로 cash/qty를 바꾸지 않는다. 사용자 sell reservation 명령과 실제 fill을 구분한다.
실제 추가 buy가 있었다면 사실을 기록해 Q0를 갱신하되 V2 신규 allocation은 기존 position/동일 issuer 중복을 금지한다.

## 15. 원장·결제·기업행동
buy: settled cash-=qty*price+fee; qty/cost_basis+=각 실제 수량/원가.
sell: remaining qty 감소; released cost=매도 전 평균 원가*qty;
unsettled cash+=gross-fee; realized PnL+=net-released cost.
마지막 전량 sell은 남은 원가 전부를 해제하여 Decimal 나눗셈 잔액을 제거한다.
settlement confirmation: unsettled 감소,settled 증가. 날짜/금액/근거 없는 T+1 자동 확정 금지.
모든 수량/가격/수수료는 명시 입력이며 명시 fee0만 허용한다. fee None은 보류다.
매도 초과·중복 payload 충돌·음수 fee·비USD는 오류다. 체결 취소/정정은 reversal+replacement로 기록한다.
예산 없는 실제 buy 등 예기치 않은 사실은 증거와 함께 reconciliation 기록으로 보존한다.
불일치 상태에서 신규 allocation을 차단한다. 실제 사실을 조용히 버리거나 추측 잔고를 만들지 않는다.
정상 승인 경로는 잔고/수량/예약 불변식을 만족해야 한다. 보정 승인 전후 차이를 사용자가 확인한다.
split은 verified ratio/효력/known 시각을 적용한다. qty/Q0/sold/reservations/단가/ref 변환,총 원가/cash/PnL 불변이다.
dividend는 실제 확인 net income만 기록한다. 예측 배당으로 수익 생성 금지.
자본 입금/원금 출금/이익 출금은 별도 external flow다. 투자 실현손익과 혼합하지 않는다.
fixture: buy10*$10 fee1→원가101; sell4*$12 fee1→net47,released40.4,realized6.6,remaining cost60.6.
매도 결제 전 settled cash는 증가하지 않는다. 원장 replay가 projection과 정확히 같아야 한다.
ledger.rebuild(as_of)는 recorded_at<=as_of인 사실만 사용한다. 월별 손익은 실제 executed_at의 NY월에 귀속한다.
늦은 보고/정정으로 지난 월 손익이 달라지면 reconciliation을 기록하고 확정 정산을 조용히 재작성하지 않는다.

## 16. Rollover·배분
rank=가중 평균(Strength,SETUP,importance); 기본 .5/.3/.2. eligible active signal만 포함한다.
tie=rank desc,ADV desc,earliest risk asc,security_id asc. 여러 이벤트를 가진 한 종목은 한 후보다.
slots=실제 open+활성 pending buy의 unique securities. 부분매도는 slot 해제가 아니다.
deployable=settled-reservations-profit earmark-tax earmark-NAV*cash buffer; 음수면 0.
unsettled/미실현 평가익/청산 권고는 매수 가능 cash가 아니다. NAV 또는 신선한 평가값 불명은 allocation 보류다.
budget=min(deployable,position NAV limit 여유,issuer limit 여유,sector limit 여유).
sector는 BIO+PHARMA를 합산한 BIOPHARMA와 SPACE로 계산한다. issuer가 겹치는 노출을 중복 계산하지 않는다.
entry_cost=quote*(1+slippage); fee=max(fee_minimum,rate*qty*entry_cost).
qty는 lot 배수 중 총cost<=budget && qty*(entry_cost-stop)<=NAV*risk_per_position을 만족하는 최대값이다.
계산 뒤 두 조건을 직접 재검사한다. fee minimum·수량0·동시 승인·경계 동일을 검사한다.
비용4개 None,quote 시각 불명,stop>=entry,expired signal,deadline 도달이면 승인 불가다.
proposal은 예약이 아니다. approve가 latest balance/노출/quote/이벤트/profile을 transaction으로 재확인 후 reserve한다.
expiry=min(next session close,forced deadline); 만료된 reserve는 해제한다. 늦게 보고된 실제 fill은 reconciliation으로 보존한다.
partial fill만큼 예약을 차감하고 cancel/expiry는 미체결만 해제한다. 동일 자금을 동시에 두 후보에 쓰지 않는다.
후보 없음은 CASH_WAIT다. 5개를 채우려고 기준을 완화하지 않는다.

## 17. 월말 실현이익 인출
P=누적 net realized(매도/확인 배당/명시 운용비); H=이미 처리한 실현손익 기준액.
M=현재 NY월 net realized; new=max(0,P-H). positive_monthly 조건이 활성이고 M<=0이면 신규 earmark0.
비용/tax None 또는 stale equity면 정산 보류다. tax_reserve는 사용자 잠정 충당률이며 법정세율이 아니다.
f=withdraw_fraction+tax_reserve. headroom=max(0,equity-net capital floor-existing earmarks).
base=min(new,available settled cash/f,headroom/f). available cash는 예약/기존 earmark/현금 buffer 차감 후 금액이다.
f=0이면 NO_EARMARK_POLICY,신규 earmark0,H를 소비하지 않는다.
profit earmark=base*withdraw_fraction; tax earmark=base*tax_reserve.
승인 transaction에서 H+=base,두 earmark 증가. 한 period/revision의 중복 승인 금지.
cash cap으로 base<new면 미처리 이익은 남는다. 다음 정산에 손실 carryforward를 반영한다.
실제 profit/tax 출금 확인은 settled cash 감소+해당 earmark 해제+external flow다. P/H는 바꾸지 않는다.
원금 출금은 floor 감소,이익 출금은 floor 감소가 아니다. 외부 출금을 실현이익으로 만들지 않는다.
과거 fill 정정은 정산 삭제 대신 reconcile adjustment다. H를 내려 같은 이익을 중복 인출하지 않는다.
fixture(tax0 명시): P100,H0,M100,cash100,headroom100,withdraw.5→base100,H100,profit50.
이후 P60→new0;P110,M50→new10. 미실현 이익만으로 earmark를 만들지 않는다.
이 기준은 실현손익 carryforward이며 계좌 최고 평가액 기준과 다르다.

## 18. Worker·scan·알림
기존 start.py shared runtime 후 같은 process lifecycle에 runup worker를 연결한다.
runup_worker_enabled=false 기본이면 DISABLED다. 화면 열기/탭 변경이 worker를 자동 enable하지 않는다.
DB lease/heartbeat/fencing token으로 중복·재시작을 관리한다. lease 잃은 worker의 commit은 거부한다.
jobs=source refresh,daily feature,quote risk,event risk,scan,settlement proposal; 주기/budget를 분리한다.
critical notice와 event revision은 일봉 close를 기다리지 않고 위험 평가를 요청한다.
각 scan은 고정 as_of,profile hash,input revision snapshot을 가진다. partial failure는 last-good와 원인을 병기한다.
일봉 결과와 장중 risk overlay는 분리한다. intraday로 daily trigger/feature를 재계산하지 않는다.
cursor/restart/lease recovery를 검사한다. 오래된 health를 정상으로 표시하지 않는다.
alert key는 의미 있는 상태 전환/누적 target 증가에 연결한다. 같은 rerun은 중복 alert를 만들지 않는다.
outbox는 retry/receipt/dead letter를 보존한다. dry-run 기본이며 credential 값은 포함하지 않는다.
알림 실패가 원장/cash/exit를 바꾸지 않는다. 실제 발송은 별도 사용자 지시가 있어야 한다.

## 19. 기존 Streamlit 탭·입력 권한
app.py의 pageconfig/admin branch/helpers와 기존 단타 UI를 보존하고 st.tabs로 단타/런업 container를 만든다.
1.41의 모든 tab 본문은 실행된다. selected tab API를 가정하지 않는다. 런업 렌더는 DB read만 한다.
탭 전환으로 HTTP/scan/job를 실행하지 않는다. 기존 1초 fragment/구독/session key를 보존한다.
전역 st.stop이 런업 화면까지 막는 경로를 검사한다. 필요한 legacy early-return은 작은 함수 범위로 분리한다.
새 UI key는 runup_*다. 기존 sidebar는 단타 전용으로 명시한다.
런업 화면=개요/캘린더/후보/보유/현금·정산/설정/수집 상태; 전체/BIO/PHARMA/SPACE 필터.
PC table과 375px mobile card가 같은 read model을 쓴다. score를 승률로 표시하지 않는다.
as_of/source/basis/received_at/trade_at/지연 불명/coverage/profile hash/block reason을 보여준다.
실제 보유·예약·권고를 명확히 구분한다. broker 자동 연결/보호 중이라고 표시하지 않는다.
기존 backtest admin 인증은 전체 앱 인증이 아니다. 런업 쓰기 command에 별도 server-side writer gate를 적용한다.
기존 secret manager의 token 검증 방식을 재사용하되 runup session 권한을 분리한다. token 값/.env를 문서나 로그에 쓰지 않는다.
권한 없음은 review/fill/settlement/allocation/profile 쓰기 모두 거부한다. read-only 공개 화면과 쓰기 권한을 분리한다.
form submit ID는 한 제출 동안 고정이다. double click/rerun/retry가 중복 command를 만들지 않는다.
설정 widget은 schema metadata를 사용하고 config service가 검증한다. Python 소스를 UI에서 수정하지 않는다.
cache key=profile hash+read model revision. 변경 후 화면과 엔진이 서로 다른 profile을 쓰지 않는다.

## 20. 증거·최종 검증
각 단계 evidence={design version,선행 확인,수정 전후 파일/hash,입출력 계약,검사 명령/결과,실제/합성 구분,잔여 문제}.
테스트 expected는 명세 수식·손계산·공식 소스 계약에서 구한다. 구현 출력을 expected로 복사하지 않는다.
실패를 xfail/삭제/조건 완화로 숨기지 않는다. 문서 충돌은 정확한 항목과 작은 대안을 보고하고 그 동작은 보류한다.
각 단계의 positive/negative/boundary/missing/wrong type/duplicate/stale/future/rollback/restart 중 해당 항목을 검사한다.
Source fixture와 실제 smoke를 구분하고 실제 미확인 항목은 LIVE_UNVERIFIED로 남긴다.
최종 bundle은 허용 목록만 export: sanitized 코드 manifest,dependency/version,config schema/hash,검사 명령/결과,
source capability/coverage/time/basis,인과적 feature/decision,합성 ledger replay,UI PC/mobile/권한/회귀 증거,미확인 목록.
secret/DB 전체/account 정보/raw auth cache는 export하지 않는다. 사용자 체결자료는 동의 없으면 합성 대체한다.
최종 Codex 검토 전 모든 기능 단계 ACCEPTED를 확인한다. 운영 미입력은 기능 완성과 구분해 명시한다.
정식 수익성 검증은 별도 요청 뒤 point-in-time 모집단/수수료/slip/가격순서/학습·검증 분리로 설계한다.
이 문서는 오류를 줄이는 계약이다. 수정 0회·실수 0회·꾸준한 수익을 보장하지 않는다.

## 21. 고정 함수 계약
config.validate_runup_config(cfg=None)->{errors:list[str],config_required:list[str]}.
config.runup_config_hash(cfg=None,profile_name=None)->str; invalid는 ValueError.
domain.validate(dto)->list[DomainIssue]; storage.transaction() context; repository.at(as_of,filters)->DTO list.
collector.collect(cursor,observed_at,source_policy)->CollectionResult.
normalize(candidate,mapping,review,as_of)->CatalystRevision 또는 DomainIssue.
prices.fetch(security,start_session,end_session,as_of)->CollectionResult[PriceBar].
features.compute(bars,benchmark,as_of,config)->FeatureSnapshot; pivots.confirm(bars,as_of,config)->PivotSet.
setup.evaluate(context)->SetupResult; trigger.evaluate(context)->TriggerResult.
strength.evaluate(context)->ScoreResult; exhaustion.evaluate(context)->ScoreResult.
stop.evaluate(position,quote,features,config)->StopResult; exit.evaluate(position,context)->ExitDecision.
ledger.record_fill(fill,command_id)->LedgerCommandResult; ledger.rebuild(as_of)->LedgerProjection.
positions.confirm_settlement(command)->LedgerCommandResult.
rollover.propose(context)->list[AllocationProposal]; rollover.approve(proposal_id,command_id,latest_context)->Allocation.
withdrawal.propose(period,context)->SettlementProposal; withdrawal.confirm(proposal_id,command_id,latest_context)->WithdrawalPeriod.
scan.run(as_of,config_profile_id)->run_id; jobs.start(runtime)/stop()/status().
alerts.enqueue(decision,event)->OutboxResult; alerts.dry_run(queued)->TransportResult.
ui.render(read_models,command_services,auth_context)->None; export.bundle(destination)->ReviewManifest.
context를 구성하는 field/type/unit/nullable를 Step02의 API_CONTRACT.md에서 고정하고 이후 단계가 해당 계약을 따른다.
