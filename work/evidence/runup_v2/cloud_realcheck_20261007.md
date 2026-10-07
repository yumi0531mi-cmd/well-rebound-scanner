# 실물 전수 점검 장치·결과 (2026-10-07 야간, "왜 자꾸 틀리냐" 대응)

## 장치
- Python 3.12 별도 venv(`uv python install 3.12`) + `libsql` 실물 + `tzdata` + `exchange_calendars`
- 하네스 `cloud_check.py`(repo 밖 temp): 실제 libsql 파일 DB로 생애주기 DB 구간 14단계 전수
- 기존 pytest는 3.14라 libsql을 import 못해서 stub 검사만 가능했음. 이게 놓친 원인.

## 잡은 실제 결함 3건 (검사 실패 → 수정 → 푸시)
1. 빈 결과 fetchall None (실 libsql은 DDL/INSERT에 None 반환) → `or ()` 처리. 푸시됨.
2. 격리 수준: libsql 기본값은 hanging transaction을 남김. `isolation_level=None` 명시로 sqlite3와 동일 autocommit. 푸시 `8bf51a2`에 포함됨.
3. Hrana 구버전 400 거부 → 공식 `libsql` 패키지로 교체.

## 하네스 결과
- 14/14 통과: migrate 8·입금·임상·검토·제안·재검증·승인·매수·매도·현금 계산·정산·정산 제안·확정·재오픈 내구성
- 남은 미확인: 원격 Hrana 프로토콜 자체(벤더 영역, stub 불가). 실 Turso 연결은 사용자 화면에서 준비 버튼으로 확인.

## 오판 기록
- 중간에 "제품 결함"으로 몰았던 hanging txn 1건은 하네스가 `isolation_level`을 안 준 장치 문제였음. 제품이 아니라 장치를 고침. 추측으로 제품 코드를 건드리지 않음.
