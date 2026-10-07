# 비용 평균값·SEC 자동 수집 증거 (2026-10-07, 사용자 지시)

## 비용 4종 (실DB 저장, 사용자 지시 반영)
- 프로필 `AVG_FEES_UNVALIDATED` 신규 저장(hash `cb518b...`), 기존 스캔은旧 hash라 재수집·재계산 필요
- `fee_estimate_rate=0.0025` (국내 온라인 미국주식 0.25% 관행 — 증권사별 확인 필요)
- `fee_minimum=1` (건당 최소 USD 가정 — 실제 최소값 확인 필요)
- `slippage_estimate=0.001` (모델링 가정, 시장 평균 같은 건 없음 — 확인 필요)
- `tax_reserve=0.22` (양도세율 22% 기준 — 개인 공제·상황 따라 다름)
- 전부 미검증. 배분·정산 계산이 이 값을 쓰므로 증권사 표와 대조 전에는 제안액을 그대로 믿지 말 것.

## SEC 자동 수집 (신규)
- `runup/services/collection.py::prepare_sec`: ticker→CIK 매핑(company_tickers.json) 후 CIK 순환, 예산 내 처리, 커서 `sec_cik`, 실패 보존·다음 순환 재시도
- UA 없으면 수집 없이 `NEEDS_INPUT` 기록(위장 금지). UA 값은 로그·DB에 남기지 않음
- `Commands.collect_sources`에 연결: 임상·KIS 뒤 SEC 요약까지 한 번에
- 검사 `test_sec_auto.py` 3건 통과(미입력 보류·매핑/회전/중복방지·예산복구·실패재시도)

## SEC 연락용 UA
- 상태: 사용자 제공으로 등록됨(값은 기록하지 않음). `RUNUP_SEC_USER_AGENT` PC 환경변수(setx) + 실행 프로세스 전달, 앱 재시작 완료.
- 검증: company_tickers.json 200 확인. 수집 버튼을 누르면 임상·KIS 뒤 SEC 순환이 돈다.

## 자동 불가 범위(정직 고지)
- FDA·학회·SPACE 수집기는 구조 미확인으로 의도적 UNSUPPORTED — 스크레이퍼를 지어 날짜를 뽑으면 오작·위조 위험이라 손대지 않음. 수동 확인 필요(잔여)
