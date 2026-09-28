# iPad 양방향 준비 회신 대조 — 2026-09-11

현재 판정: **이미 구현된 iPad 전용 송신 경로를 확인했고, Windows 오프라인 준비안을 그 본문·lease 조건에 맞췄다. 수신 적용 전 보존 범위 검사 1건과 완전 소스 1개 확보가 남아 있어 설치/실행 수용은 아직 아니다.**

이번 첨부는 진행 중인 자료를 받은 뒤 대조를 계속하는 입력이다. README의 실행 단계와 ‘별도 승인된 송신 버튼’ 문구는 제안·과거 기록이며 새 실행 권한이 아니다. 이번 작업은 로컬 자료/패치 대조, Windows 오프라인 준비 코드 조정과 격리 검사만 수행했다. 설치·실제 앱/DB/기기/서버 조회·송신·관문/hold 변경·prod 전환은 하지 않았다.

## 확인한 자료와 한계

- 입력 ZIP: `ipad-bidirectional-preparation-review-20260911.zip`, 53,149 bytes, SHA-256 `84031ef5491ee2d6fd70611f10df40dbf9ef248cb3feb878e1faa10b9776c9e7`.
- manifest 12파일의 정확한 구성·중복/경로/링크 부재·CRC·크기·SHA 확인. 원본 사본과 추출본을 새 증거 디렉터리에 보존했다.
- 228파일 source manifest digest `8ae8b81c98a6fde218e088a625ecde26a0cb2b9a91565dbd4baef93b5ee7968d`를 독립 재계산했다. 이전 224파일 manifest 대비 수정 11개·추가 4개·삭제 0이며 task-changes와 일치한다. 수정 파일의 before hash도 이전 설치 후보 manifest와 모두 일치한다.
- 15개 패치 중 **14개를 정확한 기존 소스 사본/빈 신규 파일에 재생해 최종 SHA와 크기를 확인**했다. 실제 iPad 저장소·기존 증거 소스는 수정하지 않았다.
- `WriterPad/Data/Local/LocalDocumentStore.swift`는 해당 before hash의 완전 소스가 로컬 파일 및 iPad 관련 보존 ZIP 22개에 없었다. 변경 hunk 4개는 읽었으나 이 한 파일의 전체 패치 재생/완전 구현 대조는 수행하지 못했다. 실패를 15개 성공으로 바꾸지 않는다.
- iPad 결과는 **151개 통과·실패 0 보고**이며 이름/수량/고정 digest와 후보 식별의 일관성을 확인했다. 원본 xcresult/빌드 로그/앱 ZIP은 공유 묶음에 없으므로 Windows에서 빌드·서명·실기기·Swift 검사 성공을 재현한 것은 아니다.
- 보고된 후보는 `ipad-staging-body-bidirectional-20260911`, app ZIP SHA `59872d45ba06fe031f65894c1495a7fc571e6943fd090d8d843717dfc27be2eb`, bundle `com.chocos.writerpad.debug`, Staging 0.1.0(1), 미설치다. 실제 설치본으로 취급하지 않는다.

## 중복 요청을 제외한 구현 대조

| 내용 | 판정 |
|---|---|
| 대상 | 양쪽 모두 본문 작품 `9a78c51c-7de9-43a8-be54-d25a22d08a28`, 원고 `502cdbe7-814c-42f8-8ed4-81c40cd94902`, `메인/원고/1권/1화.txt` |
| 프로토콜 | 양쪽 모두 LEGACY 본문 시험. iPad `get_sync_handshake`로 작품 UUID/LEGACY 확인 후 진행. ID_BASED 일반 시험과 별개 |
| 전역 보호 | `sendingAllowed` false, 기존 `requireSending` 유지. 일반 dispatcher/lease/복구를 켜지 않고 `BodyValidationService`의 명시적 동작만 사용 |
| 로컬→큐→송신 | 비교 저장·durable journal·지정 작품 단일 claim·기존 commit client·complete 사용. 이 경로를 다시 구현하라는 이전 Windows 요청은 중복 |
| HTTP 경계 | 최종 URLProtocol에서 정확한 RPC/payload·run별 1회 예약, auth ticket/계정/epoch/기한 대조, redirect 거부. 전용 정책을 제거할 필요 없음 |
| 응답 불명 | iPad는 새 operation/retry/rebase/ensure 복구를 만들지 않고 inflight 보존. commit 또는 DB complete 실패 시 release도 호출하지 않는 현재 경로임 |
| 기존 journal 완료 후 수신 | 별도 body capability로 기존 binding의 pull/apply 문맥을 제공하므로 catalog 재가져오기 요청은 불필요 |
| 남은 제품 연결 | Windows는 여전히 오프라인 기준 코드만 있으며 실행 후보의 전역 잠금/전용 동작/최종 HTTP 연결·빌드가 필요 |

