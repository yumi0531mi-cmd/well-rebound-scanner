# Muse 구현 후 검토 절차
이 절차는 모든 두 단계 묶음에 적용한다. 명세 전체를 매번 다시 설계하지 않는다.
구현이 끝났다는 주장과 실제 검사 결과를 구분하고, 아래 두 번째 검토 pass를 완료한 뒤 STOP한다.
Muse의 자체검토는 독립된 다른 모델·사람이 수행한 검토가 아니다. 이름과 범위를 정직하게 기록한다.

## Pass A — 구현자
현재묶음기능/입출력/실패계약과허용범위를읽고구현한다.
해당변경기능검사·관련회귀·ruff를수행한다. 파일diff와before/afterhash를남긴다.
PROGRESS에는아직구현결과만기록한다. 검사개수를보안/기능/성과전체완료의증거로쓰지않는다.

## Pass B — 검토자 관점
1. 코드부터정답을정하지않고원문설계·schema·matrix를먼저읽는다.
2. 현재묶음요구항목별로구현파일/함수/검사명/expected근거/결과를trace에연결한다.
3. '존재한다'와'작동한다'를구분한다. 함수·DTO·table·설정metadata만있고실제동작이없는지찾는다.
4. schema metadata와validator가분리된수동목록인지확인한다. 전체필드타입/범위/누락/null이실제검사되는지본다.
5. DTO의annotation/hint/nullable/codec/중첩자료형이일치하는지본다.
   Any/특별예외목록으로정상fixture를통과시키고실제mapping을검사하지않는방식을거부한다.
6. 정상값뿐아니라경계equal/justoutside/None/missing/wrongtype/NaN/Inf/bool/중복/부분실패를검사한다.
7. 기대값은설계수식·schema·공식원문·원장손계산이다. 구현출력을expected로복사하지않는다.
8. source시험이mock transport로만통과한경우실제접속 성공이라고쓰지않는다.
9. source/date/markettime/available_at/basis/결측이뒤에서조용히0/정상/특정일로변하는지추적한다.
10. cash/qty/state변경은권고/proposal이아닌명시command transaction인지본다.
11. 부작용없는함수의실제입력변경/전역now/DB/HTTP호출을찾는다.
12. errorhandler가원인·자료유형을보존하는지본다. 'except:continue'나정상empty 위장은금지다.
13. testhelper가의미없는fixture를정상이라고만들거나assert목록/검사항목을제외하는지본다.
14. 기존단타기능·client/limiter·env/secret·금지자료·동작signature 영향을확인한다.
15. 결과를문서로남긴뒤찾은문제만수정하고영향검사를재실행한다.

## 반복을 줄이는 검사 선택
현재diff·새interface·이전known문제·의존범위로필수검사집합을선택한다.
이미통과한결과를아무이유없이재실행하지않는다.검토가새우려를발견하면그사례만추가한다.
negative test의expected를'validator가무조건error내야한다'로일괄정하지않는다.
nullable·정상미래일정·휴장·정상false·실제fractional fill처럼허용된값은positive로검사한다.
동일버그의변형case 수와서로다른원인수를구분한다.
사용자비용·source credential 등미입력은알려진운영제한이며해당동작만보류한다.
명세충돌을고치려고전략기준을변경하지않는다.

## 반드시 보는 기초 계약
- 모든117설정:key/delete/null/type/범위/finite/weight sum/component/enum/profile/hash.
- snapshot 외부별칭/중첩dict/list/tuple·mapping view로값이변해hash와달라지는지.
- 비어있지않은모든DTO/command/context codec roundtrip,빈설정과잘못된mapping 거부.
- observation시각과예정future event시각분리,UTC동일instant/Decimal동일value canonical.
- source200빈본문/잘못된JSON/필수구조누락과정상empty목록분리.
- source fixture/live/전체coverage/실시간성/검증된수익성을각각분리.
- transaction rollback/replay/동일ID다른payload/concurrentreserve/leasefence.
현재묶음에영향없는과거항목은기존검사를재사용하고checkpoint에서연결을확인한다.

## 검토 결과
파일:work/evidence/runup_v2/review_batch_XX_YY.md.
fields:version,batch,reviewer=Muse,review_kind=SELF_REVIEW,changed_files,inputcontract,
requirement_test_map,findings(priority/file/function/evidence),fixes,commands/exitcodes,
synthetic_vs_live,remaining,latest_manifest,next_batch.
review_status=PASS는필수요구가구현/검사됐고blocking findings가없는경우만.
review_status=FAIL은필수기능실패,PARTIAL은확인하지못한필수항목이다.
NEEDS_INPUT/LIVE_UNVERIFIED는별도운영coverage로보존한다. PASS와섞지않는다.
stage status=IMPLEMENTED는해당필수검사와review가PASS인경우만. ACCEPTED를스스로선언하지않는다.
같은문제를반복보완해도통과못하면원인/확인한증거/영향범위를BLOCKED로남긴다.
추가전체재설계/불필요한코드덤프/자동다음묶음시작을하지않는다.
사용자가다음이라고지시하면이전묶음review를읽고해당검사가PASS인지확인하고진행한다.

