# 아키텍처 명세 v1.0
## 고정 원칙
단일 도메인 모델·SQLite·공통 특징/전략/Exit 엔진. 수집기는 사실만 수집하고 UI는 저장된 판정만 표현한다.
미국 주식과 USD가 원장 기준이다. KRW는 출처·시각이 있는 환율로 표시만 하며 환율 부재시 USD만 표시한다.
시장 시각은 America/New_York, 저장 시각은 UTC aware, 사용자 표시 시각은 Asia/Seoul을 함께 제공한다.
일봉 날짜는 UTC 자정으로 간주하지 않고 해당 거래소 session_date다. 주말·휴장·조기폐장·DST는 거래소 달력을 사용한다.
기존 Streamlit 앱에 런업 탭을 추가한다. 기존 시세 adapter·인증·운영 환경을 먼저 조사해 재사용하며 외부 공개 배포는 별도 결정이다.

## 폴더와 모듈 책임
- config.py: typed config, profile validation, canonical config hash.
- state_manager.py: PROGRESS 원자적 저장·복구·milestone evidence; 전략/포트폴리오 상태를 저장하는 곳은 아니다.
- 기존 app.py 또는 실제 진입점: 런업 탭 mount만; 기존 단타 기능 보존, 계산식·수집 스케줄·현금 변경 금지.
- runup/domain/models.py: DTO·enum·Decimal 단위·검증. 어떤 네트워크/UI import도 금지.
- runup/storage/{database,migrations,repositories}.py: SQLite 트랜잭션·versioned schema.
- runup/data/{http,provenance,universe,prices,calendar,quote_bridge}.py: 기업·시세·session 처리와 기존 adapter 연결.
- runup/catalyst/{clinical_trials,sec_ir,fda,conferences,space,normalize,review}.py: source-specific adapter, 정규화·검토.
- runup/engine/{features,setup,trigger,strength,exhaustion,stop,exit,ranking}.py: 순수함수. DB/UI/HTTP 호출 금지.
- runup/portfolio/{ledger,positions,rollover,withdrawal}.py: 수동 체결·수량·현금·배분·출금 적립 원장.
- runup/services/{scan,jobs,alerts,export}.py: orchestration·worker lease·outbox·review export.
- runup/ui/{overview,catalysts,positions,cash,settings,health}.py: read model + 명시적 사용자 command.
- tests/{unit,integration,ui}/, tests/fixtures/synthetic/, tests/fixtures/source_snapshots/.
- docs/runup/, work/evidence/, data/ 는 목적을 구분한다. data와 비밀정보는 Git 제외.

## 데이터 흐름
수집기 → RawDocument → EventCandidate → review/normalize → CatalystRevision
가격 → PriceBar/CorporateAction → FeatureSnapshot
승인된 이벤트 + Universe + FeatureSnapshot + config → DecisionSnapshot
보유·위험 이벤트 → ExitDecision → 알림/사용자 확인 → 실제 체결 입력 → Ledger → Rollover 추천
원장 순실현손익 → 출금 적립안 → 사용자 확인 기록. 실제 돈 이동은 수행하지 않는다.
worker는 서비스를 호출한다. 브라우저를 닫아도 worker가 켜져 있으면 작업할 수 있다. worker가 꺼져 있으면 UI가 이를 표시한다.

## 필수 DTO와 시간 계약
SourceDocument: source_id/url/fetched_at/published_at(nullable)/payload_hash/media_type/blob_path/parser_version/status.
available_at = 실제 시스템 first_seen 시각. 수집 당시 알 수 없던 과거 내용을 published_at으로 소급 공개하지 않는다.
검증된 역사 자료 import는 별도 provenance와 time_quality=HISTORICAL_VERIFIED, 근거 없으면 UNKNOWN.
Issuer: issuer_id/CIK(문자열 10자리, nullable)/법인명/sector tags/상장상태와 관측시각.
Security: security_id/ticker/exchange/currency/issuer_id/유효시작·종료. symbol 변경·동명이인·상장폐지 이력 보존.
EventCandidate: 원문 span·document_id·issuer/security 후보·event_type·raw_date_text·review_status.
CatalystRevision: event_id/revision_id/program_id/phase/event_type/date_precision/start/end/event_timezone
  /source_document_ids/available_at/observed_at/status/risk_class/mapping_status/reviewed_by/reviewed_at.
