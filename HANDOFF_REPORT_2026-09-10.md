# 주식 스캐너 인계 보고서

작성일: 2026-09-10 KST  
대상 저장소: `well-rebound-scanner`  
로컬 작업 폴더: `C:\Users\cj123\Documents\Codex\2026-08-27\e\work\deploy-well-rebound`

## 1. 인계 시 가장 먼저 알아야 할 사실

- 이 프로젝트는 새로 만들 대상이 아니라 기존 스캐너를 이어서 수정하는 프로젝트다.
- 자동 주문 기능은 없다. KIS는 후보·분봉·현재가 수집에만 사용하며 매수·매도는 사용자가 직접 한다.
- 운영 웹 주소는 `https://well-rebound-scanner.onrender.com/`이다.
- GitHub 저장소는 `https://github.com/yumi0531mi-cmd/well-rebound-scanner`이며 운영 브랜치는 `main`이다.
- 로컬 브랜치 이름은 `multi-strategy-v040`이지만 `origin/main`을 추적한다. 푸시는 `git push origin HEAD:main`을 사용한다.
- 2026-08-17~19 데이터와 `holdout-candidates`는 사용자의 별도 승인 전 목록 조회, 읽기, 검색, 해시, 다운로드, 실행, 평가를 모두 금지한다.
- T1 도달률 80% 또는 허용 하한 70%는 아직 독립 검증으로 입증되지 않았다. 코드 테스트 통과와 전략 성과 입증을 혼동하면 안 된다.

## 2. 사용자가 요구한 최종 목적

- 국내 정규장과 미국 데이장·프리장·정규장을 각각 감시한다.
- 활성 매매기법들을 독립 평가하고, 한 기법당 5개가 아니라 각 세션에서 전략 전체 합산 후 중복 제거된 실제 ENTRY 종목이 최소 5개 이상 나오는 것을 목표로 한다. 조건을 만족하는 종목이 없으면 억지 신호를 만들지 않는다.
- 각 신호에 종목, 적용 기법, 추세, 현재가, ENTRY, 구조적 STOP, 최대 hard stop, T1, T2, ETA, 진행 상태, 갱신 시각을 표시한다.
- 실시간 스캐너와 백테스터는 같은 공통 전략·가격·실행 엔진을 사용해야 한다.
- 비용에는 시장별 수수료·세금·슬리피지를 포함한다.
- 목표 수치를 맞추기 위한 목표가 하향, 손절 확대, 비용 축소, 미래정보 사용, 실패 거래 제외는 금지한다.
- 웹과 모바일에서 같은 기존 URL로 확인할 수 있어야 한다.

## 3. 현재 활성 전략

현재 운영 allow-list는 9개가 아니라 6개다. 이는 최근 작업에서 기존 유망 3개와 서로 다른 특징의 신규 3개만 켜고 나머지는 OFF로 둔 상태다.

| 구분 | 활성 전략 |
|---|---|
| 기존 3개 | 박스권 반등, 급등 후 눌림, 과매도 반등 |
| 신규 3개 | 가격강도 선도주 눌림 재개, 유동성 스윕 후 회복, 전일 고가 돌파 후 재지지 |

정의 위치는 `wellscan/models.py`의 `ESTABLISHED_ACTIVE_STRATEGIES`, `EXPERIMENTAL_STRATEGIES`, `ACTIVE_STRATEGIES`다. 다른 전략 구현은 과거 기록 재현을 위해 남아 있지만 활성 실행 경로에서는 제외된다. 사용자의 새 승인 없이 전략을 임의로 삭제하거나 활성화하지 말아야 한다.

## 4. 현재 구조와 주요 파일

