# Muse 단계별 실행 순서 V2

문서 bootstrap 후 Step01부터 한 개씩 전달한다. 각 단계 IMPLEMENTED 보고를 Codex가 검토하여 ACCEPTED한 뒤 다음 prompt를 전달한다.
Step00 기존 조사·증거는 재사용하고 처음부터 앱을 다시 만들지 않는다. 아래 V2 번호는 구 계획과 혼용하지 않는다.

| 단계 | 목적 | 선행 | prompt |
|---|---|---|---|
| 01 | 설정·schema·진행 복구 확정 | 기존00+bootstrap | [STEP_01.txt](prompts/STEP_01.txt) |
| 02 | 도메인 자료형·입출력 계약 | 01 ACCEPTED | [STEP_02.txt](prompts/STEP_02.txt) |
| 03 | SQLite·이력·transaction·profile 저장 | 02 ACCEPTED | [STEP_03.txt](prompts/STEP_03.txt) |
| 04 | HTTP·출처 보존·source registry | 03 ACCEPTED | [STEP_04.txt](prompts/STEP_04.txt) |
| 05 | 미국 Universe·issuer·ticker mapping | 04 ACCEPTED | [STEP_05.txt](prompts/STEP_05.txt) |
| 06 | ClinicalTrials.gov 수집기 | 05 ACCEPTED | [STEP_06.txt](prompts/STEP_06.txt) |
| 07 | SEC·기업 IR 촉매·악재 수집 | 06 ACCEPTED | [STEP_07.txt](prompts/STEP_07.txt) |
| 08 | FDA·PDUFA·AdCom 수집 | 07 ACCEPTED | [STEP_08.txt](prompts/STEP_08.txt) |
| 09 | 학회·초록·데이터 공개 캘린더 | 08 ACCEPTED | [STEP_09.txt](prompts/STEP_09.txt) |
| 10 | SPACE mission·FAA 캘린더 | 09 ACCEPTED | [STEP_10.txt](prompts/STEP_10.txt) |
| 11 | 정규화·검토·날짜 위험 경계 | 10 ACCEPTED | [STEP_11.txt](prompts/STEP_11.txt) |
| 12 | 일봉·시세 bridge·basis·finality | 11 ACCEPTED | [STEP_12.txt](prompts/STEP_12.txt) |
| 13 | Technical Feature Engine | 12 ACCEPTED | [STEP_13.txt](prompts/STEP_13.txt) |
| 14 | Confirmed pivot·HL·HH·frozen ref | 13 ACCEPTED | [STEP_14.txt](prompts/STEP_14.txt) |
| 15 | SETUP 점수·latch·generation | 14 ACCEPTED | [STEP_15.txt](prompts/STEP_15.txt) |
| 16 | TRIGGER·신호 소비·재진입 | 15 ACCEPTED | [STEP_16.txt](prompts/STEP_16.txt) |
| 17 | Strength·Exhaustion 점수 | 16 ACCEPTED | [STEP_17.txt](prompts/STEP_17.txt) |
| 18 | Structural Stop·hard stop·장중 위험 | 17 ACCEPTED | [STEP_18.txt](prompts/STEP_18.txt) |
| 19 | Exit 우선순위·누적 부분매도 | 18 ACCEPTED | [STEP_19.txt](prompts/STEP_19.txt) |
| 20 | 체결·현금 원장·command idempotency | 19 ACCEPTED | [STEP_20.txt](prompts/STEP_20.txt) |
| 21 | 결제·reservation·position·split | 20 ACCEPTED | [STEP_21.txt](prompts/STEP_21.txt) |
| 22 | 후보 ranking·allocation·물레방아 | 21 ACCEPTED | [STEP_22.txt](prompts/STEP_22.txt) |
| 23 | 월말 이익·tax earmark·출금 | 22 ACCEPTED | [STEP_23.txt](prompts/STEP_23.txt) |
| 24 | Worker lifecycle·lease·job scheduler | 23 ACCEPTED | [STEP_24.txt](prompts/STEP_24.txt) |
| 25 | 공통 scan·profile service·read model | 24 ACCEPTED | [STEP_25.txt](prompts/STEP_25.txt) |
| 26 | Outbox·알림 dry-run | 25 ACCEPTED | [STEP_26.txt](prompts/STEP_26.txt) |
| 27 | 기존 앱 런업 읽기 탭 | 26 ACCEPTED | [STEP_27.txt](prompts/STEP_27.txt) |
| 28 | 쓰기 form·설정·mobile·권한 | 27 ACCEPTED | [STEP_28.txt](prompts/STEP_28.txt) |
| 29 | 운영·backup·복구·의존성 문서 | 28 ACCEPTED | [STEP_29.txt](prompts/STEP_29.txt) |
| 30 | 전체 기능 통합·기존 단타 회귀 | 29 ACCEPTED | [STEP_30.txt](prompts/STEP_30.txt) |
| 31 | Codex 최종 검토용 export | 30 ACCEPTED | [STEP_31.txt](prompts/STEP_31.txt) |