date_precision=EXACT_DATETIME/EXACT_DATE/WINDOW/MONTH/QUARTER/UNKNOWN/NO_EARLIER_THAN.
start/end는 event 경계이고 정확한 D-Day가 없는 경우 days_to_event_range로 반환한다. 확률로 변환하지 않는다.
RiskNotice: security_id/reason/severity/source_document_id/available_at/review_status.
CollectionResult: status=OK/EMPTY_CONFIRMED/PARTIAL/FAILED/UNSUPPORTED/RATE_LIMITED, items, errors, cursor, evidence.
PriceBar: security_id/session_date/interval/OHLCV/currency/source/basis/available_at/fetched_at/is_final/revision.
FeatureSnapshot: feature_version/as_of/bar_hash/feature_values/missing_reasons.
DecisionSnapshot: decision_id/run_id/security_id/event_ids/config_hash/feature_hash/as_of
  /setup_state/entry_eligible/strength/exhaustion/exit_action/target_cumulative_sell_fraction/reasons/data_health.
Fill: fill_id/command_id/position_id/security_id/side/Decimal(qty,price,fee)/currency/executed_at/evidence.
LedgerEntry: entry_id/command_id/type/amount_usd/position_id/occurred_at/recorded_at/reversal_of.
AllocationProposal: proposal_id/snapshot_id/security_id/budget/qty/cash_reservation/expiry/status.
DTO를 서명된 함수 계약으로 문서화하고 모든 구현 단계에서 재사용한다.

## SQLite 테이블과 불변조건
schema_versions, issuers, securities, issuer_aliases, mapping_reviews, source_documents, collection_runs,
event_candidates, catalyst_revisions, event_reviews, risk_notices, price_bar_revisions, corporate_actions,
scan_runs, feature_snapshots, decision_snapshots, fills, positions, ledger_entries, allocations,
withdrawal_periods, alert_outbox, worker_leases, config_profiles.
revision은 append-only; 삭제/덮어쓰기 대신 정정 revision·reversal. 현재 projection은 rebuild 가능.
같은 document hash·source, bar revision key, fill command_id, alert key에 UNIQUE.
fills·ledger·position·reservation을 한 transaction에서 갱신. SQLite foreign_keys/WAL/busy_timeout과 bounded retry.
한 ticker는 한 active position만 기본 허용, 여러 catalyst를 연결하되 슬롯·현금을 중복 계상하지 않는다.
금액·수량은 Decimal을 문자열로 저장/직렬화. 계산식 특징은 float 가능하지만 원장 float 금지.
DB backup/restore·migration은 명시적 명령이며 기존 DB를 자동 삭제하지 않는다.

## 선행검사와 health
네트워크 실패는 universe=0으로 대체하지 않는다. 소스별 마지막 성공·원문·parse 상태·coverage를 표시한다.
미승인 mapping/event는 후보 검토 화면에 남고 ENTRY 불가. 최신 확인 없는 위험정보는 안전하다고 표시하지 않는다.
가격·이벤트 stale이면 신규 ENTRY 보류. 기존 포지션은 DATA_REVIEW; 알려진 강제청산/RiskNotice는 계속 평가한다.
전략 설정 결측은 CONFIG_REQUIRED. 파서 미지원은 UNSUPPORTED. 진짜 후보 없음은 NO_MATCH.
Health는 소스·worker·시장시각·profile·원장에 대해 각각 기록한다.

## 인터페이스 최소 계약
collector.collect(cursor, observed_at) -> CollectionResult[EventCandidate or RiskNotice]
normalizer.normalize(candidate, mapping, review, as_of) -> CatalystRevision or typed validation issue
event_repo.at(as_of) -> 당시 이용 가능했던 revision만
price_provider.fetch(security,start,end,interval) -> CollectionResult[PriceBar]
quote_bridge.latest(security, as_of) -> timestamp/source/session/bid/ask/last/health; 기존 KIS 등 실제 시세 adapter 우선 재사용
features.compute(bars, benchmark, as_of, config) -> FeatureSnapshot
setup/trigger/strength/exhaustion.evaluate(context, config) -> typed result
stop/exit.evaluate(position, context, risk_notices, config) -> ExitDecision
ledger.record_fill(fill, command_id) -> updated projection
rollover.propose(decisions, ledger_snapshot, config) -> proposals
scan.run(as_of, config_hash) -> run_id; 동일 입력은 동일 결과, current 시각을 순수 엔진 내부에서 읽지 않는다.

## 접근·배포
기존 앱 인증을 우선 재사용, 개인 단일 소유자 원장. writer/read-only mode를 분리하고 원장 command는 인증된 소유자만.
외부 모바일 접속은 인증·TLS·persistent DB·single writer worker가 갖춰진 환경에서만 안내한다.
정확한 hosting을 정하지 않았으므로 무료 Streamlit hosting의 DB 지속성·24시간 worker를 보장하지 않는다.
Telegram transport는 dry-run 기본, 실제 발송은 사용자 설정·요청 전 수행하지 않는다.
