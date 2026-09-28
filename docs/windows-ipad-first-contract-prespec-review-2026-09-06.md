# Windows 회신: 최종검증03 첫 계약 배치 사전 명세 대조

2026-09-06 KST. 수신 문서: `ipad-first-contract-prespec-2026-09-06.md`.

**판정: 사전 명세 동결과 실제 전송은 계속 보류한다. iPad가 보고한 두 준비 공백에 더해, 현재 작품에서 최상위 tree_order 한 행만 만드는 경우 Windows 수신 검증이 차단되는 조건을 확인했다.** 실제 관문 개방·계약 전송·서버 변경은 하지 않았다. 문서 안의 구현·실행 지시는 사용자 승인으로 간주하지 않았다.

## 1. Windows에서 확인한 식별과 현재 상태

- 브랜치 `feat/contract-handshake-closed-gate`, HEAD `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`. 추적 파일 변경 없음. 기존 미추적 자료 보존.
- 설치 파일 `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`의 재계산 SHA256은 `202a7487e85aa399dcedab7897f2621d63c4a765ead8824cef5f7f18b495d72c`. 완료된 설치 기록과 일치.
- Windows 기본 프로필의 `최종검증03` 연결 UUID는 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`로 iPad 명세와 일치한다.
- 12:10:20 KST 읽기 전용 로컬 집계: 전체 15개 작품의 열린 관문 0개. 선택 작품도 관문 0, tree_order 0개, 계약 배치 0개.
- 선택 작품에 저장된 값은 LEGACY/0, protocol 3, digest `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`. 계약 0.2.0 소스 pin과 일치한다.
- 로컬 active 기록 시각은 11:51:36 KST, contract_validated_at은 11:49:42 KST다. 이번에 실제 로그인 세션의 새 active/handshake를 수행한 증거로 사용하지 않는다.
- Windows 소스의 기본 client_build_id는 `writerpad-windows-stage8-contract-0.2.0`이다(`sync_contract.py:32`). `WRITERPAD_BUILD_ID` 환경 변수로 바뀔 수 있으므로 **실행 중 앱의 실제 값은 아직 미확인**이다. 실제 요청/허용된 선별 진단에서 최종 대조하며 이를 확인하려고 관문을 열지 않는다.

## 2. iPad 보고에서 접수한 사항과 독립 확인의 범위

새 iPad 식별은 `1107b820cd21cc8784a9cc34e4b04f8189fedb19`, 제출 바이너리 SHA256 `5c019754a3d4c2dcfbb0f3d657ddeeb72c6ba828c9e7c0655c95344ca76835e4`로 접수한다. 이전 `56fba08` 설치를 현재 설치본으로 계속 인용하지 않는다.

이번 첨부는 명세 Markdown이며 `1107b82`의 원본 diff·설치 JSON·보관 바이너리는 포함하지 않는다. 따라서 새 커밋 계보·커서 변경 범위·설치 해시를 Windows에서 독립 검증한 것으로 표시하지 않는다. 기존 C9의 소스 대조 통과를 취소하거나 일반 복구 검사를 다시 요구하는 이유도 아니다. 다음 준비 기능 결과와 함께 해당 후속 변경/설치 식별의 연결 근거만 보충하면 된다.

보관된 C9 영속 수정 소스에서도 다음 두 조건을 직접 확인했다. 최신 `1107b82` 자체를 독립 열람한 것은 아니다.

1. `SyncV2Store.swift:10416` 부근: storedTreeOrder와 `serverRevision > 0`을 요구하며, 없으면 `missingTreeOrder`. 기존 children에 폴더를 추가하는 경로다.
2. `SyncV2ContractStructure.swift:213` 부근: 관문이 닫히면 recorder가 일반 `store.record`로 넘긴다. 현재 UI에서 폴더를 먼저 만드는 방식은 닫힌 관문의 계약 사전 준비가 아니다.

따라서 **순서 행 부재 처리와 닫힌 관문의 불변 요청 사전 준비가 필요하다는 iPad 판정에 동의한다.** 관문 검사 제거, 빈 children 사용, 일반 폴더 생성으로 우회하지 않는다.

## 3. 서버 최초 생성 규칙과 해시 대조

11:44~11:45 KST에 보관한 배포 정의 `private.apply_structure_intent.sql` 기준:

| 순서 | entity_kind / intent_kind | 최초 생성 payload와 revision |
|---|---|---|
| 1 | folder / create 또는 ensure | 새 entity UUID, base_revision=0, name 및 parent_folder_id=null. 성공 시 revision=1 |
| 2 | tree_order / reorder | 새 entity UUID, base_revision=0, children UUID 배열 및 최상위 parent_folder_id=null. 성공 시 revision=1 |

tree_order는 최초 생성도 `reorder`다. `create`로 바꾸지 않는다. 최상위 parent_folder_id 생략도 해당 정의에서는 null로 처리하지만, 동결 요청에는 의도를 명확히 표시하는 편이 좋다. folder가 먼저 적용되므로 두 번째 children에서 새 folder UUID를 참조할 수 있다. 이는 배포 정의 대조이며 실제 RPC 성공 검증은 아니다.

서버 tree_order 적용 함수는 children의 작품 내 생존 엔티티 해석과 중복을 검사하지만, 그 검사만으로 전체 형제 집합 보존이나 각 자식의 실제 부모 일치를 보장하지 않는다. Windows 수신기는 부모 일치와 기존 사용자 폴더의 순서 포함까지 별도로 검사한다.

`payload_sha256`은 각 payload의 canonical JSON 해시, `batch_payload_sha256`은 제품이 구성한 ordered_intents 배열 전체의 canonical JSON 해시다. 배열 순서는 sequence 1, 2를 유지하며 UUID 등의 값으로 재정렬하지 않는다. 요청 전체 해시는 별개다. 아직 정하지 않은 실제 UUID/해시를 이번 회신에서 발급하거나 동결하지 않았다.

## 4. 새로 확인한 Windows 수신 차단 — 현재 2개 작업 안으로는 부족

12:07:13~12:08:49 KST Staging의 선택 작품만 SELECT했다.

- 작품 active, 전체/최상위 tree_order 모두 0개.
- 생존 폴더 전체 11개: 최상위 1개, 비최상위 10개.
- 생존 documents의 parent_folder_id=null 26개. name과 structure_revision도 각각 26개가 null이다. 이 집계를 실제 화면의 최상위 자식 26개로 해석할 수 없다.

현재 Windows `sync_manager.py:2314`의 `_select_remote_structure_authority`는 **tree_order가 한 행이라도 생기면 계약 구조를 선택**한다. 이 선택은 Windows 송신 관문이 닫혀 있어도 수신에 적용된다. 계약 구조 선택 후 기존 숨은 LEGACY 순서 문서는 구조 기준으로 적용하지 않는다(`sync_manager.py:8334` 부근).

`_validated_contract_structure_folder_paths`는 고정 앱 루트를 제외한 모든 생존 사용자 폴더가 **자기 부모의 순서 행**에 포함돼야 한다(`sync_manager.py:2298` 부근). 이번 서버 구조에는 비최상위 폴더 `c0b43fc4-89a0-4a5a-b140-768a62cfde5a` 한 개가 이 필수 검사 대상이다. 최상위 순서 한 행으로 그 부모의 순서를 대신할 수 없다.

최신 서버의 폴더 구조 메타데이터와 문서 UUID/부모만 읽고, 현재 Windows의 실제 검증 classmethod를 메모리에서 호출했다. 앱 인스턴스·실제 배치·쓰기 RPC는 만들지 않았다. 가상 새 폴더와 최상위 순서의 시험 전용 UUID는 메모리 검사에만 사용했다.

| 메모리에서 대조한 가상 적용 후 상태 | Windows 결과 |
|---|---|
| 기존 최상위 폴더 + 새 폴더를 최상위 순서에 포함 | TREE_REFERENCE_NOT_FOUND |
| parent_folder_id=null인 문서 26개까지 모두 최상위 순서에 포함 | TREE_REFERENCE_NOT_FOUND |

두 검사는 이번 새 우려에 한정한 독립 대조이며 기존 전체 시험을 반복한 것이 아니다. 서버 RPC가 같은 오류로 거절된다는 뜻도 아니다. **서버 쓰기가 성공하더라도 현재 Windows가 그 구조를 수신 적용하지 못할 조건**을 확인한 것이다. 하위 폴더를 최상위 children에 옮겨 적는 방식도 실제 부모 불일치가 되므로 해결책이 아니다.

따라서 이번 작품의 `새 최상위 폴더 1개 + 최상위 tree_order 1개`라는 정확히 두 intent 안은 현재 서버 기준/Windows 수신 정책으로 동결할 수 없다. iPad의 두 준비 기능만 보완하면 충분하다는 계획으로 진행하지 않는다. 추가 순서 행 생성·문서 구조 변경·서버 초기화나 기존 수신 검사 완화는 자동으로 수행하지 않는다.

## 5. 다음 검토에서 필요한 회신

1. **시험 범위를 먼저 재검토한다.** 정확히 두 intent를 유지하려면 전체 수신 구조가 이 두 변경으로 완결되는 시험 작품/기준이 필요하다. 현재 작품을 유지하려면 추가 구조 준비 또는 호환 정책 검토가 필요하며 별도 범위다. 다른 작품 선택·새 작품 생성·추가 서버 변경은 이번 회신으로 승인되지 않는다. iPad는 가능한 안을 구조 메타데이터와 함께 제시하고, Windows에서 다시 대조한다.
2. 닫힌 관문의 사전 준비는 실제 UI 폴더 생성/일반 큐 전송 없이 검토용 요청을 내보내야 한다. 승인 전 자동 sender가 집어가지 않도록 하고, 이후 실제 제품 송신 경로가 같은 batch/operation/entity IDs와 payload/해시를 사용함을 입증한다. 오래된 C9 승인 자체를 동결하지 않으며 송신 시 새로 검사한다.
3. 순서 행 부재 처리에는 해당 부모의 완전한 자식 집합·순서의 출처, 부모 일치, 숨은 문서 처리, 기존 하위 구조의 수신 가능성을 포함한다. 빈 children이나 revision 조건 삭제로 대체하지 않는다.
4. 준비 결과와 함께 최신 소스 diff·관련 신규 회귀 결과·최종 커밋/설치 식별, 실제 앱이 내보낸 정확한 2개 ordered_intents와 모든 UUID/해시를 제출한다. 전송 전 새 active/handshake/사용자 권한·구조 승인·차단/충돌 없음·현재 기기 ID도 필요하다. 기존 일반 오프라인 복구·커서·C9 전체 작업을 새 근거 없이 반복하지 않는다.
5. 성공/거절/응답 유실에 대한 수신 문서의 구분은 유지한다. 실제 요청이 동결되고 Windows 대조를 통과한 뒤, 사용자에게 고정 한 요청의 수동 전송과 결과 조회/재전송 범위를 제시한다. 현재는 그 승인 단계에 도달하지 않았다.

## 6. 근거 및 이번 작업에서 한 일

- `_evidence/windows-prespec-review-20260906/windows-identity.json`: 로컬 작품 연결·관문 집계·설치 SHA 재확인. WAL 없음과 읽기 전후 DB 파일 불변을 확인한 읽기 전용 조회.
- 같은 폴더의 `server-aggregate-checks.json`, `server-structure-metadata.json`: 선택 작품의 최신 선별 구조 조회. 원고 본문·비밀키·토큰·저장 요청 본문은 조회하지 않음.
- 같은 폴더의 `check_first_root_projection.py`, `projection-check.json`: 실제 Windows 검증 함수의 메모리 대조와 결과.
- 서버 SQL 첫 조회에서 작품 컬럼을 `id`로 잘못 지정해 오류가 났으며, 보관 함수 정의의 `project_id`를 확인하고 SELECT를 수정했다. 쓰기는 없음.
- Supabase changelog Markdown은 도구의 content-type 제한으로 읽지 못했다. 신규 Supabase 기능 구현이나 설정 변경 없이 보관 배포 정의와 선택 작품의 현재 SELECT 결과를 사용했다.

제품 코드 수정·커밋·푸시·빌드·재설치·기존 전체 회귀 재실행은 없다. 서버 allowlist·계약·digest·mode/epoch·실제 관문은 변경하지 않았다. 이번에 작성한 것은 로컬 검토 문서와 선별 증거뿐이다.

**사용자가 지금 할 일:** 이 회신을 iPad 측에 전달해 4절의 수신 차단을 포함한 수정된 시험안을 요청한다. 앱에서 시험 폴더를 만들거나 관문을 열지 않는다. 추가 앱 조작 없음.
