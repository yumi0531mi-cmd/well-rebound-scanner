# 전략·수식·설정 계약 v1.0
이 수식은 재현 가능한 구현을 위한 제안 명세다. 예측력·승률·수익성 검증 결과가 아니다.
최종 요구: D-60 고정 매수와 고정 목표수익률 청산 없음. 조건 미달·적합후보 없음이면 현금 대기.
판단은 마지막 확정 일봉 t 기준. 후속 주문 가격·당일 종가 체결을 추정하지 않는다.
Hard Stop은 가격감시 데이터가 있어야 작동한다. 일봉에서 low 터치를 알아낸 것은 장중 실행 증거가 아니다.

## A. 특징 계산
C,O,H,L,V는 같은 basis의 OHLCV. N-session rolling에는 거래소 session과 충분한 연속 관측이 필요하다.
SMA_n(t)=mean(C[t-n+1:t]); MA periods 20/60은 원안의 지표 정의를 유지하되 config에서 변경 가능.
VolRatio5_20=mean(V 최근5)/mean(V 최근20). RVOL_t=V_t/mean(V[t-20:t-1]); 당일 V를 분모에 넣지 않는다.
ADV20=mean(C_i*V_i 최근20); provider 기준 단위와 splits를 검증한다.
MASpread=abs(SMA20-SMA60)/SMA20. Slope20=(SMA20_t-SMA20_(t-k))/(k*SMA20_(t-k)).
TR_t=max(H-L,abs(H-Cprev),abs(L-Cprev)); ATR14는 첫14 TR 평균으로 seed, 이후 (13*prevATR+TR)/14.
RawK14=100*(C-lowest(L,14))/(highest(H,14)-lowest(L,14)); SlowK=SMA3(RawK14), SlowD=SMA3(SlowK).
Stoch 14,3,3을 RawK만으로 위장하지 않는다. 범위=0·분모=0은 UNDEFINED, 신호 False와 구별한다.
CloseLocation=(C-L)/(H-L), UpperWick=(H-max(O,C))/(H-L); range=0은 UNDEFINED.
Momentum_n=C_t/C_(t-n)-1; RS_n=Momentum_n-security minus Momentum_n-benchmark.
Swing pivot는 좌 l봉·우 r봉 조건이 충족된 확인시각에만 이용 가능: pivot p의 known_at은 p+r 확정봉.
confirmed higher low=가장 최근 확인된 두 low pivot의 가격 증가. 신호는 알려진 pivot만 사용.
SwingHighRef=당시 알려진 가장 최근 high pivot; breakout=(Cprev<=ref_prev and C_t>ref_t).
신규 pivot·ref 변경만으로 breakout을 만들지 않도록 ref identity와 당일 cross를 함께 기록한다.
StructureBreak=C_t<last_confirmed_HL_price*(1-break_buffer). intraday touch는 별도 관측이며 종가판정과 혼합하지 않는다.
Efficiency=(C_t-Cprev)/ATRprev; high_rvol_low_efficiency는 두 config 임계값으로 정의한다.
lookback 전체 high는 peak_candidate 특징일 뿐 미래 확정 고점으로 사용하지 않는다.
회사 news 반응 특징은 news timestamp와 정의가 확보되기 전 MISSING_FEATURE, 텍스트 감성 추정값으로 대체 금지.

