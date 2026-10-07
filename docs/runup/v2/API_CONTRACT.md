# 런업 V2 입출력 계약 (Step 02)

기준: `DESIGN_V2.md` §21, `API_CONTRACT_BLUEPRINT.md`가 원문 기준이다.
이 문서는 원문을 옮긴 정리본이며, 원문과 다르면 원문이 우선한다.
이후 단계는 임의 dict/context 추가로 이 계약을 우회하지 않는다. 단위·
nullable은 아래 표를 따르고, 표에 없는 값은 원문을 확인한다.
금액·수량·수수료는 `Decimal(str)`, feature/score는 유한 float도 허용,
시각은 UTC aware, `?`는 명시적 nullable이다.

## Context field 계약

| Context | field | type | unit | nullable |
|---|---|---|---|---|
| EvaluationClock | as_of | UTC aware datetime | timestamp | no |
| EvaluationClock | session_date/previous_session/next_session | string | session | prev/next만 yes |
| EvaluationClock | market_open/market_close | UTC aware datetime | timestamp | yes |
| EvaluationClock | calendar_version | string | version | no |
| ConfigSnapshot | schema_version | int | version | no |
| ConfigSnapshot | profile_name/profile_id/config_hash | string | id/hash | no |
| ConfigSnapshot | values | non-empty immutable Mapping[str, 설정값] | config | no |
| SourceHealthSnapshot | source_id/scope/capability/status | string | id | no |
| SourceHealthSnapshot | revision | int | count | no |
| SourceHealthSnapshot | last_success/last_failure/coverage | datetime/string | timestamp | yes |
| MarketContext | clock/security_id | clock/id | — | no |
| MarketContext | security/issuer | Security/Issuer DTO | — | yes |
| MarketContext | gate_issues/source_health/catalysts/risk_notices/corporate_actions | tuple | — | no(빈 tuple 가능) |
| MarketContext | issuer_id/program/earliest_risk_boundary/forced_exit_deadline/importance | string/int | — | yes |
| MarketContext | universe_observation | UniverseObservation | — | yes |
| BarContext | clock/security_id/source_id/basis/bar_hash/completeness | — | — | no |
| BarContext | bars/benchmark_bars | tuple[PriceBar] | bars | no(빈 tuple 가능) |
| BarContext | issues | tuple[DomainIssue] | — | no |
| SetupContext | market/config/features/pivots | DTO | — | no |
| SetupContext | previous_setup | SetupSnapshot | — | yes |
| TriggerContext | setup/current_close/current_ma/atr_previous/rvol/close_location | DTO/Decimal·float | price/ratio | no |
| TriggerContext | previous_close/previous_ma | Decimal·float | price | yes |
| TriggerContext | consumed_generations | tuple[str] | id | no |
| TriggerContext | setup_snapshot | SetupSnapshot | — | yes(유효 스냅샷) |
| ScoreContext | config/features/pivots | DTO | — | no |
| ScoreContext | previous_features/frozen_trigger_ref/current_bar/previous_bar | DTO/str | — | yes |
| StopContext | config/clock/position_id | DTO/id | — | no |
| StopContext | position | PositionProjection | — | yes |
| StopContext | quote/features/pivots | DTO | — | yes |
| StopContext | corporate_actions | tuple | — | no |
| ExitContext | stop/market/cumulative_target | DTO/Decimal | — | no |
| ExitContext | active_sell_reservations | tuple[str] | — | no |
| ExitContext | score | ScoreResult | — | yes |
| LedgerProjection | revision | string | revision | no |
| LedgerProjection | settled_cash/unsettled_cash/net_capital_floor/processed_realized/monthly_realized | Decimal | USD | no |
| LedgerProjection | reservations/profit_earmarks/tax_earmarks/positions/issues | tuple | — | no |
| NAVSnapshot | as_of/nav_usd | datetime/Decimal | timestamp/USD | no |
| NAVSnapshot | issues/position_values | tuple | — | no |
| NAVSnapshot | external_other_exposure | Decimal | USD | yes |
| AllocationContext | clock/config/ledger/nav | DTO | — | no |
| AllocationContext | decisions/triggers/quotes/markets | tuple | — | no |
| AllocationContext | stop_reference_ids/issuer_ids/sectors/security_ids | tuple[str] | — | no |
| SettlementContext | clock/config/ledger/nav/ny_period | DTO/str | — | no |
| SettlementContext | fee_tax_confirmed | bool | flag | no |
| SettlementContext | previous_period_revision | string | revision | yes |