| 역할 | 파일 |
|---|---|
| 웹 UI와 모바일 CSS, 진행 이력 | `app.py` |
| 프로세스 시작, 상시 스캐너 실행 | `start.py` |
| 활성 전략 목록과 데이터 모델 | `wellscan/models.py` |
| 전략별 후보·확인·레벨 계산 | `wellscan/opportunities.py` |
| 공통 전략 선택과 평가 | `wellscan/engine.py` |
| ENTRY 허용, 비용, 상품 정책 | `wellscan/policy.py` |
| ENTRY/STOP/T1/T2 공통 실행 상태 | `wellscan/execution.py` |
| 실시간/과거 분봉 수집과 캐시 | `wellscan/history.py` |
| KIS REST 수집 | `wellscan/kis.py` |
| KIS WebSocket 현재가 | `wellscan/realtime.py` |
| 세션 판정 | `wellscan/sessions.py` |
| 브라우저와 독립된 상시 스캔 루프 | `wellscan/scanner_service.py` |
| 신호·성과 상태 저장 | `wellscan/validation.py`, `wellscan/sequence.py` |
| CockroachDB 저장 | `wellscan/bar_store.py` |
| 과거 재생 백테스트 | `wellscan/backtest.py` |
| Render 배포 정의 | `render.yaml` |

## 5. 완료된 핵심 수정

1. 국내 후보에서 상한가와 레버리지·인버스 계열을 제외하는 정책을 반영했다.
2. 후보 → 확인 → 실제 ENTRY 단계를 분리하고 활성 6전략을 독립 평가하도록 구성했다.
3. 하단에 하루 동안의 후보 이탈·진입 진행 이력을 복원했다. 종목별 진입가 도달, T1, T2, 손절 상태를 번호 목록으로 표시한다.
4. 기존 엔진 선택식이 `established or experimental`이라 기존 전략 후보가 하나라도 있으면 신규 전략 결과 전체가 버려지는 오류를 `established + experimental`로 수정했다.
5. `ENTRY_MAX_PREMIUM_ATR`을 0.25에서 0.5로 넓힌 커밋은 조건 완화이며 0 ENTRY의 원인이 아니어서 다시 0.25로 복구했다.
6. 후보가 0건인 정상 스캔은 세션 완료 표식을 발행하지 않아 웹이 계속 “첫 결과 준비 중”으로 보였다. 빈 결과도 완료된 세션 스냅샷으로 발행하도록 `scanner_service.py`를 수정하고 회귀 테스트를 추가했다.
7. 앱 표시 버전은 `0.7.2-empty-session-status`, 공통 엔진 버전은 `multi-strategy-18-independent-six-common-execution`이다.

## 6. 마지막 검증 결과

- 로컬 Render 동등 회귀 테스트: 460개 수집, 460개 통과, 실패 0개.
- 마지막 런타임 수정 대상 테스트: 25개 통과.
- `ruff` 정적 검사: 통과.
- 빈 세션 상태 수정과 최초 인계 보고서가 포함된 운영 코드 커밋: `f8ded86baad3ac5a5d5d668693f2f79a0782a369`. 보고서 상태 갱신 커밋은 이후 이어질 수 있으므로 실제 최신값은 `git rev-parse origin/main`으로 확인한다.
- Render 최신 배포 `f8ded86`: 빌드 성공, `Live` 확인(배포 시간 2분 29초).
- `/_stcore/health`: HTTP 200 `ok` 확인.
- 실제 운영 웹에서 앱 `0.7.2-empty-session-status`, 엔진 `multi-strategy-18-independent-six-common-execution`, 활성 6기법, 시장 상태, 현재가 갱신 설정, 하단 진행 이력 표시를 확인했다.
- 확인 당시 국내장은 장 마감 상태라 실시간 신규 ENTRY와 가격 연속 갱신은 재현할 수 없었다.

## 7. 아직 입증되지 않았거나 남은 문제

| 항목 | 실제 상태 |
|---|---|
| T1 도달률 80%/70% | 미입증. 독립 구간 성과 검증 완료 전에는 주장 금지 |
| 세션별 실제 ENTRY 5종목 | 목표일 뿐 안정 재현 미입증 |
| 미국 프리장 새 배포 직후 | 스캔 주기는 약 47~49초로 종료됐지만 후보 결과는 0건이었음 |
| KIS WebSocket | 후보가 없을 때 웹에는 “연결 시도 중”으로 표시됨. 실제 가격 연속 갱신은 새 ENTRY 후보가 나온 장중에 재확인 필요 |
| Render Free | 비활성 후 cold start가 50초 이상 걸릴 수 있음 |
| 속도 | 현재 상시 구조 계산 주기는 최대 60초이며 0.5초 전 종목 구조 스캔을 달성했다고 볼 수 없음 |
| 경고 | pandas/NumPy timedelta 관련 DeprecationWarning이 다수 있으나 현재 테스트 실패는 아님 |
| GitHub Actions | 테스트 workflow가 없음. 현재 배포 gate는 `render.yaml`의 pytest + ruff임 |

