# iPad 수신 보호 구현 검토 — 취소 경계 보완 요청

## 수용한 결과

입력 `ipad-catalog-receive-guard-implementation-reply-20260911.zip` SHA-256은 `a9c47b871f6e11c81f0a469f1f5d52d1970e0ce7afd1fbacb18e212820ad0461`이다. CRC·manifest 40개·크기/해시·경로·중복·링크·여분 파일 검사를 통과했다. 입력 요청 해시도 발신 `d18f051349aec50c860deba506660b40b6529ba116d25d8c3dd68090ed52f96d`와 같다.

변경 25개·신규 4개를 대조했고, 기준 후보의 기존 해시와 일치한다. 전달된 변경 후 소스에 patch를 역적용해 이전 해시를 재현하고 다시 정적용해 모든 변경 후 원문 바이트가 복원됨을 별도 디렉터리에서 확인했다. 변경되지 않은 기존 manifest 항목도 보존됐다. 최종 소스 digest `1494a72a6de09c62eff51ad1c7e3f12cf943dc415bc4de931a245809579dff7a`를 재계산했다.

제출된 XCTest 138회/고유 137개/실패 0은 tests.json과 tests.log의 시험명·횟수별 집합이 같다. 일반 구성 136개와 보호 모드 별도 프로세스 시험 2회라는 범위를 수용한다. 이는 iPad 개발 측의 실행 증거이며 Windows에서 Swift XCTest를 실행한 결과는 아니다. 기본 차단, 송신/큐 진입점 보호, 정책 저장 실패, 관련 일반 모드 회귀와 신규 수신 검증이 구현된 점은 보존한다.

**후보 빌드·설치로 넘기기 전에 아래 두 경계를 보완해야 한다.** 아래는 소스에서 확인한 검사 위치와 문맥 전달의 문제다. 실제 기기에서 취소 경합이나 자료 변경이 재현됐다는 뜻은 아니다. 이미 완료된 첫 실행을 다시 할 필요는 없다.

## R1 — HTTP 요청에 원래 읽기 허가를 고정할 것

근거: `ReceiveValidationPolicy.swift`의 `check`, `authorize`, `ReceiveValidationURLProtocol.startLoading/session`.

`check`는 expected가 nil이면 현재 유효 ticket을 받아들인다. URLSession에 넣는 전용 헤더에는 policy.process UUID만 있고 요청을 만든 허가의 generation/revision은 없다. `startLoading`에서 새 Task를 만든 다음 `policy.authorize(request)`로 그 시점의 ticket을 얻는다. 첫 요청의 ticket을 request/operation 경계에서 고정해 전달하는 코드가 없다. TaskLocal이 URLProtocol 콜백까지 유지된다는 검증에도 의존할 수 없다. 현재 시험은 전송이 시작된 **뒤** invalidate한 응답 거절을 검사하며, 전송 경계 진입 전 허가 A가 취소되고 B가 발급된 경우는 검사하지 않는다.

이 구조에서는 원 허가 정보를 잃은 지연 요청이 `authorize(request, ticket: A)`였다면 거절될 조건에서도, `authorize(request)`를 통해 현재 B로 허용될 수 있다. 응답 때 검사하는 ticket도 B이면 최종 HTTP 경계만으로 그 요청을 오래된 작업으로 식별할 수 없다. 상위 서비스가 응답 적용을 막더라도 취소된 인증/읽기 요청의 실제 전송을 막았다는 보장은 성립하지 않는다.

수정: 허가를 필요로 하는 요청에는 생성한 operation의 불변 ticket/문맥을 전송 경계까지 명시적으로 연결한다. SDK의 암묵 refresh도 원 읽기 작업의 허가와 연결한다. 콜백에서 현재 ticket을 새로 획득해 대신 쓰지 않는다. 구현 방법은 요청 문맥 또는 허가별 전송 객체 등 실제 SDK 경로에 맞춰 정하되, 상위 TaskLocal 상속을 전제로 하지 않는다. 알 수 없는 registry ID/문맥 누락도 검증 모드에서 차단한다. 내부 문맥은 서버 헤더·로그·토큰으로 유출하지 않는다.

신규 결정적 검사:

1. 허가 A에서 SDK/HTTP 요청을 준비하고 최종 loader 진입 전에 멈춘다. A를 취소하고 같은 계정·endpoint로 B를 발급/검증한다. 이전 요청을 풀어도 fake auth/HTTP 호출은 0이어야 한다. 비교용으로 B에서 새로 만든 요청은 성공해야 한다.
2. 암묵 SDK refresh 앞에서도 같은 A→취소→B 순서를 검사한다. actor/URLProtocol 콜백에 TaskLocal이 없는 실행 문맥을 포함한다.
3. 이미 A로 전송된 요청은 실제 호출 1을 보존하고, B가 생겨도 A 응답을 공개하지 않는다. 기존 이 시나리오는 재사용하되 재허가 경우를 추가한다.

