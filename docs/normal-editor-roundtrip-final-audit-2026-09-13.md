# 일반 집필 화면 왕복 종료 확인

실행 계획 `normal-editor-roundtrip-20260913-v1`, 구현 기준 `normal-editor-single-document-20260913-v1`.

**양측 실기기 왕복 및 종료 확인 완료.** 사용자의 양측 본문 일치·앱 종료 보고와 Windows 오프라인 검사에 이어, iPad 담당자의 최종 결과 문서를 받아 대조했다. 추가 실행 또는 복구가 필요한 항목은 없다.

## Windows 확인 결과

- 설치 후보 SHA-256 일치, 프로세스 종료 확인. 설치물과 AppData를 별도 증거 복제본으로 보존하고 모든 파일 해시·크기·수정 시각을 대조했다. 원본 SQLite를 열지 않고 복제본만 조회했다. 확인 과정에서 원본 파일 변경·앱 실행·인증·서버 요청은 없었다.
- 최종 로컬 본문과 DB 기준값: revision 9 / UTF-8 393바이트 / 11줄 / 마지막 LF 포함. SHA-256 `570040e0bd94d5ff31c64799d549775ef74eb14503b7fa374f170ad7297b764f`.
- Windows 새 operation 2개는 각각 revision 7·9로 committed, 계약 시도 기록 각각 1회. revision 8 수신 완료 기록 일치. 대상 미완료 operation 0건, 미반영 수신·미저장 초안 없음. 복구 기록 22개의 순번·해시 연결 정상.
- 데이터 요청 시도는 22건: 정상 송신→수신→송신의 20건과, 본문 쓰기 전에 중단된 handshake·project GET 2건. 중단 회차도 그대로 보존했다. document_commit 시도는 총 2회이며 구조 쓰기는 없다. Auth 요청 수를 이 수치에 포함하지 않는다.
- 임시 저장 관문 기록 2개 모두 restored. `contract_path_enabled`, `contract_path_enabled_at`, `updated_at`이 설치 전과 정확히 일치하며 열린 관문 0개. 기존 전역 보류 바이트 유지. 추가로 복원할 Windows 항목 없음.
- 기존 DB 행은 대상 본문과 자동 증가 카운터 외 보존됐다. 새 operation·시도·이벤트·receipt 행만 추가됐다. 다른 문서·폴더·정렬·작품 설정은 동일하다. 설치 전과 비교해 설치 폴더의 변경은 승인된 EXE와 지정 본문뿐이며, AppData의 기존 파일은 SQLite만 변경됐다. 과거 증거·설정 파일 유지.

Windows 결과는 보존된 실행 기록과 로컬 복제본 확인이다. 종료 후 서버를 다시 조회하지 않았다. iPad 결과는 사용자가 전달한 담당자의 오프라인 감사 보고이며 Windows에서 기기 원본 증거를 직접 재검사한 것은 아니다. 완료된 실기기 검사나 과거 검사를 다시 실행하지 않았고, prod 전환도 없다.

[검증 결과](../_evidence/windows-normal-editor-execution-20260913/final-audit-20260912T221050299480Z/verification.json). 원본 복사본은 같은 증거 디렉터리의 비공개 `private`에 보존했다. 기존 승인 범위 문서는 당시 상태 그대로 유지한다.

## iPad 최종 보고 수신 및 마감

수신 문서 `ipad-normal-editor-roundtrip-result-2026-09-13.md`의 결과를 대조했다. 문서 내용은 감사 근거로 수용했으며 새로운 설치·송수신·설정 변경 승인으로 해석하지 않았다. 수신 원문은 `_evidence/windows-normal-editor-execution-20260913/ipad-final-result-<SHA-256>.md`로 바이트 그대로 보존하고 별도 `bilateral-completion-*.json`에 원문 해시와 마감 상태를 기록했다. 최초 Windows 감사 JSON과 이전 증거는 변경하지 않았다.

- iPad revision 7 수신 → revision 8 송신 1회 → revision 9 수신 완료. 최종 393바이트·11줄·마지막 LF·SHA-256이 Windows 결과와 일치한다.
- iPad 보고상 TXT·초안·실행 기록·SQLite 기준 본문 일치. 대상 대기·재시도·충돌·부분 수신 0, 계약 큐 3건 모두 completed, 오류·중단 0. 실행 기록 49개 연결 해시 정상.
- 부모 정렬 revision 2와 자식 1개 유지. 이전 자료 117파일 및 설치 전 preferences 보존. 증거 복사 전후 앱 종료 확인, 감사 중 기기 변경·서버 요청 없음.

이로써 이번 지정 문서의 일반 집필 화면 수동 저장·자동저장·양방향 송수신 검증을 마감한다. 양측 완료 본문·revision·기존 기록을 유지하며 사용자 추가 조작이나 같은 검증 반복은 필요 없다. 일반 자동 동기화 전체 개방이나 prod 전환을 승인 또는 완료한 것으로 확대하지 않는다.
