# WriterPad Staging 계약 전송 사전 점검

2026-09-06 KST. 서버 상태 조회 시각: 11:44–11:45 KST. 이후 함수 권한·인덱스와 Windows 관문 집계를 추가 확인했다.

## 판정

**합의한 첫 시험(새 폴더 1개 + 부모 tree_order 1개)에 필요한 서버의 인증·권한·작품 활성 상태·revision·원자적 적용·재전송 처리 구현을 배포 정의에서 확인했다. 현재 이 범위를 준비하기 위해 서버 설정이나 계약을 변경해야 할 근거는 발견하지 않았다.**

다음 단계는 iPad 측에서 시험용 작품과 전송할 1배치의 사전 명세를 확정하는 것이다. 실제 관문 개방과 전송은 아직 하지 않는다. 이번 결과는 읽기 전용 사전 점검이며, 실제 계약 RPC의 성공·실패·동시성·멱등성 실행 시험을 대신하지 않는다.

## 1. 확인한 환경과 버전

| 항목 | 결과 |
|---|---|
| 서버 | WriterPad Staging, `mhpnszcorfzrvhyondxr`, ACTIVE_HEALTHY, Singapore |
| Postgres | 17, 서버 보고 버전 `17.6.1.155` |
| Windows | `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`, 커밋·푸시·설치 완료. 자동저장·일반 동기화는 사용자 확인 완료 |
| iPad | `56fba08726974a0f1d645b6063e43d5b8a30943e`, 이전 대조에서 소스·푸시·설치 기록 확인 |
| 계약 | `0.2.0`, protocol `3` |
| digest | `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670` |
| 0.2.0 allowlist | **이미 enabled=true**, revoked_at=null, valid_from 경과, 허용 protocol `[3]` |
| 0.3.0 allowlist | enabled=false. 이번 범위에서 사용하지 않음 |
| 서버 작품 mode/epoch | 현재 존재하는 11개 모두 LEGACY/0, 설정 행 부재도 서버 정의상 LEGACY/0 |
| 현재 0.2.0 배치 기록 | `sync_batches` 집계 0건. 과거 시험의 유무를 뜻하지 않음 |
| Windows 로컬 관문 | 기본 프로필 15개 작품 중 열린 관문 0개, SQLite 읽기 전용 집계 |

Windows 빌드에 사용한 release 설정의 URL은 위 Staging과 일치했다. 키 값은 출력하지 않았다. iPad의 관문 0개는 앞서 받은 설치 기록의 값이며 이번 Windows 작업에서 기기 값을 다시 읽지는 않았다.

서버 allowlist와 기기별 계약 관문은 별개다. 서버의 허용 설정은 이번 점검 이전부터 켜져 있었으며 이번에 바꾸지 않았다. LEGACY/0에서도 이 계약을 허용하는 서버 경로가 있으므로 첫 시험을 위해 mode/epoch를 변경할 이유는 없다. `minimum_client_builds`는 빈 객체이며, 배포 검증 함수는 client_build_id가 비어 있지 않은지 검사한다. 시험 기록에는 실제 커밋·바이너리 해시·client_build_id를 함께 남긴다.

## 2. 배포 함수에서 확인한 안전 조건

### 인증·권한·작품 활성 상태

- `public.atomic_structure_commit`은 요청자가 보낸 사용자 값이 아니라 `auth.uid()`를 사용한다. 인증이 없으면 쓰기 구현을 호출하기 전에 AUTH_REQUIRED 응답을 반환한다.
- 내부 `atomic_structure_commit_legacy`와 `private.validate_contract_request`는 실제 소유자/멤버의 editor 이상 권한을 검사한다.
- `private.has_project_role`은 해당 작품이 존재하고 `trashed_at is null`이어야 허용한다. 삭제된 작품과 영구 삭제로 사라진 작품은 계약 쓰기 권한 검사를 통과하지 못한다. 이 계약 경로에서는 FORBIDDEN으로 나타날 수 있으며, 일반 get_project_status의 trashed/purged 표시와 오류 이름이 반드시 같지는 않다.
- 공개 wrapper는 anon에게도 EXECUTE가 있지만 첫 인증 검사에서 거절한다. 내부 `_legacy`, `apply_structure_intent`, `validate_contract_request`는 anon/authenticated가 직접 실행할 수 없다. 관련 SECURITY DEFINER 함수의 search_path는 빈 값으로 고정됐다.
- 점검한 작품·멤버·구조·계약 원장 표는 authenticated의 직접 INSERT/UPDATE/DELETE가 차단돼 있다. 공개 관련 표에는 RLS와 FORCE RLS가 설정돼 있다. private allowlist는 직접 접근 권한을 차단한다.

