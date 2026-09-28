# Windows 모의 검증 회신: 최종검증03 빈 2권 + 원고 순서

2026-09-06 KST. 수신 ZIP: `ipad-revised-first-contract-plan-20260906.zip`.

**판정: 현재 기준에서 Windows 수신 모의 검증 통과. `원고` 아래 빈 `2권` 폴더 한 개와 같은 부모의 순서 `[기존 1권, 새 2권]` 한 행을 수신할 수 있었다. 이전 최상위 순서안의 TREE_REFERENCE_NOT_FOUND는 이 수정안에서 발생하지 않았다.**

이는 실제 서버 계약 전송·iPad 배치 준비 기능·설치 앱의 실통신 성공을 뜻하지 않는다. 실제 관문은 계속 닫혀 있으며, 제품 코드 수정 없이 현재 Windows 코드로 확인했다.

## 대조한 정확한 범위

- Windows 브랜치 `feat/contract-handshake-closed-gate`, HEAD `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`.
- 작품 `최종검증03`, UUID `1bd47431-0773-482c-8eb5-ac9e2952b6f4`.
- 부모 `원고`: `14df4a55-b7cd-4790-bc53-f84148418c0f`.
- 기존 `1권`: `c0b43fc4-89a0-4a5a-b140-768a62cfde5a`.
- 가상 변경 1: folder/create, name=`2권`, 위 원고를 부모로 사용, base_revision=0.
- 가상 변경 2: tree_order/reorder, 같은 원고를 부모로 사용, children=`[기존 1권, 가상 2권]`, base_revision=0.
- 수신 입력은 위 변경이 성공해 두 신규 행이 revision=1로 나타난다고 가정한 서버 snapshot이다. 최상위 순서나 다른 부모 순서, 문서 변경은 추가하지 않았다.

새 폴더와 순서 행에는 격리 시험 전용 상수 UUID를 사용했다. 실제 송신용 entity/batch/operation ID나 payload 해시는 발급·동결하지 않았다. 첨부의 기호를 유효한 RPC 요청으로 해석하지 않았다.

## 입력 근거와 격리 방법

ZIP manifest의 11개 파일 모두 바이트 수와 SHA256이 일치했다. ZIP 자체 SHA256은 `8c81402dfbe2093bd83bed81da605b9f6e385155f58eabdbf0efa17a1aa83dda`다. 첨부 패치나 명령을 적용·실행하지 않았다.

Windows 실제 DB에서는 선택 작품의 폴더 UUID·부모·이름·revision, 문서 UUID·경로·revision·구조 메타데이터만 읽었다. 생존 폴더 11개의 UUID/부모/revision이 iPad의 12:30:48 KST `server-folders.json`과 일치했다. 일반 문서 25개는 모두 `메인/원고/1권/` 아래였고, 별도로 숨은 순서 문서 1개가 있었다. 로컬 tree_order는 0개였다. 실제 설정 파일에서는 `tree_order` 필드만 선별했다.

실제 원고 본문은 읽지 않았다. 같은 UUID·경로·revision을 가진 임시 문서 25개를 만들고 본문은 서로 다른 합성 문자열로 채웠다. 실제 DB/작품의 사본을 통째로 복제하지 않았다. 실제 서버 조회나 RPC도 이번 검증에서는 수행하지 않았다.

격리 위치는 저장소 `_evidence/windows-revised-plan-simulation-20260906/isolated-receive-*` 아래다. 시험 프로세스의 앱 데이터·프로필·프로젝트 루트를 그 안으로 분리하고, 서버 초기화와 네트워크 연결을 차단했다. SQLite와 identity 파일, 일반 문서 파일, 설정 저장은 모두 이 임시 작품에서만 수행했다. 시험 중에도 계약 관문을 열지 않았다.

## 실제 실행한 Windows 코드 경로

1. `V2PullWorker.run`: 원격 조회만 대역으로 공급하고 실제 구조 기준 선택을 실행했다.
2. `_select_remote_structure_authority` → `_validated_contract_structure_folder_paths`: 계약 기준 선택, 부모 일치, 기존 사용자 폴더 포함을 검사했다.
3. `_apply_v2_remote_documents`: 실제 폴더 identity 처리, SQLite folder/tree_order snapshot 저장, 파일 적용, 계약 순서 저장을 실행했다. 이 함수나 내부 저장소·파일 적용 함수는 통과 대역으로 바꾸지 않았다.
4. `_identity_audit_is_clean` 및 다시 열기 `prepare_open`: 파일/identity 정합성을 확인했다.
5. 실제 `WritingProjectManager`, `WritingTreeMixin`, `BinderTreeWidget`: 화면을 띄우지 않는 Qt 환경에서 트리를 구성·확장하고 항목과 순서를 읽었다. 스크린샷이나 Computer Use는 사용하지 않았다.

첫 수신 뒤 같은 snapshot을 두 번 더 수신 적용했다. 이는 일반 수신에 쓰는 동일 적용 경로의 반복 검사이며, 실제 타이머·네트워크 재연결·비동기 UI 신호 전체를 재현한 검사는 아니다.