현재 API의 `authorize(request, ticket: A)` 거절과 `authorize(request)` 허용을 대조하는 작은 정책 시험만으로 끝내지 말고 실제 사용하는 URLSession/SDK 경계에서 검사한다. 구현 API 변경에 맞춰 시험도 수정한다.

## R2 — 비동기 중단 뒤 실제 로컬 변경 경계에서 재검사할 것

근거: `SyncV2SnapshotPullService.swift:1021`의 `adoptEquivalentInitialDocument` await 다음 `replaceEquivalentLocalDocumentIdentity`/`mergeStore.resolve`에는 재검사가 없다. `:1176`의 `finish`/`resolve`도 앞선 apply await 이후 검사가 없다. `:1327`의 검사는 `stateStore.applySnapshotBaseline`을 await하기 **전**이다. 실제 저장 actor/파일 적용기가 처리할 때까지 허가가 유지됐다는 근거는 이 검사 하나로 확보되지 않는다.

현재 manifest 응답 직후 취소 시험은 그 지점의 차단을 확인하지만, 실제 적용기/저장소에 들어가 대기한 뒤 취소·만료되는 경우를 확인하지 않는다. journal 공개의 동기 잠금은 좋은 마지막 보호지만 앞선 파일/메타데이터 변경을 모두 대신하지 않는다. 선택/authorizeReceiving도 검사와 상태 대입이 별도 lock 구간이므로 재허가 사이에 오래된 선택을 새 상태에 대입하지 않도록 같은 문맥으로 확인한다.

수정: 실제 적용/기록을 시작하는 최종 위치까지 읽기 operation 문맥을 전달하고, 마지막 비동기 대기 이후 변경 직전에 검증한다. 필요한 동기 파일·DB commit은 취소/revision 갱신과 명확한 순서로 직렬화한다. NSLock을 async await 전체에 걸쳐 잡거나, 검증 실패를 일반 네트워크 실패/merge 실패로 바꾸어 추가 기록을 쓰는 방식은 피한다. 정리가 반드시 필요한 경우에는 허가된 수신 적용과 분리하고 어떤 비공개 임시 자원만 정리하는지 설명한다.

신규 결정적 검사:

1. 실제 receiver/stateStore/applier의 변경 직전 제어 가능한 지점에서 멈춘 뒤 policy.invalidate 또는 허가 만료를 발생시킨다. 작업을 풀어도 그 이후 새 identity 교체·원고/메타데이터/baseline 기록·merge 해결·수신 공개가 없어야 한다.
2. equivalent adoption 뒤 identity 교체, apply 뒤 finish/resolve, baseline actor 진입 대기 등 위 경계를 직접 검사한다. 특정 분기가 보호 모드에서 불가능하다는 판단이면 그 진입 불가능성을 코드/시험으로 입증한다.
3. 취소 전에 이미 완료된 합법적인 부분 수신과 journal은 그대로 보존하고 같은 ID로 재개한다. ‘전체 실행 중 아무 파일도 바뀌면 안 된다’는 기준으로 부분 수신을 삭제하거나 롤백하지 않는다. 취소 직전/직후 시점을 분리해 보호 행/원고/이력 해시를 비교한다.

## 작업·회신 범위

기존 코딩 요청의 후속 수정이다. 이번 구현 snapshot 위에서 두 항목의 수정과 재현/회귀 검증을 완료해 회신한다. 설치·실제 앱 실행·서버 시험 승인을 다시 요청하거나 해당 단계로 진행하지 않는다. 새로운 일반 권한 체계나 미래 송신 허가 발급 기능을 추가하지 않는다.

완료한 138회 시험을 근거 없이 전부 반복하지 않는다. 위 신규 경계 시험과 실제 변경에 영향을 받는 인증·수신·큐 보존 회귀를 실행한다. fake 응답/합성 자료·임시 정책 저장소·외부 네트워크 차단을 유지하고 실제 사용자 Keychain·DB·기기를 사용하지 않는다. 실패 재현 여부와 수정 전/후 결과를 구별하고 Swift 실행은 iPad 개발 환경에서 수행한다.

`ipad-catalog-receive-guard-boundary-fix-reply-20260911.zip`으로 변경 소스·이번 snapshot 기준 patch·변경 전후 해시·실제 시험 명령/결과·R1/R2 대응표·남은 한계를 보내 준다. 위 지적에 반대 근거가 있으면 해당 운영 경로의 불변 조건과 결정적 시험 결과로 설명한다. 문서 설명만으로 HTTP/적용 경계의 증거를 대신하지 않는다.

현재 설치본·정책 파일·전역 설정·관문·보류·프로필·Production은 변경하지 않는다. 사용자는 이 요청 ZIP을 iPad 개발 작업에 전달하고 iPad 오프라인/양쪽 앱 종료 상태를 유지한다. 추가 버튼 조작은 없다.
