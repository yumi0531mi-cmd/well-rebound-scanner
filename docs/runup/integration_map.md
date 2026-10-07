# 런업 Step 00 — 실제 저장소 통합 지도
확인: 2026-10-06 11:09 KST / 2026-10-05 22:09 EDT. 코드 읽기·위치 확인이며 앱 실행·성과 검증은 하지 않았다.
실제 저장소: C:\Users\cj123\Documents\Codex\2026-08-27\e\work\strategy-filter-reentry
Default Project에는 기존 앱 코드가 없었으므로 이전 BLOCKED는 잘못된 작업 폴더에서의 조사 기록이다.

## 실제 파일과 연결
| 역할 | 확인한 파일·심볼 |
|---|---|
| UI 진입 | app.py:90 st.set_page_config, app.py:756 기존 sidebar, app.py:809 hero |
| 운영 실행 | start.py:main → shared_runtime_components → start_daemon_service → streamlit app.py |
| 공유 runtime | wellscan/scanner_service.py:250 shared_runtime_components |
| KIS client | wellscan/kis.py:58 KISClient, :342 get, :696 overseas_current_price |
| 시세 허브 | wellscan/quotes.py:15 QuoteBook.get, wellscan/realtime.py:32 RealtimeHub |
| UI resource | app.py:370 client, :400 history, :405 sequences, :410 validations |
| 시세/시장 master | wellscan/instruments.py:65 MasterCatalog, wellscan/candidates.py UniverseBook |
| 기존 분봉 캐시 | wellscan/history.py:51 HistoryCache, kis_data_fetcher.py KISMinuteDataFetcher |
| 시장 시간 | wellscan/sessions.py session_status, session_exchange, 기존 pandas_market_calendars |
| daemon | wellscan/scanner_service.py start_daemon_service, stop_daemon_service, daemon_service_status |
| 관리자 확인 | wellscan/web_status.py ADMIN_TOKEN_ENV 및 admin_* 함수; app.py:97 admin=backtest 분기 |
| 설정/진행 | config.py, state_manager.py:load_progress/save_progress, 기존 PROGRESS.json |
| 의존성 | requirements.txt streamlit==1.41.1, pyproject.toml Python>=3.11 |
| 회귀 검사 후보 | tests/test_web_app_boundary.py, test_web_status.py, test_quote_cadence.py, test_sessions.py, test_scanner_service.py, test_config_state.py |

## 통합 위치와 구현 경계
기존 앱은 st.tabs/router 기반이 아니라 app.py의 top-level UI다. 기존 탭이 있다고 가정하지 않는다.
런업 navigation 예정 경계는 공통 helper 정의 종료 후 기존 sidebar 시작 직전(app.py 현재 약756행)이다.
Step22에서 실제 1.41.1 지원 API로 단타/런업 표시 컨테이너를 구성해야 한다. 이 조사에서는 UI를 바꾸지 않았다.
기존 관리자 분기·st.stop·fragment 범위와 전역 변수를 확인하고, 양쪽 UI가 불필요한 새 scan/KIS client를 실행하지 않게 한다.
Streamlit 1.41.1에 없는 최신 tab selection/parallel fragment API를 사용하거나 전체 upgrade하지 않는다.
런업 코드만 runup/ 아래, app.py는 최소 mount. 기존 wellscan 전략 엔진·정책 변경 금지.
quote/current client는 공유 runtime으로 얻는다. KIS 새 인증 client·별도 rate limiter를 만들지 않는다.
KISClient의 현재가 시각은 datetime.now(UTC)인 수신시각이다. 거래소 체결시각으로 표시하지 않는다.
QuoteBook의 freshness/기존 1초 주기 정책을 런업 60초 정책으로 전역 변경하지 않는다.
기존 HistoryCache는 분봉 중심이다. 일봉 수집을 재사용할 수 있다는 근거는 아직 없으므로 Step10에서 공식계약을 확인한다.
MasterCatalog는 상품 참고정보 연결 후보이지 BIO/PHARMA/SPACE 분류와 sponsor mapping이 자동 완성된 API가 아니다.

## 충돌 방지
config.py에 RUNUP_CONFIG namespace만 추가; 기존 단타 변수·수수료·손절·70/80 목표 보존.
root PROGRESS의 기존 필수키를 유지하고 runup_scanner namespace로 런업 milestones를 분리한다.
runup DB는 .scanner_data/runup/ 전용 경로(설정값), 기존 Cockroach/분봉·sequence/validation 원장과 혼용 금지.
session_state key는 runup_*로 이름공간 분리, live fragment key와 scan_key에 영향 금지.
admin backtest 인증은 일반 사용자 화면 전체 인증이 아니다. 런업 원장 write 권한은 Step23에서 별도 명시적 gate가 필요하다.
worker는 기존 process daemon과 자원 공유하면서 런업 job lease·주기·저장소를 별도 관리한다.
다른 스캐너 작업의 미커밋 파일이 있으므로 손대지 않는다. push/deploy/restart는 이 단계의 범위가 아니다.

## 상태
Step00 실제 코드 조사: ACCEPTED_BY_CODEX.
Step01 골격 구현: NOT_STARTED. Step00 인정은 전략·모니터링·성과 검증 완료가 아니다.
