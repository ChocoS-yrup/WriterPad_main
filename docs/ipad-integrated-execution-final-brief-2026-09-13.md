# iPad 전달 — 최종 실행안 요약

**사용자 승인 전.** 수치·설정 회신을 반영했으며 추가 회신·제품 변경·재검사·ZIP 재전송은 필요 없다. 상세 기준은 [최종 실행안](windows-ipad-integrated-execution-final-2026-09-13.md)이다. 이 전달문은 실행 지시나 승인서가 아니다.

- 제안 시간: **2026-09-13 11:00~12:30 KST**(UTC 02:00~03:30), 최초 공통 기준·절대 만료 유지. 실행 ID `6a7a9c7d-982a-4fcd-90e6-3b4140504860`. 늦은 시작·재시작으로 연장하지 않는다.
- 후보: Windows `windows-staging-integrated-editor-20260913-v1`, iPad `202609130843`, 기존 합의 해시 유지. 승인 후 양측 설치하고 Windows가 UI 생성한 실제 시험 ID 5개와 정렬 명세만 공유한다. 이전 고정 시험 UUID는 사용하지 않는다.
- 명세/설정: **iPad의 실제 시험 ID·명세 해시는 별도 명세로 보존·대조한다. 실행 JSON에는 실행 ID·계정·만료·한도·자동·checkpoint를 결합한다.**
- iPad 쓰기 산정: S2=7, S3=5, S4=3. S2는 **R 첫 쓰기·복구 4호출 → 자동 꺼짐 상태에서 나머지 변경 모으기 → 자동 송신 1호출**. 이후 ‘자동 일시정지’와 예약 호출 완료를 확인한 뒤 S3. 빈 자동은 최대 6회에 포함한다.
- 초기 iPad 설정: `automatic:false`, `pollingSeconds:10`, `maximumRequests:520`, `maximumWrites:26`, `maximumAuthentication:16`, `checkpoint:"all"`. 계획된 4지점마다 정상 종료·동일 설정 재시작 후 ‘송수신·복구’. S2 시작/나머지 송신 때 ‘자동 재개’, S2 끝 ‘자동 일시정지’.
- 한도: Windows 200 + iPad 520 = 전체 HTTP 720, 시간 90분. iPad HTTP/시간/쓰기/Auth는 하드 차단, Windows 쓰기/Auth/빈 주기는 계획·기록 관리. iPad Auth 16회 내 완료를 보장하지 않으며 소진 시 중단한다. 여유로 추가 작업을 만들지 않는다.
- 순서: Windows 초기 송신 → iPad 수신·복구 → iPad 편집/구조 송신 → Windows 수신 → iPad E 별도 삭제 후 B 삭제 → Windows 삭제 수신 → iPad B/R 복원(E 삭제 유지) → Windows 수신 → Windows 최종 본문 송신·양측 정상 대조 → Windows 먼저 쓰기/iPad 마지막 충돌 → 양측 로컬 종료. 구조 송신 중 상대 자동 수신 중지, 충돌 뒤 iPad 일반 수신 없음.
- 미확인 유지: iPad 중간 불완전 snapshot 자동 회복, Windows 실제 receipt SELECT/RLS, Windows 실제 충돌 관찰은 이번 통과 항목으로 확대하지 않는다. 기존 rev9 문서·관문/hold/preferences·증거 보존, prod 전환 없음.

최종안에 사용자가 승인하면 범위 안에서 중간 승인 없이 이어가고, 예상 밖 상태·한도 소진에서는 보존 후 중단한다. 기기 간 실제 ID 명세와 구간 완료 신호만 교환하며 완료 후 결과를 한 번 정리한다. 직접 메시지는 발송하지 않았다.
