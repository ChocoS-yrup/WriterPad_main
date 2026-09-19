# Windows 격리 대상 생성·기준본 준비 경로 — 오프라인 구현 결과

2026-09-14. 사용자 요청 범위의 오프라인 구현·신규 영향 검사를 완료했다. 실제 대상 생성·조회·토큰 접근·로그인·설치·앱/GUI 조작·관문 변경은 하지 않았다.

**신규 26개 검사 통과, 실제 HTTP/credential 접근 0건.** 기존 완료 검사나 실제 실행을 재실행하지 않았다. 제품 기존 소스는 수정하지 않고 `isolated_target_bootstrap.py`, 신규 검사 파일과 이번 증거·문서를 추가했다.

## 구현한 경로

| 단계 | 구현 |
| --- | --- |
| 계획 준비 | 보존 reference 원래 바이트·본문 byte/LF/SHA·기존 후보 ID/이름/부모에 결합. 신규 서버 revision은 미확정으로 유지 |
| 생성 직전 확인 | 기존 조회와 같은 Q1~Q7을 새 범위 안에서 수행. 계정·계약·count·기존 항목·정렬·후보 충돌을 확인한 후에만 생성 요청 준비 |
| 생성 | 폴더 생성 1회 → 새 본문 문서 생성 1회 → 새 빈문서 생성 1회 → 공유 부모 정렬/새 폴더 정렬을 한 atomic_structure_commit으로 반영 1회 |
| 정렬 보호 | 기존 children을 유지하고 새 root 하나만 추가. 공유 부모의 직전 관찰 revision을 조건으로 사용. 충돌 시 이전 children으로 덮어쓰지 않음 |
| 생성 후 확인 | Q3~Q7에 해당하는 5개 테이블을 다시 읽어 count 확인. 생성 응답과 새 항목의 ID/부모/이름/revision/본문/정렬을 대조하고 기존 행 보존 확인 |
| 인계 자료 | `baseline-candidate.json`에 새 폴더·문서 원문·정렬·실제 응답/조회 revision을 담고 원본 응답·계획·요청·감사를 같은 격리 결과 경로에 보존 |
| 로컬 읽기 | `read_candidate()`는 정상 terminal·파일 hash·파일 집합·권한 false 필드를 확인. 누락·변조·링크 경로는 차단 |

생성 전 7 + 생성 요청 4 + 생성 후 5 = **최대 HTTP 16회/쓰기 RPC 4회/180초**의 별도 범위다. 과거 조회 7회 승인에 쓰기 권한을 추가한 것이 아니며, 이 수치는 아직 실제 실행 승인을 받은 범위가 아니다. 실제 writer_device_id는 호출자가 제공해야 하며 이번에 실기기 식별자나 실행 범위를 발급하지 않았다.

기존 `sync_contract.py`의 요청 생성기와 응답 검증기를 사용한다. document create의 base_revision=0/structure_revision=1은 프로토콜 생성 요청 규칙이며, 기존 후보 문서의 null revision을 바꿔 채우지 않는다. 기준본 후보에는 응답과 생성 후 조회가 일치한 revision만 넣는다.

공유 부모 정렬은 children/revision 및 서버 관리 updated_at만 변경 가능하게 대조한다. 기존 나머지 행은 정확히 보존됐는지 확인한다. 삭제 행·특수 metadata도 생성 전후 원본 행 대조에 포함한다.

## 실패 처리와 판정 한계

요청 전 예약과 생성 요청/operation/batch ID를 파일에 남긴다. 실패·취소·예약 기록 오류도 실행 폴더를 지우지 않으며 같은 run은 재실행하지 않는다. 네 번의 RPC 전체가 하나의 원자적 생성이라는 보장은 없다. 중간 실패나 응답 유실 때 일부 대상이 생성됐을 수 있으므로 자동 재POST·삭제·롤백하지 않는다. 후속 receipt 확인/복구는 이번 경로에 자동 연결하지 않았다.

모든 확인이 끝나도 결과는 **수신 기준본 후보**다. `baseline_ready`, `baseline_applied`, `execution_allowed`, `app_binding_created`, `complete`, `atomic_snapshot`은 false다. 순차 조회의 가시 범위와 조회 이후 변경 가능성은 남는다. 앱 적용·iPad 계약 호환·서버 출처의 독립 증명을 자동으로 완료 처리하지 않는다.

모듈에는 CLI·기본 live transport·credential loader·앱 store 결합이 없다. `prepare_plan()`은 순수 계획 함수, `prepare_baseline()`은 transport·access/key·현재 권한 확인 함수를 명시적으로 받는 실행 엔진이다. 실제 호출 시의 xiix1/빈 profile/credential lease·공개 설정/소스 pin·관문/hold 보존·승인 연결은 **후속 실행 호출부에서 묶어야 한다**. 이번 검사에서는 이 입력을 모두 합성값·모의 전송으로 주입했다.

## 검사·보존

신규 25건을 한 묶음으로 통과한 뒤, 결과 읽기 경로의 terminal/seal 링크 차단을 보완하고 추가 1건을 통과했다. 고유 26건이며 이전 검사 수와 합산하지 않는다. 검사 범위는 정상 생성·raw 기준본 준비, 원본 보존, 종료 run/계획/한도 변경 거부, 계정·계약·count·후보 충돌, 부모 revision 경쟁, 부분 생성·응답 유실·거부·잘못된 receipt, 생성 후 본문·정렬·보호 행 차이, 예약 저장 실패·취소·deadline, 결과 변조·terminal 누락·링크 차단이다.

고정 대상 식별자에 합성 행·본문을 넣은 모의 서버와 임시 폴더만 사용했다. 실제 Windows 관찰 원문이나 기존 원고를 fixture로 복사하지 않았다. 기존 검사 파일에서는 합성 서버/토큰 생성 helper만 재사용했고 기존 test 메서드는 실행하지 않았다. socket/DNS·실제 HTTP transport·keyring/WinVault·SID/WinDLL 접근을 차단했으며 그 경계에 도달한 시도도 0건이다.

증거는 `_evidence/windows-target-bootstrap-offline-20260914/result-final.json`, 개별 결과/로그, `code-hashes.json`에 있다. 검사 대상 기존 파일의 전후 hash가 모두 일치했다. 기존 미커밋 소스·실행 기록·원본·설치물에는 쓰기 작업을 하지 않았으며 실기기/설치물 전체를 새로 검사한 것은 아니다.

다음 구현은 **새 쓰기 범위용 실행 호출부의 사용자·세션·승인 연결**이다. iPad에서는 실제 수신 계약과 앱 결합 작업을 병행할 수 있다. 지금 사용자가 앱 버튼을 누를 일이나 iPad에 큰 자료 묶음을 전달할 필요는 없다. 필요한 대조가 생기면 이 후보 입력 구조와 차이만 전달한다.
