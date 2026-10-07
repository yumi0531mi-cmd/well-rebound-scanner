# 공식 자료·provider 확인 메모
확인일 2026-10-05. 문서 존재/계약 확인과 실제 수집 smoke 성공은 별개다.
Muse는 구현 단계에서 실제 endpoint 응답·필드·허용범위를 확인해야 한다. 이전 대화의 사례 수익·사이트 가격은 사용하지 않는다.

## 1. ClinicalTrials.gov
공식 API 문서: https://clinicaltrials.gov/data-api/api
공식 study 구조: https://clinicaltrials.gov/data-api/about-api/study-data-structure
공식 NLM modernized API 안내: https://www.nlm.nih.gov/oet/ed/ct/2024/6-6_ctg-modern_508c.pdf
공식 용어: https://clinicaltrials.gov/study-basics/glossary
브라우저 도구에서는 동적 문서 본문과 개별 live JSON을 충분히 읽지 못했다. endpoint 필드·pagination은 구현 때 문서/응답으로 증명한다.
PCD와 결과 발표는 구분한다. 과거 버전 API를 추측해서 fallback하지 않는다.

## 2. SEC
공식 submissions/XBRL 안내: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
개발자 접근 규칙: https://www.sec.gov/about/developer-resources
공식 전체요청 ceiling 안내: https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits
공개 EDGAR API와 filing은 회사자료 연결에 사용한다. 식별 가능한 user agent와 fair access가 필요하다.
전체 요청은 여러 machine 포함 초당10 이하라는 공식 안내가 있다. 설계의2RPS는 그보다 낮은 제안값이며 기존 앱과 공유한다.
XBRL 데이터만으로 readout/PDUFA 일정을 자동 추출할 수 있다고 가정하지 않는다.

## 3. FDA·학회
FDA AdCom calendar: https://www.fda.gov/advisory-committees/advisory-committee-calendar
ASCO 공식 공개·embargo 정책: https://www.asco.org/annual-meeting/abstracts-presentations/abstract-policies-embargoes-exceptions/faqs
AACR 정책: https://www.aacr.org/meeting/aacr-annual-meeting-2027/abstracts/
학회 정책은 title·regular abstract·late-breaking 데이터 공개가 서로 다를 수 있음을 보여준다.
연도별 일시를 hardcode하지 않고 해당 회차 공식자료를 확인한다. 유료·비공개 전문 접근을 가정하지 않는다.
FDA calendar는 AdCom 회의목록이다. 완전한 forward PDUFA 일정 API로 취급하지 않는다.

## 4. SPACE
NASA schedule: https://www.nasa.gov/event-type/launch-schedule/
NASA missions: https://www.nasa.gov/launch-services-program-upcoming-missions/
FAA licenses: https://www.faa.gov/space/licenses
발사 예정·mission과 허가 정보를 분리한다. 허가문서의 날짜는 자동 launch 날짜가 아니다.
company IR와 issuer 관계를 추가 확인한다. NET/season 표현은 정밀도가 낮은 날짜다.

## 5. KIS·가격·거래소 시각
KIS 공식 저장소: https://github.com/koreainvestment/open-trading-api
해외 일봉 예제: https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/dailyprice/dailyprice.py
해외 현재가 도구 안내: https://apiportal.koreainvestment.com/tools-trading
기존 adapter를 먼저 읽고 재사용한다. endpoint·exchange symbol·pagination·MODP·관측지연·토큰 제한은 실제 환경에서 확인한다.
일부 다른 기간별 API의 미국 종목범위는 제한될 수 있어 전체 universe 조회 지원을 추정하지 않는다.
yfinance maintainer: https://github.com/ranaroussi/yfinance
download 계약: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
개인 연구용 대체 history adapter 후보이며 broker 공식자료와 동일하지 않다. maintainer는 Yahoo 비공식 도구·개인용 데이터 사용조건을 명시한다.
무료 라이브 시세·장기간 모든 분봉·공개 재배포 권리를 보장하지 않는다. 필요 basis 옵션은 명시한다.
거래소 calendar: https://github.com/gerrymanoim/exchange_calendars
확정봉·조기폐장·휴장/DST를 다루는 library 후보다. 기존 앱 calendar가 동등하면 재사용한다.

## 6. Streamlit·알림
Streamlit fragment: https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment
fragment의 run_every는 활성 session의 refresh다. 24시간 독립 scheduler로 취급하지 않는다.
기존 앱 버전의 지원을 확인하고 호환되는 API만 쓴다. 임의 전체 라이브러리 upgrade 금지.
Telegram은 사용자 요청 전 실제 메시지를 보내지 않는다. transport 구현은 dry-run과 독립시킨다.
