# 첫 계약 성공 영수증 대조 — Windows 실제 수신 대기

2026-09-06 KST. 수신 자료: `ipad-first-contract-committed-20260906.zip`.

**서버의 첫 계약 성공은 독립 확인했다. Windows 실제 저장소에는 아직 수신되지 않았으므로 양쪽 검증 완료로 판정하지 않는다. Windows 관문은 전체 15개 모두 닫혀 있다.**

## 서버 및 첨부 대조

- ZIP manifest의 8개 파일 크기/SHA256 모두 일치. 준비 요청과 installation.json은 직전 실행 준비 검토본과 바이트 단위로 같다.
- Staging `mhpnszcorfzrvhyondxr`, 최종검증03 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`의 고정 원장을 SELECT로 조회했다. 18:23:59 조회 결과는 첨부 18:12:25 결과와 조회 시각을 제외한 모든 열이 같다.
- batch `45af53f8-ec2c-46b9-bf84-b2d56857fe5c`, request SHA256 `710ac3cf39a545639efd17e9740cf1fdbcfe06e27f0d9b4a72aa1ecfd6ce2ed7` 그대로다.
- 18:11:35.741983에 `atomic_structure_commit_success / committed / applied=true` 기록. 두 operation 모두 attempt 1, result revision 1이며 원장의 추가 attempt는 없다. 두 operation 행은 한 원자 배치의 두 작업이다.
- response SHA256 `f25da30b5d7ed37786888c8cadaeca24dd900a2dd16d10650c33a15b34857553`를 Windows canonical 함수로 재계산했다. 현재 제품의 `validate_atomic_structure_response`도 해당 요청과 응답을 수락했다.
- 새 folder `4711b2ce-5de9-44ba-80fe-570515839549`는 원고 `14df4a55-b7cd-4790-bc53-f84148418c0f` 아래 2권이다. 새 tree_order `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`는 `[기존 1권, 새 2권]`, revision 1이다.
- 별도 18:25:16 구조 SELECT를 실행 준비 단계의 17:57 snapshot과 대조했다. 기존 폴더 11개와 문서 26개(원고 25개 + 숨김 메타데이터 문서 1개)의 선별 메타데이터는 전부 보존됐으며, 추가는 위 폴더 1개와 순서 1개다. 두 SELECT는 동일한 원자 snapshot은 아니다. 원고 본문 바이트는 읽거나 비교하지 않았다.

iPad의 completed/attempt 1/종료 후 열린 관문 0개, 기존 활성 노드 36개 보존과 새 빈 폴더 1개는 제출된 기기 검증 기록이다. 이번 Windows 작업에서 iPad DB를 직접 읽은 것은 아니다. 첨부에 적힌 타 작업의 사용자 승인 이력을 새 전송·재전송·관문 개방 승인으로 해석하지 않았다.

## Windows 18:25:39 실제 상태

| 항목 | 결과 |
|---|---|
| 소스 HEAD | `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`, 추적 파일 변경 없음 |
| 설치 SHA256 | `202a7487e85aa399dcedab7897f2621d63c4a765ead8824cef5f7f18b495d72c`, 기존과 일치 |
| 작품 | 최종검증03, LEGACY/0, protocol 3, 고정 0.2.0 digest |
| 관문 | 선택 작품 닫힘, 전체 15개 중 열린 관문 0개 |
| 계약 배치 / tree_order | 각각 0개 |
| 일반·구조 작업 최종 영속 상태 | completed 23, cancelled 1, 미완료 0 |
| 기존 로컬 폴더/문서 메타데이터 | 전송 전 snapshot과 모두 일치 |
| 새 2권 | sync_folders 행 없음, identity 노드 없음, 디스크 디렉터리 없음 |
| 저장된 원고 순서 | `[1권]` |
| 실제 원고 파일 수 | 25개, 기존 폴더 경로 모두 존재 |

기본 프로필 SQLite는 `mode=ro&immutable=1`로 조회했고, 읽기 전후 WAL 없음과 DB 크기/수정 시각 불변을 확인했다. 기기의 실제 identity 및 설정에서 구조 정보만 확인했다. 현재 상태는 **Windows 수신 전**이며, 수신 실패나 제품 결함을 입증하는 결과가 아니다. 앱을 강제 실행하거나 동기화를 직접 호출하지 않았다.

## 사용자가 다음에 할 행동

1. **이 회신과 근거 ZIP을 iPad 측으로 보내세요.** 서버 성공 확인, Windows 일반 수신 대기 상태라고 전달하세요. iPad에서 재전송하거나 준비 요청을 재발급하지 마세요.
2. **설치된 Windows 앱을 실행하고 최종검증03을 여세요.** 계약 관문은 계속 닫아 둔 채 일반 자동 수신을 기다리세요. 양쪽에서 이 작품의 내용·구조를 편집하지 마세요.
3. **원고 아래 1권 뒤에 빈 2권이 표시되는지 확인해 이 작업에 회신하세요.** 예: “최종검증03을 열었고 1권 뒤 빈 2권이 보입니다.” 보이지 않거나 오류가 나오면 그대로 알려주세요. 수동 폴더 생성이나 재전송으로 맞추지 마세요.

회신 후 동일 UUID/revision 1의 Windows DB·identity·디스크 수신과 기존 25개 원고·순서 보존을 다시 대조한다. 재열기 확인이 필요하면 그 단계의 구체적인 조작을 안내한다.

## 근거와 작업 범위

근거: `_evidence/windows-first-contract-receive-20260906/`의 archive-verification.json, server-receipt.json, server-metadata.json, windows-receive-state.json, review-verification.json, 두 SELECT SQL, 검증 스크립트와 received/.

이번 작업에서 계약 전송·재전송·관문 변경·서버 쓰기·실제 작품 수정은 없었다. 완료된 제품 수정, 전체 모의 수신, 전체 회귀 검사, 빌드·설치를 반복하지 않았다. Windows 실행 중 client_build_id는 확인하지 않았으며 소스 기본값으로 대신하지 않았다.