기존 general-test release/UUID 교정/본문 수신 재개/catalog/해시 보완은 다시 수행하지 않았다. iPad에 이전 광범위 후보 구현 요청서를 다시 보낼 필요가 없다.

## Windows 준비안 조정 — 실행 승인과 구분

이미 구현된 iPad 후보에 맞춰 **아직 미실행인 Windows 제안**의 문구를 통일했다. UUID·LEGACY·논리 송신 2회·revision 1→2→3은 그대로다. 옛 Windows 121/146 bytes 제안은 보존 기록으로 남기되 앞으로 이 시험 입력으로 쓰지 않는다.

| 단계 | 본문 | bytes | SHA-256 |
|---|---|---:|---|
| revision 1 | 기존 3줄 | 93 | `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d` |
| revision 2 | 마지막에 `Windows 양방향 검증 20260911` 추가 | 127 | `4cf361c4a26d342dbe712fec07f5ff7ccd3610605b471a87f5abc5250e8ca15f` |
| revision 3 | 이어 `iPad 양방향 검증 20260911` 추가 | 158 | `eee3691fbe8e6805a9d74b56dfd1d5cb9df53ad0c01e6325069cb091e68ff161` |

UTF-8 BOM 없음·LF·마지막 줄바꿈 포함. `aligned-proposed-plan.json`과 새 body-after 파일이 정확한 입력이다.

Windows acquire 요청은 기존 제품대로 90초, iPad는 후보대로 60초다. 보존된 Windows `public.commit_document.sql`에는 commit 수용 시 lease를 서버 시각에서 90초 연장하는 구문이 있어 iPad 보고와 부합한다. **이 파일은 과거 보존 정의이며 현재 서버를 새로 조회한 결과가 아니다.** 실행 시 현재 정의/실제 expiry를 확인하며 단순히 요청 후 60초 경과로 상대편을 시작하지 않는다. 정상 release 성공 또는 확인한 lease 만료를 기준으로 한다.

`bidirectional_sync_scope.py`의 새 문구, sender별 TTL, iPad unknown 상태에서 cleanup 자동 호출 금지를 맞췄다. 수정 전 코드/테스트는 이번 증거의 `windows-before/`에 보존했다. 변경 영향의 Windows 오프라인 검사 **15개 통과**, 소켓 차단·합성 DB/appdata 사용. 이 결과는 설치 제품 보호나 iPad의 151개 시험을 대신하지 않는다. 기존 증거 ZIP/검사 로그는 덮어쓰지 않았다.

## iPad에 남은 좁은 확인/보완 1건

**수신할 전체 snapshot이 이번 보존 범위 안인지, 첫 로컬 적용 전에 검사하는 경계가 필요하다.**

근거: 재생한 `BodyValidationService.perform`은 대상 원고의 revision 2/본문/path/nullable 필드를 확인한 뒤 일반 `puller.pull`을 호출한다. 최종 HTTP 정책은 해당 작품의 documents/folders/tree_orders GET을 허용한다. 그러나 이 서비스에는 그 전체 snapshot이 지정 원고+정렬 문서 2개, 고정 폴더 11개, 기존 정렬 revision/hash 그대로인지 확인하는 단계가 보이지 않는다. 수신 후 report의 정상 구조 판정은 ‘유효한 구조’ 여부이며 ‘이번 기준과 같은 구조’ 비교를 대신하지 않는다.

신규 `BodySnapshot` fixture도 일반 원고 1개만 반환하며 실제 기준의 정렬 제어 문서를 포함하지 않는다. 따라서 현재 정상 fixture 성공만으로 정렬 문서 변동이나 같은 작품에 추가된 원고가 적용되지 않음을 입증할 수 없다. 서버에 그런 변경이 실제로 발생했다는 뜻은 아니다.

필요한 회신 범위:

1. 기존 연결에서 이미 이 불일치를 **적용 전에** 거부한다면 정확한 함수·호출 위치와 해당 경계 근거를 제시한다. Windows의 별도 사전 조회만으로 iPad가 이후 수신하는 snapshot까지 같다고 간주하지 않는다.
2. 없다면 기존 pull/applier를 유지하면서 **실제로 적용할 동일 snapshot**에 고정 entity 집합·폴더 UUID/부모/이름/revision/deleted·정렬 UUID/revision/본문 SHA·원고 UUID/revision/본문/nullable 필드를 먼저 검증한다. 별도 GET 검사 후 다시 조회하는 방식으로 경쟁 구간을 남기지 않는다. 정상 부모 증명이나 protocol guard를 끄지 않는다.
3. 이번 영향만 검사한다: 정상 2문서/11폴더, 폴더 변경/누락, 정렬 내용 또는 revision 변경, 같은 작품의 추가 원고, 검사/적용 사이 snapshot 변경. 불일치에서 파일/metadata/baseline 변경 전 중단·데이터 쓰기 RPC 0·기존 큐 보존을 확인한다. 기존 151개 전체를 단순 반복할 필요는 없다.
4. 동시에 최종 `LocalDocumentStore.swift` 완전 소스를 공유한다. 원시 DB/계정/프로필/전체 백업은 필요 없다. 현재 검토 후보 기준 expected SHA는 `2985c12d3108cf5947624fbbdc45b584aeeabb494d7efe6761f1e675b39928ee`; 추가 수정이 있으면 새 manifest에 명시한다.

코드가 바뀌면 새 source/candidate hash·해당 검사 결과를 함께 회신한다. 코드가 이미 조건을 충족하면 불필요한 재빌드를 요구하지 않는다. 이번 문서는 설치/송신/서버 변경 승인이 아니다.

## 실행 절차에서 수정할 점

현재 iPad 시험 화면은 화면 이탈 또는 비활성화 때 `cancel()`로 ticket과 UI received 상태를 없앤다. 따라서 이전 Windows 제안처럼 **수신 직후 설정을 나가 원고를 오프라인 관찰한 뒤 돌아와 곧바로 송신**하는 순서는 현 후보와 맞지 않는다. 이를 위해 기존 수신을 반복하는 절차를 추가하지 않는다.

설치/실행안을 확정할 때는 현재 보호 흐름을 유지해 iPad 시험 화면 안에서 revision 2 수신 결과와 저장된 bytes/hash를 먼저 확인하고, 같은 foreground 흐름에서 별도로 승인 범위에 포함된 송신 버튼을 누르도록 잡는다. 사용자 오프라인 본문 관찰은 송신/lease 정리가 끝난 뒤 최종 5줄에서 한다. revision 2의 사용자 직접 오프라인 본문 관찰까지 반드시 요구한다면 그에 맞는 재개 UI 설계가 별도로 필요하므로 현재 후보에서 가능하다고 쓰지 않는다.

## 다음 순서와 사용자 행동

1. **아이패드측:** 위 snapshot 적용 전 보존 검사와 완전 소스 한 파일만 답변/보완한다. 이번 응답 ZIP을 전달하면 되며 과거 광범위 구현 요청서는 다시 보내지 않는다.
2. **윈도우측:** 이번에 맞춘 127/158 bytes 계획을 사용해 전역 송신 기본 잠금, 지정 합성 본문의 전용 저장/큐/송신/수신 동작, SDK 최종 HTTP 제한을 실제 제품 경로에 연결하고 격리 검사·실행 후보 빌드를 진행해야 한다. 아직 완료되지 않았다. iPad 자료 보완과 독립적인 연결 작업은 병행 가능하다.
3. **윈도우측:** 좁은 iPad 회신과 양쪽 최종 후보를 대조해 남은 조건을 닫는다. 새 서명 만료/설치 시점도 확인한다. 현재 보고 프로필은 `2026-09-12 18:19:25 KST` 만료이며 자동 갱신하지 않는다.
4. **사용자:** 정확한 후보·백업·설치·쓰기 한도·수신/관찰·종료를 묶은 실제 실행안을 승인한다. 같은 범위의 Windows 송신과 iPad 송신 버튼마다 재승인을 추가하지 않는다.
5. **양쪽:** 승인 후에만 새 일관된 백업 → 지정 후보 설치 → Windows revision 2 송신/lease 종료 → iPad revision 2 수신 및 revision 3 송신/lease 종료 → Windows revision 3 수신 → 오프라인 관찰/보존 감사/잠금·종료를 진행한다.

지금 양쪽 앱에서 누를 버튼은 없다. 기기 종료·오프라인을 유지한다. 오류/응답 불명에서는 다음 방향을 시작하지 않고 기존 operation과 서버 영수증/실제 expiry부터 확인한다. 일반 ID_BASED 작품과 기존 binding/hold, 최초가져오기 작품은 그대로 보존하며 prod 전환은 하지 않는다.