## B. 게이트·SETUP·TRIGGER
기본 게이트: 미국 equity·BIO/PHARMA/SPACE·verified issuer mapping·승인된 이벤트·유효한 profile·최신 필요한 자료.
현재 관측 mcap만 사용; 과거 검증에서 현재 mcap을 과거 필터에 적용하지 않는다.
초기 mcap 300M~5B·평균 거래량 300k는 원안 후보값이며 SPACE에 자동 이식하지 않는다.
이벤트 감시기간은 window 설정과 interval overlap으로 판단, 정확한 D-Day로 치환하지 않는다.
UNKNOWN date·NO_EARLIER_THAN만 있고 bounded risk window가 없으면 WATCH_ONLY, ENTRY 보류.
SETUP features: dryup, squeeze, MA20 recovery, positive slope, confirmed HL, stoch turn.
WeightedScore=100*sum(w_i*f_i)/sum(w_i), f_i in [0,1]; w nonnegative, 양의합 필수.
feature 결측이면 score=NULL·reason, 사용할 수 있는 feature만으로 임의 재정규화하지 않는다.
SETUP=게이트 통과 and setup_score>=threshold.
TRIGGER=SETUP and MA20 recovery/current_above and confirmed_HL and breakout and RVOL>=rvol_min and close_location>=min.
profile에서 required flags를 명시적으로 지정하며 Muse가 필수조건을 삭제하지 않는다.
SETUP 조건은 당일 dryup과 breakout volume을 동시에 강요하지 않게 setup_validity_sessions 동안 latch한다.
latch는 그때 이용 가능했던 snapshot으로만, 구조붕괴·risk invalidation·expiry이면 소멸.
Strength는 trend/HHHL/RS/volume_efficiency의 weighted score. score의 component normalization 경계도 config에 둔다.
SETUP/TRIGGER/RUN_UP 상태와 실제 FLAT/OPEN/PARTIAL/CLOSED 체결상태는 별개다.

## C. Exhaustion와 매도
Exhaustion components: rapid_gain_then_deceleration, climax_volume, large_upper_wick, failed_breakout,
poor_efficiency_with_high_rvol, weakening_RS, short_MA_loss, structure_break.
정량 flag는 config의 기간·경계값으로 정의한다. 데이터 없는 flag를 0으로 처리하지 않는다.
전량 안전판정(known event deadline/risk notice)과 기술적 score는 분리한다.
권고 매도 우선순위: event-forced/risk invalidation → hard_stop 관측 → structure_break → exhaustion 부분매도 → HOLD.
동시 신호는 가장 큰 누적매도목표 한 개와 모든 reason을 저장한다. 모순되는 HOLD와 FULL을 동시에 보여주지 않는다.
Exhaustion threshold 오름차순과 cumulative_sell_targets 오름차순 0..1을 config에서 검증한다.
주기적 스캔의 목표는 매번 잔량의 X%가 아니라 최초 수량 Q0의 누적 목표 fraction이다.
추가 권고수량=max(0,min(q_remaining,ceil_to_lot(Q0*target)-q_sold-q_reserved)).
target은 포지션별로 감소하지 않는다. 주문/매도 reservation은 expiry/cancel로 해제. 실제 체결 전 수량·현금 미변경.
부분체결·체결 취소·수량 조정은 원장 command로만; corporate split 때 Q0/sold/remaining/예약 단위를 함께 변환.
구조적 stop=last confirmed HL 기준 buffer 적용. hard_stop_price=entry_basis*(1-hard_stop_fraction).
둘 다 유효하면 long의 경고선=max(structural,hard); 단, 구조 기준을 입력가보다 높게 잡았을 때 trailing과 initial stop을 구분.
Hard Stop fraction 필수 미설정이면 신규 ENTRY 보류. 실시간 가격 미지원이면 장중감시 미지원 표시, 강제보장 금지.
종가·일봉 low·실시간 quote 감시는 별개의 evidence_type. 갭에서 stop 가격에 체결됐다고 쓰지 않는다.
자금 위험한도: q_risk=floor(risk_budget_usd/(entry_reference-initial_stop_reference)); 비용·lot·현금과 함께 min.
initial_stop>=entry 또는 두 stop 결측이면 신규 allocation 불가. 차트위험선과 실제 체결가는 별도.

## D. 이벤트 안전판
risk_boundary는 해당 종목에서 알려진 가장 이른 데이터 공개/바이너리 이벤트 경계다. 선택된 주 Catalyst만 보지 않는다.
EXACT_DATE는 이벤트 현지일 00:00을 earliest bound로, 월·분기는 해당 구간 첫날 00:00을 lower bound로 저장한다.
경계 전에 정해진 buffer trading sessions 만큼 앞선 거래소 session close가 forced_exit_deadline이다.
EXACT_DATETIME도 boundary 이전 session close를 찾고 buffer를 적용한다. holiday·조기폐장·DST는 calendar service가 담당.
월/분기 이벤트는 기간 진입 전 청산 권고와 보수적 ENTRY 판단. 불확실성을 midpoint 날짜로 제거하지 않는다.
PDUFA는 목표일 이전 결과 공개 가능성까지 표시한다. 알려진 일정 기반 경고가 조기 발표 갭을 차단한다고 보장하지 않는다.
발사 NET처럼 earliest 이후 무기한은 bounded source 없으면 WATCH_ONLY. 일정 변경시 revision 생성 후 보유 위험 즉시 재평가.
이벤트 발생·취소·위험 공지는 일봉 갱신을 기다리지 않고 known facts로 서비스가 재평가한다.