## 고정 함수 계약 (§21)

```text
config.validate_runup_config(cfg=None) -> {errors, config_required}
config.runup_config_hash(cfg=None, profile_name=None) -> str
config.resolve_config_snapshot(values, profile_name=None) -> ConfigSnapshot dict
domain.validate(dto) -> list[DomainIssue]
domain.to_dict(dto) -> dict / domain.from_dict(cls, data) -> DTO
domain.stable_key(payload) -> sha256 hex
storage.transaction() -> context manager
repository.at(as_of, filters) -> list[DTO]
collector.collect(cursor, observed_at, source_policy) -> CollectionResult
normalize(candidate, mapping, review, as_of) -> CatalystRevision | DomainIssue
prices.fetch(security, start_session, end_session, as_of) -> CollectionResult[PriceBar]
features.compute(bars, benchmark, as_of, config) -> FeatureSnapshot
pivots.confirm(bars, as_of, config) -> PivotSet
setup.evaluate(context) -> SetupResult
trigger.evaluate(context) -> TriggerResult
strength.evaluate(context) -> ScoreResult
exhaustion.evaluate(context) -> ScoreResult
stop.evaluate(position, quote, features, config) -> StopResult
exit.evaluate(position, context) -> ExitDecision
ledger.record_fill(fill, command_id) -> LedgerCommandResult
ledger.rebuild(as_of) -> LedgerProjection
positions.confirm_settlement(command) -> LedgerCommandResult
rollover.propose(context) -> list[AllocationProposal]
rollover.approve(proposal_id, command_id, latest_context) -> Allocation
withdrawal.propose(period, context) -> SettlementProposal
withdrawal.confirm(proposal_id, command_id, latest_context) -> WithdrawalPeriod
scan.run(as_of, config_profile_id) -> run_id
jobs.start(runtime) / jobs.stop() / jobs.status()
alerts.enqueue(decision, event) -> OutboxResult
alerts.dry_run(queued) -> TransportResult
ui.render(read_models, command_services, auth_context) -> None
export.bundle(destination) -> ReviewManifest
```

구현은 위 signature를 그대로 따른다. `command_id`는 호출자가 만든 안정 ID이며
같은 ID+같은 payload는 기존 결과, 다른 payload는 IDEMPOTENCY_CONFLICT다.

## 순수성 보완 (Step 15·16, 원문 근거)
- `setup.evaluate(context, sessions)`·`trigger.evaluate(context, session,
  sessions)`: sessions는 calendar 서비스의 정렬 세션 목록이다. 엔진이
  calendar·DB·현재 시각을 직접 읽지 않기 위한 명시 입력이며, 만료·latch·
  deadline 계산에 사용한다. 그 외 입출력은 원문 signature와 같다.

## Command·Result·Lifecycle 자료형

