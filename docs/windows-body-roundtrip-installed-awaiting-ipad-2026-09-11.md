# Windows 설치 완료·iPad 설치 결과 대기 — 2026-09-11

사용자의 현재 **“승인”**에 따라 준비된 본문 왕복 실행 범위 중 Windows 새 백업·후보 설치·실제 기본 잠금 확인을 완료했다. 승인 원문/승인 대상 문서 SHA 및 양쪽 후보 SHA는 `_evidence/windows-body-roundtrip-execution-20260911/user-approval.json`에 기록했다. 앞선 실행 범위의 ‘승인 대기’ 표기는 당시 준비 기록이며 이 문서가 이후 진행 상태다.

- **현재 설치 EXE:** `windows-staging-body-snapshot-scope-20260911`, SHA `9893f7c434bf9a4118c58a3ef015a6a5fa94bf285c75fad5caec566b34babd3f`, 79,406,832 bytes.
- **새 백업:** 현재 원본에서 각각 만든 두 벌. 설치 폴더 715파일/544디렉터리, AppData 8파일/2디렉터리. 원본 SQLite 파일을 쓰기·삭제 방지 읽기 핸들로 유지하고 복사했으며 두 벌 모두 모든 파일의 크기·SHA·mtime 검증 완료. 실제 DB는 진단용 SQLite로 열지 않았다.
- **새 검사 사본:** schema 8013, 본문 작품 LEGACY/epoch 0/대기 0, revision 1/93 bytes, 일반 시험 작품 ID_BASED/epoch 1, 열린 관문 0, 기존 hold SHA 보존.
- **설치/시작:** 승인된 EXE만 교체. 실제 전용 창에서 일반 자동 송신 잠금과 미연결 안내를 접근성 텍스트로 확인하고 정상 종료했다. 송신/수신 버튼은 누르지 않았다. 원본 DB/WAL/SHM 및 AppData 전체, EXE 외 설치 파일/디렉터리의 bytes·mtime이 유지됐다. 새 body run journal은 없다.
- **현재 단계:** 앱 종료 상태. iPad의 현재 상태 백업 두 벌·정확한 후보 설치·기본 잠금 확인 결과를 기다린다. 그 결과가 확인되면 같은 승인 범위로 Windows 127 bytes 송신을 먼저 수행한다. 아직 실제 왕복 성공을 주장하지 않는다.

Computer Use는 screenshot 없이 UI 텍스트만 사용했다. 최초 닫기 요청은 창의 사용자 입력 감지 때문에 실행되지 않아 새 텍스트 상태를 관찰한 뒤 정상 닫았다. 송신·수신 시도 기록은 없다. 일반 창 또는 자동 dispatcher를 실행하지 않았다는 고정 소스와 화면 안내/파일 보존을 근거로 하며, OS 패킷 캡처로 HTTP 전체를 측정한 것으로 표현하지 않는다.

미커밋 작업·기존 증거·두 새 private 백업은 보존했다. 관문/hold 변경·prod 전환·iPad 원격 실행이나 외부 메시지 전송은 하지 않았다. 다음 사용자 행동은 `ipad-body-roundtrip-install-authorized-2026-09-11.md`와 전달 ZIP을 iPad 측에 전달하는 것이다. 이 단계는 설치 준비 완료를 확인하기 위한 연락이며 재승인/추가 코드 검토 요청이 아니다.

근거: `_evidence/windows-body-roundtrip-execution-20260911/backup-verification.json`, `installation-verification.json`, `startup-ui-text.json`, `startup-preservation-verification.json`. private에는 새 백업 두 벌·검사 복제본·전체 파일 목록과 테이블 digest를 보존하며 공유 ZIP에서 제외한다.