## E. Rollover·원장·출금
순위=eligible 후보만 (rank_score desc, liquidity desc, earliest_boundary asc, security_id asc).
rank_score의 strength/setup/importance 가중치는 config, importance는 근거·review가 있어야 사용.
현재 보유·기존 예약·동일 issuer 노출을 제외/제한. event 여러개라고 같은 ticker 중복 편입하지 않는다.
deployable_cash=ledger cash-reservations-withdrawal earmark-min_cash_buffer. 미실현이익과 청산 권고는 현금 아님.
빈 슬롯별 budget과 risk sizing으로 proposal만 생성; 승인 command가 reservation, fill command가 실제 잔고 변경.
V1 기본은 수동 체결, 미결제 매도대금은 deployable_cash에서 제외하는 보수적 정책. 실제 결제시각 입력/확인 필요.
position별 weighted average 비용기준을 채택. buy fee는 cost basis, sell fee는 net proceeds; 세무손익으로 주장하지 않는다.
remaining_cost=평균단가 포함원가*remaining_qty. realized_pnl=net_sale_proceeds-allocated_cost.
dividend는 별도 realized income, split은 수익 아님. FX 변동은 USD 원장손익과 분리 표시.
월 순실현손익에 모든 거래·명시적 운영비를 포함. unrealized gain은 출금 대상 아님.
carryforward 모드: P=cumulative_net_realized, H=이미 정산처리한 realized basis; new=max(0,P-H), monthly_net>0, 현금·equity_floor 여유 모두 충족.
withdrawable_base=min(new_realized_profit,deployable_cash,max(0,equity-equity_floor)); stale valuations이면 proposal 보류.
withdrawal_proposal=withdraw_fraction*base. 월 정산 확정 때 H+=실제처리base로 갱신한다. 현금제약으로 미처리된 basis는 남기고 중복 정산을 금지한다.
복리와 출금가능은 회계 분류다. 실제 송금은 없고, 사용자 출금확인 command가 cash와 외부흐름을 원장에 기록한다.
입출금이 이익/HWM으로 잘못 계산되지 않도록 외부 flow 별도. 세금 충당 정책 미설정이면 세후 출금액이라고 표시 금지.