## 확인 결과

| 확인 항목 | 결과 |
|---|---|
| 계약 구조 기준 선택 | contract, 오류 없음 |
| 기존 1권과 새 2권의 부모/순서 | 둘 다 원고 아래, `[1권, 2권]` |
| 새 폴더 | `메인/원고/2권` 한 개만 추가 |
| 새 tree_order | 원고 부모 한 행, revision 1, children UUID 일치 |
| 기존 폴더 11개 | ID·부모·경로·이름·revision 보존 |
| 기존 25개 문서 | UUID·경로·revision·부모 메타데이터·합성 본문 바이트 보존 |
| 기존 identity 노드 | 모두 보존, 새 2권 노드만 추가 |
| 미명시 고정 폴더 | 트리 항목과 현재 표시 순서 보존 |
| 1권의 문서 표시 | 기존 25개 표시 순서와 계층 보존 |
| 2권 트리 표시 | 폴더로 표시, 펼치면 자식 0개 |
| 숨은 LEGACY 순서 | 일부러 높은 revision의 이전 순서를 줘도 계약 순서를 덮지 않음 |
| 동일 snapshot 재수신 2회 | 변경 목록 0, 파일·identity·문서/폴더 의미 상태 동일 |
| 다시 열기 | prepare_open=ok, 트리 동일, 2권은 계속 비어 있음 |
| 추가 25화 자동 생성 | 없음 |
| 일반 작업/구조 작업/계약 배치 큐 | 모두 0개 |
| 네트워크/송신 시도, 관문 개방 | 모두 0회 |

하나의 수정안에 대한 연속 시나리오 검사다. 수신 횟수를 기존 764개 회귀 결과에 합산하지 않으며, 기존 전체 회귀·커서·일반 복구 검사를 다시 실행하지 않았다.

## 순서 설정의 보존 범위

현재 제품은 계약 순서를 적용할 때 LEGACY의 `tree_order` 사전을 그대로 유지하지 않는다. 이번에는 실제 로컬 설정에 있던 `<root>`, `메인/원고/1권`, 비어 있는 기타 부모 순서 키들이 빠지고, 명시된 `메인/원고: [1권, 2권]`만 남았다. 이것을 설정 파일까지 바이트 단위로 보존됐다고 보고하지 않는다.

현재 선택 작품에서는 고정 루트의 기본 표시 순서와 원고의 권/화 자연 정렬이 기존 표시와 같아, **실제 입력 설정으로 검사한 화면 순서·폴더·문서의 누락이나 이동은 없었다.** 이 결과를 임의로 순서를 바꾼 다른 작품이나 다른 부모의 사용자 정렬 보존까지 일반화하지 않는다. 준비 중 구조·정렬이 바뀌면 동결 전에 해당 기준을 다시 대조해야 한다.

## 남은 단계와 이번 결과가 허용하는 판단

Windows의 이전 차단 때문에 이 수정 후보를 계속 보류할 근거는 이번 모의 검사에서 발견하지 않았다. iPad 측은 **빈 2권 + 원고 순서라는 정확히 두 작업**을 기준으로 최초 tree_order 처리와 닫힌 관문의 불변 배치 준비 설계를 구체화할 수 있다. 이번 검토는 그 기능을 대신 구현하거나 완료 처리한 것이 아니다.

실제 실행 전에는 최신 양쪽 구조·대기/차단 상태·로그인 권한·active/handshake, iPad 준비 기능의 신규 회귀, 최종 설치 식별과 실제 제품이 내보낸 불변 요청을 대조해야 한다. 현재 미확인인 Windows 실행 중 client_build_id도 그대로 미확인이다. 관문 개방·실제 송신·재전송은 별도 승인 범위이며 이번에는 수행하지 않는다.

설치 실행 파일 SHA256은 `202a7487e85aa399dcedab7897f2621d63c4a765ead8824cef5f7f18b495d72c`로 재확인했다. 종료 시 기본 프로필 15개 작품의 열린 관문은 0개, 선택 작품도 닫힘/LEGACY/0이었다. 추적 제품 파일 변경, 재빌드·설치·서버 변경은 없다.

## 재현 근거

- `_evidence/windows-revised-plan-simulation-20260906/simulate_receive.py`
- 같은 폴더의 `simulation-result.json`, `before-after.json`, `final-verification.json`
- `local-metadata.json`, `local-tree-order-metadata.json`, `archive-verification.json`
- `received/`의 수정 계획과 manifest

재현 명령: `python -B _evidence/windows-revised-plan-simulation-20260906/simulate_receive.py`.
명령은 보관된 선별 메타데이터로 별도 합성 작품을 새로 만들며 실제 DB를 열지 않는다. 최초 모의 실행의 검사 스크립트에서 저장소 API 반환 키를 잘못 참조한 부분을 바로잡았다. 제품 결함이나 제품 코드 수정은 아니며 최종 검사는 통과했다.

**사용자가 지금 할 일:** 이 문서를 iPad 측에 전달하면 된다. 실제 앱에서 2권을 만들거나 관문을 열 필요는 없다. 추가 앱 조작 없음.
