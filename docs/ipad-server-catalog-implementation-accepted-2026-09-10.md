# iPad 서버 작품 목록·수신 가져오기 구현 검토 수용 — 2026-09-10

## 판정

이번 iPad 서버 작품 목록·수신 전용 가져오기는 **제출 소스와 격리 검증 근거 범위에서 수용**한다. 검토한 변경에서 추가 코딩을 요구할 구체적 결함은 확인하지 못했다. 실제 iPad 설치·화면·RLS 데이터 조회·가져오기 및 일반 양방향 송신 완료 판정은 아니다.

입력 ZIP은 `ipad-server-project-catalog-implementation-reply-20260910.zip`, SHA-256 `fc34ae719f572e56b6b21b54823c200218922ddeb8409ce1ffda83ede64b8c0c`이다. CRC, manifest 32개 파일의 크기·해시, 여분 파일 부재, 경로·중복·링크 검사 통과. 이전 수용 감사 및 Windows 발신 해시와 연결되는 회신이다.

기준 iPad HEAD는 `1107b820cd21cc8784a9cc34e4b04f8189fedb19`이며 패치는 구현 직전 미커밋 작업 트리 기준이다. 수정 8개·신규 3개 파일의 최종 해시, 재사용 소스 14개의 해시를 확인했다. 이전 감사에 중복되는 수정 파일 4개의 변경 전 해시도 일치한다. 격리 사본에서 역패치 후 변경 전 해시, 정방향 적용 후 변경 후 해시를 대조했다. Windows Git이 사본에 만든 CRLF만 원래 LF로 정규화했으며 수신 원본 파일은 그대로 보존했다.

## 완료 조건 대조

| 조건 | 확인한 소스와 근거 | 판정 |
|---|---|---|
| 서버 작품 목록 | `ServerProjectCatalog.swift`의 SELECT 전용 transport, UUID keyset pagination·중복/역행 거절, 선택 UUID 재조회 | 소스 확인 |
| 동명 작품·인증 변화 | UUID별 상태 분류, 계정/endpoint/인증 epoch 검증, UI 취소·세대 검사 | 소스·제출 검사 확인 |
| 수신 UUID 보존 | 최초 journal에 서버 UUID·계정·endpoint·transaction 기록. 이번 신규 수신에만 local=server 정책. 표준 폴더는 빈 목록으로 생성 | 소스 확인 |
| 기존 iPad 연결 보존 | 기존 수동 local/server 두 필드 연결을 소급 수정하지 않음. 기존 Windows 파일 복사 기능과 서버 가져오기 UI 구분 | 소스 확인 |
| 수신 전용 연결 | 일반 ensure/name-change/initial-upload transport를 수신 서비스에 주입하지 않음. AppEnvironment의 전용 puller에 lease/legacy 이관 송신 의존성 제외 | 소스 확인 |
| 완료 전 공개 방지 | journal 보존 중 작품 목록·currentBinding·connectedBindings에서 제외. snapshot·큐·binding·인증 검사 후 journal 제거로 공개 | 소스 확인 |
| 실패·취소·재개 | 소유 표식·symlink 검사, journal/UUID 재사용, 기존 작품·원고·큐 보존 | 소스·제출 검사 확인 |
| 실제 snapshot 적용 | 합성 폴더 10개·문서 1개·정렬표 11개, 문서 revision 7·정렬표 revision 4, 기존 자료·큐 불변 검사 | 제출 통합 검사 확인 |

## 검증 근거의 범위

`tests.json`의 30개 서버 목록/수신 검사와 27개 작품 관리 회귀, 합계 57개 이름이 중복 없이 모두 Passed이고 실패·skip 0임을 확인했다. `tests.log`의 57개 Passed 항목과 이름이 일치하며 첨부 Swift 검사 소스에 해당 메서드가 있다. 이 Windows 호스트에서 Swift/XCTest를 재실행하거나 실제 xcresult 디렉터리를 직접 조회한 결과는 아니다.

시험은 합성 transport·URLProtocol 가로채기라고 보고됐다. 별도 Staging 시스템 카탈로그 권한 조회는 실제 네트워크를 사용한 iPad 측 보고이며, 시험의 실서비스 데이터 API/RPC 호출과 구분한다. Windows 검토에서는 서버에 접속하지 않았다.

기존 Windows UUID 수정 100개와 과거 iPad binding 22개는 반복 실행하지 않았다. 기존 설치 앱의 22:52:18 KST 준비 성공은 보존하고, 아직 설치하지 않은 최신 소스의 runtime 성공 근거로 사용하지 않는다.

## 다음 단계: 양쪽 후보 빌드·설치 준비

1. Windows는 수용된 UUID 재발 방지 수정 및 앞서 완료한 변경을 포함한 소스를 고정해 새 EXE 후보를 만든다. 빌드 입력·포함 소스·해시·시작 검사와 기존 설치본의 차이를 확인한다. 현재 설치 앱·원본 DB는 이 준비에서 교체하지 않는다.
2. iPad는 이번 11개 변경뿐 아니라 보존 중인 기존 작업을 포함한 정확한 소스를 기준으로 후보 빌드를 식별한다. 커밋만으로 미커밋 작업이 포함됐다고 판단하지 않는다. 빌드/버전과 포함 소스 근거, 설치·복구 절차를 준비한다. 현재 기기 데이터나 전역 동기화는 변경하지 않는다.
3. 구체적인 양쪽 설치 후보와 보존·복구 범위를 사용자에게 제시한 후 해당 후보 설치 승인을 받는다. 이전 후보 설치 승인을 이번 새 후보의 포괄 승인으로 해석하지 않는다.
4. 설치 후 서버 목록·수신 검증은 승인된 계정과 합성 대상에서 진행한다. 기존 `일반동기화 검증 20260910`은 iPad에 이미 연결돼 있어 미가져온 작품의 최초 가져오기 시험과 구분한다. 새 시험을 위해 기존 연결을 지우거나 UUID를 덮어쓰지 않는다. 사용 가능한 미가져온 합성 작품이 있는지 먼저 확인하고, 새 서버 작품 생성이 필요하다면 그 쓰기 범위를 별도로 제시한다.
5. 이후 대상 작품만으로 Windows→iPad, iPad→Windows 일반 송신을 한 방향씩 확인한다. 관문·보류 해제·실제 송신은 준비 또는 설치에 암묵적으로 포함하지 않는다. iPad의 기존 전역 대기 4건·충돌 3건을 일괄 처리하지 않는다.

현재 사용자 앱 조작이나 수용 확인만을 위한 회신 ZIP 왕복은 필요 없다. 다음 사용자 행동은 후보가 준비된 뒤 설치 범위를 확인하고 양쪽 앱 저장·종료를 안내에 따라 수행하는 것이다. 실제 서버 목록·화면·중단 재개와 일반 양방향 실사용 검증은 남아 있으며 prod 전환은 그 뒤 단계다.

## 검토 산출물

`_evidence/ipad-server-project-catalog-review-20260910/input-verification.json`

`_evidence/ipad-server-project-catalog-review-20260910/source-and-tests-verification.json`

동일 경로 `received/`에 받은 소스와 보고서를, `patch-roundtrip/`에 소스 패치 대조용 사본을 보관한다. 수신 실행 스크립트는 실행하지 않았으며 Windows 제품 코드·설치 앱·실제 DB·서버는 변경하지 않았다.