관리자 연결에서 함수 정의와 권한을 읽은 결과다. 선택할 시험 작품에 대해 실제 iPad 로그인 계정으로 상태/핸드셰이크를 재조회하는 단계는 전송 사전 명세에 포함해야 한다. 관리자 조회 성공을 사용자 권한 시험 성공으로 간주하지 않는다.

### 다른 요청과 겹치는 경우

계약 구조 전송, 계약 원고 전송, 일반 commit_folder/commit_document, 작품 trash/restore/purge 및 mode 전환 RPC에서 같은 `hashtextextended('project:' || project_id, 0)` 기반 트랜잭션 잠금을 확인했다. 계약 경로는 잠금을 얻은 뒤 권한을 확인한다.

이 배포 RPC 경로들에서는 삭제가 먼저 완료되면 뒤의 계약 쓰기가 거절되고, 계약 쓰기가 먼저 잠금을 얻어 완료됐다면 뒤의 삭제가 그 이후에 실행되는 구조다. 이미 적용된 쓰기를 관문을 닫는 것만으로 취소했다고 해석하지 않는다. 이 판정은 함수 정의와 PostgreSQL의 트랜잭션 advisory lock 동작에 근거하며, 관리자 직접 SQL까지 같은 잠금을 강제한다는 뜻은 아니다. [PostgreSQL 잠금 설명](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS).

### revision과 부분 적용 방지

