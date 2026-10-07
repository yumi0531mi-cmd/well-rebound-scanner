# 단계 승인·통합 검사 기준 V2
각 개별 prompt의 검사 항목은 필수다. 아래 통합 ID는 최종 Step30/31에서 전부 추적한다.
기능 PASS,실제 API LIVE_VERIFIED/LIVE_UNVERIFIED,운영 READY/NEEDS_INPUT,전략 PERFORMANCE_UNVALIDATED를 따로 표시한다.

| ID | scenario | 기대되는 결과 |
|---|---|---|
| C01 | 117개 config 전 필드 삭제/null/타입/경계 | nullable 비용 None만 CONFIG_REQUIRED,누락은 오류; unknown key도 오류 |
| C02 | numeric bool/NaN/Inf/weight sum overflow | 모두 거부,hash도 invalid 거부,원본 불변 |
| C03 | profile/schema/hash 변경 | 새 snapshot·cache invalidation,구 profile migration 필요,기존 단타 불변 |
| D01 | source 200 empty vs parser/auth failure | EMPTY_CONFIRMED와 FAILED/PARTIAL 분리 |
| D02 | retry budget·private redirect·size 초과 | bounded 실패,secret 로그 없음,cursor 미전진 |
| D03 | 현재 처음 본 과거 publication | available_at은 first_seen,과거 시점 소급사용 금지 |
| D04 | issuer 유사명/다중ticker/sector 미검토 | REVIEW_REQUIRED,ENTRY 보류 |
| E01 | PCD만 있는 trial | 완료 marker/WATCH_ONLY,readout 날짜 생성 금지 |
| E02 | conference abstract가 main보다 먼저 | 실제 data release 중 earliest risk 적용 |
| E03 | FDA AdCom/FAA license만 존재 | PDUFA/launch 날짜로 대체하지 않음 |
| E04 | 월요일 event,buffer1 | 목요일 close deadline; 휴장·조기close/DST calendar 적용 |
| E05 | quarterly/NET/UNKNOWN/충돌/취소 | earliest bounded 또는 WATCH_ONLY/REVIEW/CRITICAL,revision append |
| P01 | missing session/진행봉/unknown basis | 계산 unavailable,ffill/0봉 생성 금지 |
| P02 | split-adjusted/provider change | 동일 basis lineage,이중 split/field별 provider혼합 금지 |
| P03 | KIS received-only quote | trade fresh로 오표시 금지,allocation 차단,POSSIBLE_STOP 표시 |
| F01 | Feature golden/분모0/warmup | 수식 hand-calculated expected,typed unavailable |
| F02 | future append/as_of revision | 과거 pivot/feature/setup/trigger 불변 |
| S01 | SETUP85/threshold/latch10 | qualified 전환 generation,latch 재평가 연장 없음 |
| S02 | TRIGGER3future sessions/next close | frozen previous ref cross,expiry/deadline 엄격 적용 |
| S03 | 소비한 generation·옛신호 재진입 | 중복/재매수 차단,새 setup+새cross만 허용 |
| X01 | Q01,target.25 및 FULL | partial0/SMALL_LOT_DEFER,FULL1 |
| X02 | Q0100,sold25,reserve10,target.5 | 추가15,rerun 추가 side effect0 |
| X03 | score 누락+event CRITICAL | FULL 권고 유지,실제 체결/현금 자동 변경0 |
| L01 | buy10*10 fee1/sell4*12 fee1 | 원가101,net47,released40.4,P6.6,remain60.6 |
| L02 | 매도 미결제와 결제확인 | confirmation전settled 증가0,중복confirmation 1회 |
| L03 | 동일 command replay/conflict/중간실패 | 1회적용/typed conflict/전체rollback |
| L04 | split·full sell·reversal·불일치 fact | 총원가/cash/P 불변,잔액0,reconcile보존,신규allocation차단 |
| R01 | pending+open max5/부분sell | slot미해제,동일issuer중복차단 |
| R02 | 동시 두 approve/costNone/NAVunknown | 중복cash사용0,CONFIG_REQUIRED/보류 |
| R03 | cash없는 후보/후보없는cash | 대기,조건완화·가짜후보0 |
| W01 | P100→60→110 carryforward | H100 유지,new0→10,평가익 제외 |
| W02 | cash/headroom 제한·f0·월손실 | 제한 base만 H처리,earmark범위준수,f0H소비없음 |
| W03 | profit/tax/capital withdrawal | earmark/cash/externalflow만 변경,capital만floor감소 |
| J01 | disabled/two workers/leaseexpired | 탭열기로기동0,한workercommit,oldfence거부 |
| J02 | critical notice 장중 갱신 | 일봉trigger재계산없이riskoverlay갱신 |
| A01 | rerun/targetincrease/transportfailure | 의미있는outbox만1회,실제송신0,원장불변 |
| U01 | 기존 단타/Streamlit1.41.1/rerun | 기존 1초fragment·admin·session·구독 보존 |
| U02 | read-only가 직접command시도 | server-side권한거부,UIdisabled만의보호금지 |
| U03 | PC/375px mobile/설정form | 같은readmodel/수치,profilehash일치,중복submit1회 |
| O01 | backup/restart/rebuild/export | consistent restore,replay일치,secret/pathescape 차단 |

통합 합성 scenario는 위 ID를 조합한다. 유효 synthetic bar/benchmark/event를 만들고 expected는 엔진 출력 복사가 아닌 수식으로 계산한다.
실제 source 응답은 요청 당시의 사실 증거로만 쓴다. 과거 이벤트 수익률·최적화 실행은 이번 검사에 포함하지 않는다.
manifest에 결과·검사 명령·exit code·시간·fixture 종류·live 미확인 이유를 기록한다.
작동하지 않는 지원 기능을 임의 UNSUPPORTED로 바꿔 PASS하지 않는다. 소스 접근제한은 승인된 manual fallback 범위와 구분한다.
진행 승인 체크: 현재 stage 기능검사 통과,기존 영향회귀 통과,artifact hash 일치,계약 충돌 없음,잔여문제 정확 표시.

