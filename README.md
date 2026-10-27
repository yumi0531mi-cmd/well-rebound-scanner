# Runup Scanner

미국 BIO·PHARMA·SPACE 종목의 이벤트 전 런업을 관찰하는 Streamlit 스캐너입니다.
자동 주문은 없고, 후보·진입가·목표가·손절가를 계산해 수동 매매를 돕습니다.

## 실행

Python 3.11 이상에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
streamlit run runup_app.py
```

또는 저장소 루트의 `start_runup.bat`을 실행합니다. 기본 주소는
`http://localhost:8502/`입니다.

필요한 환경변수:

- `KIS_APP_KEY`
- `KIS_APP_SECRET`
- `RUNUP_DB_URL` (선택: 원격 libSQL/Turso를 쓸 때)
- `RUNUP_DB_AUTH_TOKEN` (선택: 원격 DB 인증 토큰)
- `WELLSCAN_ADMIN_TOKEN` (선택: 24자 이상, 관리자 화면 사용 시)

비밀값은 채팅·문서·커밋에 기록하지 않고 실행 환경에만 넣습니다.

## 데이터

- 로컬 기본 저장소: `.scanner_data/runup/runup.sqlite3`
- 원격 저장소를 설정하면 `RUNUP_DB_URL` 대상에 저장합니다.
- 일봉·이벤트·판정·원장은 분리 저장하며, 승인되지 않은 이벤트는 ENTRY 근거로 쓰지 않습니다.
- 후보가 없다는 것과 저장소를 읽지 못한 것은 화면에서 구분합니다.

## 포워드 테스트

전략의 수익성은 아직 입증되지 않았습니다. `work/forward_test.md`의 사전등록 규칙을
수정하지 않고 `CLINICAL × 런웨이≥4` 가설을 관찰합니다. 평균 기대값이 0 이하이면
전략을 기각합니다.

## 클라우드

`render.yaml`의 `runup-scanner` 서비스 하나만 사용합니다. 무료 인스턴스 시간을
아끼기 위해 GitHub Actions가 평일 08:00–10:00 KST와 21:00–24:00 KST에만
health endpoint를 호출합니다. UptimeRobot의 5분 모니터는 24/7 기동을 만들 수
있으므로 사용하지 않습니다.

이 프로그램은 의사결정 보조 도구이며 수익을 보장하지 않습니다.