빈 후보 완료 수정은 “0건”을 “로딩 중”으로 오표시하는 문제만 해결한다. 후보 수를 인위적으로 늘리거나 ENTRY 조건을 완화하지 않는다. 해당 수정은 운영 웹에 반영됐다.

## 8. 외부 환경과 비밀값

Render 서비스에는 다음 환경변수가 필요하다.

- `KIS_APP_KEY`
- `KIS_APP_SECRET`
- `DATABASE_URL`
- 관리자 화면을 쓸 경우 `WELLSCAN_ADMIN_TOKEN`(24자 이상)
- `PYTHON_VERSION=3.12.8`

비밀값 자체는 이 보고서나 Git에 기록하지 않았다. 다른 AI에게 비밀번호·KIS secret·DB URL 원문을 대화로 전달하지 말고 Render 환경변수 화면에서 존재 여부만 확인하게 해야 한다.

## 9. Git 작업 시 주의

- 저장소에는 추적되지 않은 연구 문서·연구 스크립트와 권한 오류가 나는 pytest 임시 폴더가 남아 있다.
- `git add .`를 사용하지 말고 변경한 production 파일과 테스트만 정확히 지정해 stage한다.
- 연구 파일은 운영 코드와 자동으로 섞지 않는다.
- 현재 production 인계에 필요한 최신 변경 파일은 `wellscan/scanner_service.py`, `tests/test_scanner_service.py`, `wellscan/__init__.py`, 이 보고서다.

## 10. 재현 및 배포 명령

PowerShell 기준:

```powershell
cd C:\Users\cj123\Documents\Codex\2026-08-27\e\work\deploy-well-rebound

# 로컬 핵심 회귀 테스트
.\.venv\Scripts\python.exe -m pytest -q tests/test_scanner_service.py tests/test_web_app_boundary.py --basetemp=C:\Users\Public\Documents\ESTsoft\CreatorTemp\wellscan-handoff
.\.venv\Scripts\python.exe -m ruff check wellscan/scanner_service.py tests/test_scanner_service.py

# 상태 확인
git status --short --branch
git log --oneline -12

# 반드시 파일을 명시해서 커밋
git add wellscan/scanner_service.py tests/test_scanner_service.py wellscan/__init__.py HANDOFF_REPORT_2026-09-10.md
git commit -m "Publish empty scan completion and add handoff report"
git push origin HEAD:main
```

Render는 `main` 커밋을 자동 배포한다. 빌드 명령은 `render.yaml`에 있으며 지정된 연구 테스트를 제외한 production 회귀 테스트와 `ruff check .`를 실행한다. 배포 완료 후 다음을 확인한다.

1. Render deploy 상태가 `Deploy succeeded | Live`인지 확인한다.
2. `https://well-rebound-scanner.onrender.com/_stcore/health`가 HTTP 200 `ok`인지 확인한다.
3. 실제 웹을 새로고침하고 앱 버전이 `0.7.2-empty-session-status`인지 확인한다.
4. 국내/미국 선택, 세션 상태, 후보·ENTRY 카드, 하단 진행 이력이 표시되는지 확인한다.
5. 후보가 0건이면 무한 로딩 문구가 아니라 완료된 0건 상태가 표시되는지 확인한다.

## 11. 다른 AI에게 전달할 것

다음 네 가지면 바로 이어갈 수 있다.

1. 이 로컬 폴더 전체: `C:\Users\cj123\Documents\Codex\2026-08-27\e\work\deploy-well-rebound`
2. 이 인계 파일: `HANDOFF_REPORT_2026-09-10.md`
3. GitHub 저장소 주소와 운영 `main` 브랜치
4. Render 서비스 주소와 대시보드 접근 권한(비밀값 원문은 전달하지 않음)

다음 AI에게는 먼저 `git status`, 최신 커밋, Render Live 버전만 확인한 뒤 이 보고서의 “아직 입증되지 않은 문제”부터 이어가라고 요청하면 된다. 이미 완료된 전수 감사를 처음부터 반복할 필요는 없다.
