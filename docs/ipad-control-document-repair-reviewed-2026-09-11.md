# iPad 정렬 문서 교정 검토 및 Windows 적용안 — 2026-09-11

## 현재 판정

실제 첫 수신은 아직 미완료다. 이번 회신은 **기존 후보의 같은 journal 재개가 올바른 UUID만 받으면 성공한다는 합성 시험 결과**다. 실제 서버 교정이나 iPad 재개 승인이 아니다.

입력: `ipad-control-document-repair-local-review-reply-20260911.zip`
SHA-256: `37706877e77bbce051f03516784c70cb5f47664539b81cb40eb960c8509090d2`
manifest 18개 파일의 CRC·경로·중복·링크·정확한 구성·길이·해시 검증 통과.

## 회신 대조

- 보고된 XCTest 2건 통과: 올바른 내부 UUID만 제공하면 같은 journal로 완료하고, 잘못된 UUID의 tombstone도 남기면 다시 incompleteSnapshot이다.
- 기존 오류 marker는 성공 후에도 동일 바이트로 남는다. 삭제는 재개의 선행 조건이 아니다. 이번 적용안에서 marker/journal/owner 파일을 손대지 않는다.
- 전체 추가 테스트를 역패치해 동결 원본 해시와 일치함을 확인했다. 동결 manifest와 발췌 파일 해시를 대조하고, 확보된 완전 소스 5개에서 발췌 내용을 직접 대조했다. 한 발췌의 끝 줄 범위가 마지막 공백 줄을 포함하지만 본문 내용은 일치한다.
- 합성 원고·정렬 content·폴더 11개의 UUID/부모/이름은 Windows 실제 초기 업로드 계획과 일치한다.
- Windows에서는 XCTest를 재실행하지 않았다. iPad 시험은 manager/puller 재생성과 정책 재인증이며, 실제 앱 프로세스 재시작 검증은 아니다.
- 기존 후보의 새 설치는 이 로직 검증에 필요하지 않다. 실제 재개 전 서명 유효성은 별도 확인한다. 보존된 만료 기록: 2026-09-12 18:19:25 KST.

**보완 1건:** `isolated-test-result.json`의 `xcresult_summary_sha256` 값은 `fbc6a3a25b1f07e8a975cab1e4817c2db11a9a74998fefb15cd95b75160b36f8`인데, 첨부 `xcresult-summary.json`의 실제 SHA는 `4ececc09d81115712823139c6525d54e5be489fd0f00e8e6ca72bef8be29855f`다. manifest는 실제 첨부 파일과 일치하고 두 시험 결과/로그/코드도 일치한다. 원시 요약과 공유용 요약의 구분인지 오래된 참조인지 확인을 요청한다. 실기기 재개 전 기록을 보완하며, 완료 시험을 반복 요청하지 않는다.

## 교정 대상

시험 작품 `본문수신검증 20260911`, project UUID `9a78c51c-7de9-43a8-be54-d25a22d08a28`만 대상으로 한다.

