# Well Rebound Scanner

기존 `ymym`과 코드·상태·배포를 공유하지 않는 독립 Streamlit 스캐너입니다.

## 신호 순서

1. KIS 거래량 TOP100과 거래대금 TOP100 합집합
2. 15분봉 정배열 또는 정배열 전환
3. 5분봉 이격도 수렴 + Stochastic Slow(11,4,4) 우물 반등 + MACD(5,20,5) 상승 전환
4. 3분봉 높은 저점 + 거래량 증가 + 세션 VWAP 회복
5. 첫 반등고점 돌파 시 `FINAL_BUY`

국내주식은 정규장에만 신호를 만들고, 미국주식은 뉴욕 현지시간과 서머타임을 기준으로 데이·프리·정규·애프터장을 자동 구분합니다. 현재가는 KIS WebSocket 체결가를 우선 사용하며 사용자가 1·3·5초 화면 갱신을 선택합니다. 구조 계산은 완성된 분봉에서만 60초마다 실행합니다. WebSocket이 끊기면 REST 현재가를 3초 캐시로 안전 대체합니다.

## 실행

Python 3.11 이상에서 환경변수 `KIS_APP_KEY`, `KIS_APP_SECRET`을 설정한 뒤 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Render에서는 저장소 루트의 `render.yaml`을 Blueprint로 배포하고 위 두 비밀값만 입력합니다. `SUPABASE_URL`, `SUPABASE_KEY`는 사용하지 않습니다.

## 무료 모바일 접속 대안

Windows PC를 켜둘 수 있다면 `start_mobile_server.ps1`을 실행해 로컬 Streamlit과 무료 Cloudflare Quick Tunnel을 함께 시작할 수 있습니다. KIS 키는 실행 중인 프로세스 환경변수에만 넣고 파일로 저장하지 않습니다. 표시되는 임시 `https://*.trycloudflare.com` 주소를 휴대전화에서 열면 됩니다. 임시 주소는 재시작할 때 바뀌며 Cloudflare가 운영 안정성을 보장하는 서비스는 아닙니다. 종료할 때는 터널 창을 닫고 `stop_mobile_server.ps1`을 실행합니다.

## 데이터와 안전장치

- 15분 MA60 계산 전까지 최소 900개의 실제 1분봉을 누적하며 임의값을 만들지 않습니다.
- 신호 단계, 분봉 캐시, 사후검증 자료는 `.scanner_data` 아래에만 저장하며 국내/미국·거래소·세션별로 분리합니다.
- 미국 분봉은 한 번에 최대 120개인 공식 조회를 연속 조회하고, 부족하면 다음 실행에서 더 오래된 구간을 누적합니다. 실제 900개 전에는 신호 대신 수집 상태를 표시합니다.
- SHAKEOUT, REAL_BREAKDOWN, HARD_EXIT, 15분 Cooldown, 일 3회 붕괴 Hard Kill을 구분합니다.
- 5·15·30분 MFE/MAE와 목표/손절 선도달을 기록합니다. 같은 봉에서 둘 다 닿으면 보수적으로 손절 우선입니다.
- 동일 전략·동일 엔진의 고정 신호 100건 전에는 실제 승률을 표시하지 않습니다.

이 프로그램은 의사결정 보조 도구이며 수익을 보장하지 않습니다. 자동주문은 구현하지 않았고, 주문 전 호가·공시·시장 상태를 확인해야 합니다.
