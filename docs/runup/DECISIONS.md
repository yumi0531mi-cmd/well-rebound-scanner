# 런업 통합 결정 — 실제 저장소 확인 후
2026-10-06, 사용자 요청: Codex가 실제 저장소를 찾아 장애를 해소; 구현은 Muse 담당이라는 역할 분담 유지.
1. 기존 Streamlit 저장소는 integration_map.md의 strategy-filter-reentry 경로로 확정.
2. 기존 앱에 런업 tab/navigation 추가. 별도 신규 앱·Default Project scaffold 생성은 승인 범위가 아니다.
3. root AGENTS.md의 비밀정보·금지자료·기존 정책·공유 배포 규칙을 준수. 새 런업 요청은 별도 scope이며 기존 단타를 교체하지 않는다.
4. 단타 우선 목표·T1/종목수·비용 규칙을 런업 설계값으로 변경하지 않는다. 금지 날짜/holdout 자료에 접근하지 않는다.
5. root PROGRESS.json은 유지하고 runup_scanner namespace를 추가. 기존 state_manager의 필수키와 함수 signature 유지.
6. 기존 Default Project의 BLOCKED는 historical_wrong_workspace로 보존. 실제 저장소에서 Step01/02 구현이 수행된 것은 아니다.
7. 실제 코드 변경은 Muse Step01부터. Step00 ACCEPTED는 Codex의 파일 확인에 근거하며 Muse가 임의 승인한 것이 아니다.
8. 수정/배포 pending인 단타 작업이 있다. 기존 dirty 파일은 baseline으로 취급하며 reset/revert/stash/commit/push 금지.
9. 기존 Streamlit 1.41.1과 calendar library 우선 사용. 없다는 이유로 최신 동등모듈을 중복 설치하지 않는다.
10. 미래 단계 진행은 해당 milestone evidence를 검토한 뒤 결정한다. 지금 Step01만 진행할 수 있다.