| DTO | field | type | unit | nullable |
|---|---|---|---|---|
| Actor | actor_id/kind | id/string | — | no |
| AuthContext | actor/verified | Actor/bool | — | no |
| AuthContext | permissions | tuple[str] | — | no |
| AuthContext | verified_at | datetime | timestamp | yes |
| Reservation | reservation_id/security_id | id | — | no |
| Reservation | qty/amount_usd | Decimal | qty/USD | amount만 yes |
| Reservation | expires_at/status | datetime/string | — | no |
| Reservation | position_id | id | — | yes |
| Exposure | scope/amount_usd/as_of | string/Decimal/datetime | USD | no |
| PositionValue | position_id/security_id/qty/price_source | id/Decimal/string | — | no |
| PositionValue | market_value/price/price_time | Decimal/datetime | USD | yes |
| ReviewCommand | command_id/candidate_id/decision/date_precision/reviewer/expected_revision | id/enum | — | no |
| ReviewCommand | evidence_document_ids | tuple[str] | — | no |
| ReviewCommand | revision_id/start/end/timezone/issuer_id/program_id/event_type/importance_class | — | — | yes |
| FillCommand | command_id/fill/expected_ledger_revision/evidence | id/DTO/string | — | no |
| FillCommand | is_correction/is_reconciliation | bool | flag | no |
| FillCommand | allocation_id | id | — | yes |
| SettlementCommand | command_id/amount/confirmed_date/evidence/expected_ledger_revision | id/Decimal/date | USD | no |
| SettlementCommand | unsettled_ledger_ids | tuple[str] | — | no |
| ReserveExitCommand | command_id/position_id/qty/expiry/expected revisions | id/Decimal/datetime | — | no |
| ReserveExitCommand | decision_ids | tuple[str] | — | no |
| CapitalFlowCommand | command_id/flow_type/amount/flow_date/evidence | id/enum/Decimal/date | USD | no |
| CapitalFlowCommand | earmark_id | id | — | yes |
| CorporateActionCommand | command_id/action_id/effective_session/expected_revision | id/string | — | no |
| CorporateActionCommand | evidence_document_ids | tuple[str] | — | no |
| CorporateActionCommand | verified_ratio/verified_net | Decimal | ratio/USD | yes |
| ProfileCommand | command_id/profile_name/schema_version/config_values/expected_active_profile_id/config_hash | — | — | no |
| AllocationApprovalCommand | command_id/proposal_id/latest_context_hash | id/hash | — | no |
| AllocationApprovalCommand | expected_input_revisions | tuple[str] | — | no |
| WithdrawalApprovalCommand | command_id/proposal_id/period/revision/latest_context_hash | — | — | no |
| WithdrawalApprovalCommand | expected_input_revisions | tuple[str] | — | no |
| LedgerCommandResult | status/command_id/ledger_revision | enum/id/revision | — | no |
| LedgerCommandResult | event_ids | tuple[str] | — | no |
| LedgerCommandResult | projection/issues | DTO/tuple | — | projection만 yes |
| JobContext | job_id/handler_kind/clock/config/budget_seconds | id/DTO/number | seconds | no |
| JobContext | dependencies | tuple[str] | — | no |
| JobContext | cursor/lease_owner/fencing_token | string | — | yes |
| ScanRun | run_id/as_of/profile_hash/input_hash/status/health | id/hash/string | — | no |
| ScanRun | decision_ids/input_revisions/failure_reasons | tuple[str] | — | no |
| ScanRun | finished_at | datetime | timestamp | yes |
| RiskOverlay | security_id/observed_at/daily_decision_id/recommendation/health | id/string | — | no |
| RiskOverlay | event_revisions/quote_revisions | tuple[str] | revision | no |
| UISection | section/payload_hash/revision | string/hash | — | no |
| UIReadModels | revision/daily_as_of/profile_hash | string/hash | — | no |
| UIReadModels | sections/capabilities/issues | tuple | — | no |
| UIReadModels | risk_observed_at/coverage | datetime/string | — | yes |
| OutboxResult | accepted/idempotency_key | bool/string | — | no |
| OutboxResult | reasons | tuple[str] | — | no |
| OutboxResult | outbox_id | id | — | yes |
| TransportResult | delivered | bool | flag | no |
| TransportResult | issues/transport_receipt | tuple/string | — | receipt만 yes |
| ReviewManifest | manifest_id/destination/created_at | id/string/datetime | — | no |
| ReviewManifest | artifact_hashes/issues | tuple | — | no |

불변 규칙: 모든 DTO는 frozen이며 내부 container까지 freeze된다. 외부 입력
container 변경은 DTO에 전파되지 않는다. codec 명시 export만 새 값을 만든다.
aware datetime은 UTC로 정규화하고, 같은 수치의 Decimal은 scale과 무관하게
같은 canonical 문자열이다. naive 시각·미래 시각 판정은 순수 도메인이 아닌
명시 as_of·config 정책 단계에서 한다.
