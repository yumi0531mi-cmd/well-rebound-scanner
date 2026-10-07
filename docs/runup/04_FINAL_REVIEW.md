# 최종 검증 인계 규격
Muse가 완성했다고 보고한 뒤 사용자가 이 채팅에 저장소와 이 자료를 제공하면 Codex가 검증한다.
구현 기능 검증과 전략 성과 검증은 분리한다. 웹앱 기능 검증 요청만으로 52주 전략 백테스트를 실행하지 않는다.

## 제출할 자료
- 실제 저장소 경로·commit 또는 변경 파일 manifest, Python/dependency 버전, 시작·worker·검사 명령.
- PROGRESS의 단계별 상태와 evidence, 기존앱 integration_map, config.py의 활성 config hash.
- 출처·captured/published/available_at이 있는 원문 표본, event 후보·승인·revision 기록.
- 실제 PriceBar의 source·basis·session·finality, quote timestamp/latency·실시간 지원 범위.
- scan feature·decision snapshot의 reason과 lineage. current 지표를 과거에 소급한 흔적 여부.
- sanitized 원장·체결·reservation·월정산 이력과 재구축 결과.
- source별 OK/PARTIAL/UNSUPPORTED·manual fallback·coverage, 남은 버그·데이터·운영상 제약.
- synthetic 검사자료는 실제자료와 별도, 실제 손익·승률로 표시하지 않는다.
- 비밀값·계좌번호·Telegram token/chat id는 제거. 환경변수 이름과 필요 조건만 제공.

## 기능 검증 항목
기존 단타 탭 회귀, 런업 탭/모바일, config hash·결측, worker와 화면 분리, 중복 작업·재시작,
공식 source 파서·coverage·mapping, 날짜 정밀도·earliest 공개·DST/휴장, first_seen와 미래정보 차단,
MA/Stoch/ATR/RVOL·confirmed pivots·split basis, SETUP latch·TRIGGER dedup·늦은진입 차단,
Strength/Exhaustion·hard/structure/event exit 우선순위, 부분매도 누적량·미체결/체결 분리,
Decimal 원장·fee·settlement·이중 현금 사용, 후보없음 CASH_WAIT, 월말손실·carryforward·출금정산,
dry-run 알림·재시도·시크릿 로그, backup/복구·인증·persistent data.
코드 검토 후 적절한 단위/통합/UI검사·소량 read-only 실제 source 연결을 실행한다.
실제 네트워크나 자격이 없는 항목은 미확인으로 남긴다. 수집 실패를 정상으로 평가하지 않는다.

## 별도 승인 후 전략 검증
당시 알려진 이벤트 모집단·실패/무신호 포함·historical source timestamp 검증, 거래가능 universe,
체결시간·fee/slippage/gap·split/basis·delisted, profile freeze와 학습/검증 구간 분리를 먼저 정한다.
그 후 동일 엔진으로 Trigger→실제 Exit·포트폴리오 cash/settlement·주별기회·MDD/PF를 산출한다.
일봉만으로 장중 순서·손절체결을 확정하지 않는다. 확률은 충분한 분리 검증·calibration 없이는 제공하지 않는다.
기존 사용자 KIS 실제 분봉 기준이 적용되는 성과 검증은 KIS source인지 별도 확인한다. Yahoo 일봉 검사를 KIS 분봉 결과로 바꾸어 부르지 않는다.
검증 결과는 PASS/PARTIAL/BLOCKED/FAIL, 실제 자료·검사 증거와 제한을 함께 보고한다.
