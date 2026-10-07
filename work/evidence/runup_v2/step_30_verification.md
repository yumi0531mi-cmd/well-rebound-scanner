# Step 30 검증표 (C01–O01 추적, 2026-10-07)

범례: PASS(검사 통과) / NEEDS_INPUT(운영 입력 부족) / LIVE_UNVERIFIED(실제 API 미확인) /
FAIL(기존 문제, 본 작업 무관). 합성 fixture만 사용, 백테스트·수익성 표시 없음.

| ID | 결과 | 근거 |
|---|---|---|
| C01 | PASS | `test_step_01_config` + `test_step_01_repair` |
| C02 | PASS | `test_step_01_config` (NaN/Inf/bool 거부) |
| C03 | PASS | `test_step_25_services::test_profile_json_roundtrip_hash_and_compare_swap` |
| D01 | PASS | `test_step_06_ct` (EMPTY vs FAILED) + `test_web_collection` 임상 2건 + `test_daily_cursor::test_empty_confirmed_only_on_real_empty` |
| D02 | PASS | `test_step_04_http` + `test_daily_cursor` 회전 3건(예산중단 복구·실패보존·커서) |
| D03 | PASS | `test_step_05_universe` (관측 소급 금지) + `test_daily_cursor::test_revision_dedup_and_no_backdate` |
| D04 | PASS | `test_step_05_universe` 매핑 3건 + `test_step30_full_lifecycle` 3단계(근거·이력 보존) |
| E01 | PASS | `test_step_06_ct` (PCD=TRIAL_COMPLETION_MARKER) + lifecycle 2·3단계 |
| E02 | PASS | `test_step_09_10_conf_space` (기존) |
| E03 | PASS | `test_step_07_08_sec_fda` (기존) |
| E04 | PASS | `test_step_11_normalize` (기존) |
| E05 | PASS | `test_step_11_normalize` + lifecycle 3단계(REVIEW 상한 유지) |
| P01 | PASS | `test_step_25_services` (UNAVAILABLE/PARTIAL) + lifecycle 4단계 |
| P02 | PASS | `test_step_12_prices` (기존) |
| P03 | PASS | `test_step_12_prices` + `test_risk_screen::test_position_risk_chain_no_quote_masquerade` |
| F01 | PASS | `test_step_13_features` (기존) |
| F02 | PASS | `test_step_14_pivots` (기존) |
| S01 | PASS | `test_step_15_setup` (기존) |
| S02 | PASS | `test_step_16_trigger` (기존) |
| S03 | PASS | `test_step_16_trigger` + `test_step_22_rollover` (소비 generation) |
| X01/X02/X03 | PASS | `test_step_18_stop`, `test_step_19_exit` (기존) |
| L01 | PASS | `test_step_20_ledger` (기존) + lifecycle 6·8단계(손계산: 원가 50·실현 100) |
| L02 | PASS | `test_step_21_positions` (기존) + lifecycle 9단계(미결제 150→결제, 중복 거부) |
| L03 | PASS | lifecycle 1단계(REPLAY/충돌 거부) + `test_step_20_ledger` |
| L04 | PASS | `test_step_20_ledger` + `test_step_23_withdrawal` (기존) |
| R01 | PASS | `test_step_22_rollover` (기존) + lifecycle 5단계 |
| R02 | PASS | `test_alloc_commands` 6건(중복·비용None·NAV·만료·동시예약) |
| R03 | PASS | `test_step30_r03_wait_paths`(후보 없음 대기) + 실DB 차단 상태 유지 |
| W01/W02/W03 | PASS | `test_step_23_withdrawal` + `test_step_23_review` (기존) + lifecycle 10단계(base 100·이익 50·CONFIRMED) |
| J01 | PASS | `test_step_24_jobs` (기존) + DISABLED 정직 표시(`test_risk_screen`) |
| J02 | PASS | `test_step_25_services::test_critical_overlay_keeps_daily_identity` + lifecycle 7단계 |
| A01 | PASS | `test_step_26_alerts` (기존) |
| U01 | FAIL | `test_mount_preserves_exact_legacy_ast` — 작업 전부터 `app.py` dirty라 기준 불일치(본 작업 무관, 수정 금지 유지) |
| U02 | PASS | `test_step_27_28_ui` 권한 2건 + `test_alloc_commands::test_readonly_direct_calls_rejected`(8경로) |
| U03 | PASS | `test_risk_screen` AppTest 1건(PC·모바일 동일 read model·hash 일치·폼). 375px 실기기는 NEEDS_INPUT |
| O01 | NEEDS_INPUT | `test_backup_restore_integrity_and_overwrite` — 실행 환경 basetemp가 workspace 밖이라 거부됨. 기본 tmp도 권한 없음. 별도 환경에서 재실행 필요 |

## 통합 검사 명령·결과
- `pytest tests/runup_v2 tests/test_web_app_boundary.py` 전체 1회: 226 수집 중 224 통과, 2 실패(둘 다 기존·환경 문제)
- 신규: `test_step_30_lifecycle` 2건 + `test_alloc_commands` + `test_daily_cursor` + `test_risk_screen` + `test_step_31_export` 2건
- 전체 1회: 228 수집 중 226 통과, 2 실패(둘 다 기존·환경 문제)
- 실DB 쓰기 0건(전체 합성 tmp DB). 실DB는 읽기 전용 대조만.
- 전략 백테스트·수익성 검증 미실행(명시적 금지 준수).
- 정정: 이전 증거의 "199 passed"/"215 passed"는 미확인 집계였음. 확인된 전체 결과는 본 항목이 유일 기준.

## 30단계 검사 중 수정한 결함(덮어쓰기 없음, 삭제·추가·수정으로 해결)
1. 정산 폼 크래시: `SettlementCommand` 필수 `expected_ledger_revision` 누락 → 현재 원장 revision 전달로 수정(`runup/ui/main.py`)
2. 마감 후 제안 즉시 만료: `_next_sessions`가 마감된 당일 종가를 포함 → 미래 종가만으로 수정(`runup/services/commands.py`)
3. 다중 제안 저장 충돌·보유수량 삼킴·빈 제안 침묵: 강화 감사에서 수정(증거 `harden_20261007.md`)
4. CT timezone 미상 후보의 REVIEW 상한: 완화하지 않고 검사 기대값으로 확정(잔여로 기록)
