# V2 Step 02 증거 — 도메인 자료형·입출력 계약

- 상태: IMPLEMENTED (ACCEPTED 아님)
- 일시(UTC): 2026-10-06
- 선행: V2 stage_01 IMPLEMENTED (사용자 묶음 지시로 01·02 연속 수행,
  둘 다 ACCEPTED 전. Step 02 단독 선행조건은 사용자 지시로 갈음).

## 변경 파일
- `runup/domain/`: base(검증·codec·stable_key)·enums(11종)·documents(7종)·
  market(11종)·decisions(11종)·trading(11종)·ledger(6종)·collect(1종) +
  `__init__` 재수출. 전부 frozen dataclass, kind-hint 검증.
  DB/HTTP/Streamlit import 없음, 엔진 없음.
- `docs/runup/v2/API_CONTRACT.md`: context field 계약표 + §21 고정 함수 계약.
- `tests/runup_v2/unit/test_step_02_domain.py`: 신규 13건.

## 계약
- §5 DTO 전종 + blueprint context + §21 signature 문서화.
- 금액·수량 Decimal(str), float·bool·NaN 거부. 시각 UTC aware,
  naive=TYPE_ERROR·미래=REVIEW 정책 분리. UNKNOWN feature와 정상 false 분리.
- 직렬화→복원 값·단위 보존, frozen 변경 거부, stable key 순서 불변.

## 검사
- `pytest runup_v2 24건 + 기존 40건`: **64 passed, 0 failed**.
- `ruff check` 통과. DB·네트워크·백테스트·push·배포 없음. 합성 fixture만.

## 잔여
- CollectionResult items는 개별 DTO 검증으로 확인(제네릭 제거 한계 명시).
- NEEDS_INPUT 없음. 다음은 Step 03, 선행 ACCEPTED 후 진행.

## Repair (REVIEW D01~D12 반영, 2026-10-06)
- 상태 유지: IMPLEMENTED. ACCEPTED 선언 없음. Step 03 미시작.
- codec: 중첩 tuple[DTO]/nullable DTO/dict 일관화, from_dict 미등록 key 거부,
  finite float feature/score 지원(금액 float 계속 거부), aware UTC 정규화,
  Decimal scale 정규화(반올림 없음), naive 거부·미래 허용(벽시계 제거).
- 불변: FrozenDTO 공통 기반, 내부 dict/list까지 freeze, 외부 별칭 차단,
  명시 export만 새 값 생성. set 입력 거부.
- 검증: semantic 비교 전 타입 가드(예외 대신 Issue), PriceBar OHLC 필수·
  USD 통화·score 0..100·target 0..1, FeatureValue VALID/논리값/missing 구분,
  HL/HH FeatureValue, SourceDocument 출처 필수, collection 불변식,
  전 필드 실제 annotation + hint 일치 검사.
- 자료형 추가: 9 Command·LedgerCommandResult·JobContext·ScanRun·
  RiskOverlay·UIReadModels(+UISection)·OutboxResult·TransportResult·
  ReviewManifest·Actor·AuthContext·Reservation·Exposure·PositionValue,
  context typed 스냅샷 보강(security/issuer/setup/position/stop refs).
- API_CONTRACT.md에 전체 DTO·command·return 표 추가, 원문 우선 명시.
- 검사: repair 12건 포함 Step 02 범위 전량 통과(아래 전체 결과 참조).
  `ruff check` 통과. DB·네트워크·백테스트·push·배포 없음.

## 선행 잔여 해결 (BATCH_03_04, 2026-10-06)
- ConfigSnapshot.values·ProfileCommand.config_values는 비어있지 않은 불변
  Mapping[str, 설정값]이어야 하며 `{}·1·문자열·list`와 미freeze 내부를
  DomainIssue로 거부. 117개 규칙 semantic은 config.py 전용으로 복제 없음.
- 중앙 resolver 출력은 통과, deep-freeze·codec round-trip 보존 확인.
- 검사 `tests/runup_v2/unit/test_step_02_mapping.py` 3건 통과.
- status=IMPLEMENTED 유지. ACCEPTED 선언 없음.