- 새 폴더는 base_revision=0이며 ID가 이미 있으면 거절한다. 기존 폴더와 tree_order는 저장된 revision과 일치해야 한다.
- 폴더 부모의 작품·생존 상태, 이름 충돌, 순환 관계를 검사한다. tree_order는 참조 중복과 같은 작품의 생존 엔티티 해석을 검사한다.
- 부모별 tree_order와 작품 최상위 tree_order에는 각각 유일 인덱스가 있다.
- 순서대로 적용하는 루프가 하나의 예외 블록에 있다. 뒤의 작업이 P0001 오류를 일으키면 해당 블록의 앞선 구조 변경도 롤백하고, applied=false·빈 results의 실패 응답 및 진단 원장을 남긴다. 처리되지 않은 SQL 오류는 호출 트랜잭션 전체 실패로 전파된다. 따라서 ‘구조가 적용되지 않음’과 ‘실패 진단 기록도 전혀 없음’은 다르다. [PostgreSQL 예외 처리 설명](https://www.postgresql.org/docs/17/plpgsql-control-structures.html#PLPGSQL-ERROR-TRAPPING).

### 동일 요청 재전송

- batch_id 기본키, operation_id 기본키, (batch_id, sequence) 유일 제약을 확인했다.
- 기존 batch_id가 있으면 작품·사용자·기기·빌드·계약·mode/epoch·요청 전체 해시 등을 비교한다. 다른 요청이면 BATCH_ID_REUSED, 이미 사용된 operation_id는 OPERATION_ID_REUSED로 거절한다.
- 같은 요청의 성공 결과가 있으면 구조를 다시 적용하지 않고 저장된 영수증을 replayed로 반환한다.
- 이미 원장에 저장된 실패 결과도 그대로 반환한다. **서버가 영구적인 거절 결과를 저장한 배치와, 아직 전송을 시작하지 못해 로컬에서 차단된 배치를 구분해야 한다.** revision/payload를 고친 뒤 옛 batch_id를 재사용하지 않는다.
- 기존 결과 반환은 현재 allowlist/mode 재검증보다 앞에 있지만 현재 작품 권한은 먼저 확인한다. 이는 과거 결과 반환이며 새 구조 쓰기가 아니다.
- 배치·작업·결과 원장에는 UPDATE/DELETE 거절 트리거가 있다. 작품 영구 삭제의 명시적 정리 경로는 예외다.

실제 RPC는 호출하지 않았다. 재전송 시 서버 revision과 원장 건수가 증가하지 않는 실행 증거는 실제 시험 단계에서 별도로 남긴다.

## 3. 첫 배치를 만들기 전에 필요한 정보

이전 검증 작품 `최종검증02`, `최종검증03`은 모두 active, LEGACY/0이고, 현재 계약용 최상위 `tree_orders` 행은 각각 0개다. 이 두 작품 중 하나가 최종 시험 대상으로 승인됐다는 뜻은 아니다.

기존 작품을 사용한다면 최상위 순서를 revision=1로 가정하지 않는다. 실제 부재를 재확인한 뒤 새 tree_order의 base_revision=0과 새 ID를 사용해야 한다. 이미 로컬에 다른 기준이 있다면 먼저 일치 여부를 확인한다. 기존 최상위 항목 목록을 누락하거나 새 폴더만 넣어 덮어쓰는 배치를 만들지 않는다. 비어 있는 별도 시험 작품을 택해도 된다.

iPad 측 사전 명세에 필요한 항목:

1. 시험용 작품 1개, 양쪽 연결 일치, 실제 사용자 권한 및 새 active/handshake 판정.
2. iPad `56fba08`과 Windows `e5ff6e0`의 설치 식별. 서버·계약·mode/epoch는 위 값 유지.
3. 새 폴더 ID·부모 ID·충돌 없는 시험 이름·base_revision=0. 원고 본문은 포함하지 않음.
4. 부모 tree_order ID·현재 존재 여부·실제 base_revision·기존 자식 집합 및 추가할 폴더. 이름과 원고 본문 대신 필요한 구조 식별 정보만 공유 가능.
5. 정확히 2개 ordered_intents, batch_id·operation IDs·payload hashes·batch digest·기기 ID·client_build_id. 검토 후에는 임의로 바꾸지 않음.
6. 구조 기준 승인, 미해소 blocked/conflict 없음, 기존 계약 대기 배치와 시험 배치 구분. Windows는 관문을 닫고 같은 작품을 동시에 편집하지 않음.
7. 성공·거절·응답 유실별 확인 방법. 성공 후 관문을 닫고 서버에서 batch/result/revision을 읽어 대조. 응답 유실 시 동일 요청의 재시도·결과 조회 범위를 별도 승인 범위에 명시. 저장된 거절은 반복 고속 재시도하지 않음.

배치를 준비한다는 이유로 실제 관문을 켜거나 서버에 임시 쓰기를 하지 않는다. iPad가 닫힌 관문 상태에서 사전 명세를 작성하고, 실제 수동 송신은 다음 단계의 별도 승인 후 진행한다. 폴더 이동·삭제·복원·원고 계약 전송으로 범위를 넓히지 않는다.

## 4. 보안 진단의 해석

Supabase security advisor도 읽었다. 계약 wrapper의 anon/authenticated EXECUTE 경고는 위 인증·권한 처리와 함께 해석했다. 선언만으로 무단 쓰기가 가능하다고 판정하지 않았으며 권한을 바꾸지 않았다. SECURITY DEFINER는 함수 자체의 권한 검사가 중요하다. [Supabase 설명](https://supabase.com/docs/guides/observability/advisors?queryGroups=lint&lint=0029_authenticated_security_definer_function_executable).

그 밖에 기존 trigger 함수 2개의 mutable search_path, 유출 비밀번호 보호 비활성 경고가 있다. 이번 첫 폴더+순서 RPC 경로의 선행 수정으로 단정하지 않고 별도 유지보수 항목으로 남긴다. [search_path 진단](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable), [비밀번호 보호 설명](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection). RLS policy가 없는 edit_leases와 private purge 표의 INFO도 기록했다. 이번 점검에서 어떤 보안 설정도 변경하지 않았다.

## 5. 증거와 한계

`_evidence/server-contract-preflight-20260906/`에 배포 함수 정의 18개, 권한·제약·인덱스·설정 집계 `inventory.json`, Windows 관문 집계 및 파일 해시 목록을 보관한다. SQL 카탈로그·설정·선별 메타데이터 SELECT와 advisor 조회만 사용했다. 비밀키·토큰·원고 본문·sync_operations.payload·저장된 응답 본문은 조회하지 않았다.

실제 사용자 인증 RPC 호출, 계약 쓰기, rollback을 전제로 한 쓰기 시험, 배치 생성, 서버 설정·allowlist·mode/epoch·관문 변경은 하지 않았다. prod에는 SQL 조회를 실행하지 않았다. Supabase changelog Markdown은 도구의 content-type 제한으로 읽지 못했고, 관련 공식 권한 문서와 실제 배포 정의를 근거로 검토했다.

## 지금 사용자가 할 일

**이 문서를 iPad 측에 전달해 3절의 ‘시험용 작품과 1배치 사전 명세’를 작성해 달라고 요청한다.** Windows 서버 사전 점검은 완료됐으며 추가 앱 조작이나 일반 복구 반복 시험은 필요 없다. iPad 측 사전 명세가 오면 Windows에서 마지막 대조를 하고 실제 전송 단계의 범위를 확정한다. 현재 실제 관문은 계속 닫아 둔다.