| 항목 | 현재 | 적용 후 |
|---|---|---|
| 정렬 문서 UUID | `6df5660a-c1ae-492f-bb1d-26b582508074` | `ef6e1de1-a3d0-5959-96be-58f87a683cc0` |
| 내부 경로 | `__antigravity__/tree-order.json` | 동일 |
| 정렬 content | 385 bytes, revision 1 | 동일 |
| 정렬 SHA-256 | `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be` | 동일 |
| 일반 원고 | UUID `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 93 bytes, revision 1 | 동일 |
| 폴더 | 11개, 각 revision 1 | 동일 |

새 UUID는 UUIDv5(project UUID, 내부 경로)의 결과다. 프로젝트/원고/폴더 UUID를 재발급하지 않는다.

서버 읽기 조회 결과: documents 2, document_versions 2, folders 11, folder_versions 11, tree_orders/sync_batches/sync_operations 0, 프로젝트 설정 행 없음(LEGACY), 해당 UUID lease 0. public 15개 테이블에서 잘못된 UUID 참조는 documents 1개와 document_versions 1개뿐이며 올바른 UUID 참조는 0이다.

Windows는 종료 상태다. 잠근 파일 복사를 만든 후 복사본만 SQLite로 열었다. 스키마 8013, integrity 정상, FK 위반 0, 열린 contract 관문 0. 잘못된 UUID 참조는 sync_documents 1개와 완료된 sync_operations 1개뿐이다.

## 승인 후 실행할 구체적 범위

1. **사전 보존과 재확인:** Windows 종료, iPad 종료·오프라인 유지, 설치 파일·원고·설정·hold·관문·DB 상태를 재확인한다. 현재 DB/WAL/SHM을 새로 보존하고 복사본의 전체 행/스키마 지문이 검증 기준과 같은지 확인한다. 서버 원본 대상 문서/버전 행도 비공개 보존한다. 상태가 달라지면 적용하지 않는다.
2. **Staging 서버 교정:** 기존 operation/document/project 잠금 규칙과 단일 SERIALIZABLE 트랜잭션을 사용한다. 즉시 검사되는 자식 FK 때문에 버전 1행을 변수에 보존한 뒤 제거하고, 정렬 문서의 document_id만 교정한 뒤 같은 version_id/operation_id/본문/revision/작성자/시각으로 버전 행을 다시 넣는다. 최종 차이는 두 행의 document_id뿐이다. 잘못된 UUID의 live/tombstone 행은 모두 0이어야 한다. 서버 트리거/FK/RLS/권한/스키마를 끄거나 변경하지 않는다.
3. **Windows 장부 교정:** 독점 트랜잭션에서 sync_documents와 완료 operation(`3691241c-9a6f-45ff-851b-75c3fc121179`, queue 1581)의 document_id 두 필드만 교정한다. 기존 operation 변경 방지 트리거는 그 한 행의 정확한 UUID 치환만 허용하는 더 좁은 임시 보호 트리거를 먼저 설치한 뒤 트랜잭션 안에서 교체한다. 다른 필드/다른 행 변경은 거부한다. 원래 트리거를 복구하고 임시 트리거를 제거한 후 commit한다. 삭제 방지 트리거와 sync_purge_gate는 건드리지 않는다. 동기화 관문은 계속 닫혀 있다. 완료 이력의 본문·작업 ID·상태·payload hash·시각은 보존한다.
4. **사후 검증과 결과 묶음:** 서버 public 전체 행을 UUID 두 필드만 정규화해 전후 대조한다. 로컬 30개 테이블과 스키마도 같은 방식으로 대조한다. 원고 93바이트/SHA, 정렬 385바이트/SHA, revision 1, 폴더 11개, 기존 작품·설정·잠금 보존을 재확인하고 결과 ZIP을 만든다.

서버와 SQLite는 하나의 분산 트랜잭션이 아니다. 서버 성공 후 Windows 단계가 실패하면 양쪽 앱을 열지 않고 해당 단계의 상태를 읽어 확인한다. 응답이 불확실하면 쓰기를 반복하지 않는다. 서버를 임의로 역교정하거나 이전 초기 업로드를 재송신하지 않는다. 각 DB 내부 오류는 해당 트랜잭션 전체를 rollback한다.

이번 승인은 **지정 Staging 정렬 문서와 Windows 완료 이력의 한정된 UUID 교정**만을 뜻한다. iPad 실제 재개, marker 삭제, 신규 설치, 원고 송신, 관문 개방, prod 전환은 포함하지 않는다. 별도 허가가 필요한 이유는 첨부 회신이 아니라, 종전 초기 업로드/첫 수신 승인 범위에 이 데이터 교정이 포함되지 않았기 때문이다.

## 완료한 격리 검증과 한계

- Windows 실제 DB 복사본: 8건 통과. UUID 두 필드만 변경, 30개 테이블/스키마 보존, 원래 보호 트리거 복구, 재적용 거부, 3개 중간 실패 지점 rollback, 임시 보호 장치의 범위 외 변경 거부, 사전 상태 변화 거부.
- 서버 FK 모델: 메모리 SQLite에서 5건 통과. 부모 UUID 단독 변경 거부, 자식 보존→부모 교정→자식 복원 후 전체 보존, 3개 실패 지점 rollback.
- 실제 PostgreSQL SQL은 읽기 조회로 확보한 제약/트리거/인덱스/컬럼/잠금 규칙을 기반으로 작성했으며 **실행하지 않았다**. 로컬 PostgreSQL 실행 도구가 확인되지 않아 PL/pgSQL 컴파일·RLS·잠금·직렬화 실행까지 시험한 것은 아니다. 모델 결과를 PostgreSQL 실증으로 보고하지 않는다.
- 이번 검토 중 실제 서버/Windows DB/iPad 데이터 변경, 설치, 원고 송신, 관문 개방은 0회다.

제약 검사 시점은 [PostgreSQL SET CONSTRAINTS](https://www.postgresql.org/docs/current/sql-set-constraints.html), SQLite의 트랜잭션 내 FK 검사 유예는 [SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_defer_foreign_keys)를 확인했다. Supabase changelog.md 웹 읽기는 도구의 text/markdown 지원 오류로 열리지 않았다. 새로운 SDK/API 기능을 도입하지 않고 현재 서버에서 직접 읽은 정의를 사용했다.

## 사용자 행동 순서

1. 현재 양쪽 앱은 종료 상태로 유지한다. iPad는 오프라인을 유지하며 가져오기/재개 버튼을 누르지 않는다.
2. 이 문서의 **서버+Windows UUID 교정만 승인**하면 Windows 작업에서 1~4단계를 수행한다. 사용자가 SQL이나 DB 파일을 직접 수정할 필요는 없다.
3. iPad 작업에는 `ipad-control-repair-evidence-hash-clarification-request-20260911.zip`을 전달해 요약 해시의 출처만 보완한다. 기존 기기 상태·시험을 다시 실행하지 않는다.
4. 교정 결과와 해시 보완을 대조한 뒤 iPad의 같은 journal 재개 1회에 대한 구체적 안내를 제공한다. 본문 3줄 오프라인 관찰은 그 재개 성공 후 진행한다.

증거 폴더: `_evidence/ipad-control-document-repair-review-20260911/`
실행 후보: `server-repair-candidate.sql`, `local-repair-candidate.py`
원본 장부/사전 목록/격리 DB는 `private/`에 보존하며 외부 전달 ZIP에서 제외한다.
