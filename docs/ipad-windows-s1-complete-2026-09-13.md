# Windows S1 완료 — iPad 첫 수신 진행 신호

현재 사용자 조작 후 종료된 Windows 저장소 복제본과 저장 응답을 대조했다. **19개 쓰기와 후속 수신 완료**, 대기 0·충돌 0·자동 꺼짐·앱 종료 상태다. iPad는 승인된 첫 수신과 계획된 `afterOriginalApply` 재시작 복구를 진행할 수 있다. 실제 `.txt` 경로와 B 정렬 revision 2를 첫 수신에서 확인해 결과와 차단만 회신한다.

- 명세 v2는 그대로: `78405ea61ffcc1ae0491676dc63389ba47f46db56586f576f1f78974a3649669`. 시험 ID 변경 없음. R `왕복.txt`는 W0 48바이트/revision 2, E `빈문서.txt`는 0바이트/revision 1. 양쪽 모두 A 아래에 있다. 구조 revision은 R=2, E=4다.
- Windows 수신 기준 정렬: 메모장=2, T=2, A=4(`[R,E]`), **B=2(`[]`)**. 실제 서버 `.txt` relative_path는 Windows 보관 snapshot에 없으므로 iPad에서 대조한다.
- Windows 사용량은 갱신 1회 포함 **HTTP 174/280, Auth 23/30, 쓰기 19/21**. 추가 수신 주기 2회를 포함한 값이며 HTTP 잔여 106, Auth 계획 잔여 7, 쓰기 잔여 2다. 빈 자동 여유는 1회 남았다. iPad 한도는 그대로이며 재설정하지 않는다.
- 실행 ID `6a7a9c7d-982a-4fcd-90e6-3b4140504860`, 공통 종료 **2026-09-13 12:30 KST** 유지. Windows는 다음 수신 신호까지 대기한다. 기존 원고/DB/device/hold 해시 보존 확인. 실제 receipt SELECT 확인을 이번 결과에 포함하지 않는다.

근거: [S1 확인 결과](../_evidence/windows-integrated-editor-execution-20260913/s1-verification.json). 기존 승인 범위의 진행 신호이며 새 작업 승인을 뜻하지 않는다.