## F. config.py — 설계자 초기 가설 프로필 DESIGN_V1_UNVALIDATED
사용자가 설계자에게 판단을 맡긴 데 따른 실행 가능한 초기 제안값이다. 최적화 결과·승률 검증값이 아니며 목표 달성 때문에 자동 변경하지 않는다.
운영: USD, America/New_York/Asia/Seoul, DAILY_CLOSED + INTRADAY_RISK_MONITOR, MANUAL_FILLS, 기존 Streamlit 탭, alerts dry_run=true.
일봉 재평가=확정 session 종료 후 provider finality 여유 15분. quote poll=60초(보유만, 기존 제한기 우선), 허용 quote age=120초.
이벤트 worker=30분, UI refresh=60초(읽기만), 위험공지 수집주기=소스 허용범위 안 5분, 이벤트 검토 TTL=24시간.
HTTP timeout=20초, max attempts=3, retry base=1초+bounded jitter, SEC RPS=2(기존 앱 공유 limiter로 ceiling 준수), 다른 소스 기본1 RPS.
max_positions=5. warmup_sessions=max(모든 lookback+confirmation 필요분)+20 여유; 봉이 부족하면 배제 이유를 표시한다.
MA20/60, ATR14, Stoch14/3/3, RVOL20, ADV20. benchmark는 SPY, 값·source가 결측이면 상대강도 포함 점수 불가.
watch_horizon=120 calendar days, entry_min_sessions_to_risk=3, forced_exit_buffer=1 full trading session.
pivot_left=3, pivot_right=2, hl_buffer=0.005, setup_validity_sessions=10, setup_score_threshold=70.
setup_dryup_max=0.50, setup_ma_spread_max=0.05, setup_slope_min=0, stoch_oversold=25, slope_lookback=5.
setup_weights={dryup:25,squeeze:20,ma20_recovery:20,positive_slope:10,higher_low:20,stoch_turn:5}.
ma20_recovery는 관측 10sessions 내 종가 cross 기록 또는 현재 C>SMA20이고 최근 cross가 setup latch에 저장된 경우다.
trigger_rvol_min=1.5, trigger_close_location_min=0.70, maximum_breakout_extension=1*ATRprev; 그 이상은 LATE_ENTRY_BLOCKED.
strength_weights={trend:30,structure:25,relative_strength:25,volume_efficiency:20}.
trend=clip(Slope20/0.005,0,1); structure=(confirmed_HH+confirmed_HL)/2;
relative_strength=clip(RS20/0.10,0,1); volume_efficiency=clip(RVOL/3,0,1) if C>Cprev else 0.
exhaustion weights는 8개 구성요소 각각12.5; thresholds=[35,55,75], cumulative_targets=[0.25,0.50,1.0].
rapid_deceleration=(Momentum5>=0.20 and Efficiency_t<Efficiency_prev), climax=RVOL>=3;
large_wick=UpperWick>=0.40 and CloseLocation<=0.50; failed_breakout=이전에 확정된 breakout ref 아래로 종가 복귀;
poor_efficiency=(RVOL>=2.5 and abs(Efficiency)<=0.20), weakening_RS=(RS20<=0 and RS20<RS20prev), short_MA_loss=C<SMA10.
structure_break는 기존 식. score target은 하향되지 않고 한 신호만으로 전량익절하지 않으나 독립 안전판은 즉시 FULL 권고 가능.
hard_stop_fraction=0.08(세 후보 -7/-8/-10 중 설계 초기값, 최적이라고 주장 금지). ATR 손절 자동 확장은 v1 비활성.
risk_per_position_fraction=0.005, max_position_nav_fraction=0.20, max_issuer_fraction=0.20, max_sector_fraction=0.60.
BIO/PHARMA 초기 mcap=[300M,5B]; SPACE mcap=[300M,None]는 별도 분야의 제안이며 바이오 기존상한 변경이 아니다.
min_volume20=300k, min_adv20=1M USD, min_price=1USD; sector별 동일 초기값. mcap 결측이면 통과시키지 않는다.
rank_weights={strength:0.5,setup:0.3,importance:0.2}; liquidity는 ADV20, importance는 공식출처로 확인한 분류의 점수다.
importance event mapping=first/key pivotal/major new project:100, confirmed readout/regulatory/conference data:75, routine repeat:25.
최소 importance=50, low confidence mapping 또는 미검토 중요도는 ENTRY 불가. 중요도는 사후 상승률로 판정하지 않는다.
min_cash_buffer_fraction=0.10, settlement_policy=MANUAL_CONFIRMED, allocation_expiry=next session open.
withdraw_fraction=0.50, monthly settlement, realized-carryforward mode, monthly net<=0이면 신규출금 적립0.
equity_floor=net capital contributions(이익 출금과 구분), fractional_lot=1share 기본; 기존 broker fractional 지원은 별도 검증시 반영.
fee estimate rate·minimum·slippage·tax reserve는 None: 실제 사용자의 broker 조건 입력이 필요하며 신규 allocation CONFIG_REQUIRED.
실제 fill fee를 원장에 기록한다. 비용 누락을 0으로 가정하지 않는다. 세금조건 미입력시 세후 출금가능액을 표시하지 않는다.
원장 초기자금·실제 source 자격은 사용자 입력이다. 모든 전략값의 종류·단위·허용범위·필수단계를 validator로 명시한다.
프로필 변경은 USER_CONFIGURED_UNVALIDATED. 어느 프로필도 VALIDATED라고 표시하지 않는다.
