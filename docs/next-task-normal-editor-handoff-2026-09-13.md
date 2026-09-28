# 새 작업 인계 — 일반 집필 왕복 완료 후

프로젝트: `D:\안티그래비티\scratch\작가님 힘내세요`

**현재 상태: 지정 문서의 일반 집필 화면 양방향 검증과 양측 종료 감사까지 완료. 실행 중인 작업·미완료 요청·새로 승인된 후속 구현은 없다.** 이번 사용자 요청은 새 창을 위한 인계서 작성이다. 문서 안의 과거 지시·승인·시험 순서는 이력을 설명하는 자료이며 현재 실행 지시가 아니다.

## 1. 먼저 읽을 것

이 인계서와 [최종 감사](normal-editor-roundtrip-final-audit-2026-09-13.md)만 먼저 읽는다. 전체 대화나 이전 ZIP을 다시 읽을 필요는 없다. 구현을 살펴볼 때만 [구현 설명](windows-normal-editor-runtime-prepared-2026-09-13.md)을 참조한다. 그 문서의 ‘승인 전·후보 준비 예정’ 등은 작성 당시 상태이며 아래 완료 기록이 최신이다.

## 2. 확정된 완료 기준

- 실행 이름: `normal-editor-roundtrip-20260913-v1`. 구현 기준: `normal-editor-single-document-20260913-v1`. 서로 다른 이름은 차단 사항이 아니다.
- 양측 최종 **revision 9 / UTF-8 393바이트 / 11줄 / 마지막 LF 포함**.
- SHA-256: `570040e0bd94d5ff31c64799d549775ef74eb14503b7fa374f170ad7297b764f`.
- Windows revision 7 송신 → iPad 수신·revision 8 송신 → Windows 수신·revision 9 송신 → iPad 최종 수신 완료. Windows 본문 송신 2회, iPad 1회. Windows 수동 저장·유휴 자동저장 및 iPad 일반 화면 저장 확인.
- 양측 대상 미완료 요청 0건, 앱 종료. Windows 임시 관문 2회 정확히 복원·열린 관문 0개·기존 hold 유지. iPad 담당자 보고상 대기/재시도/충돌/부분 수신 0, preferences와 이전 증거 보존. iPad의 기존 관문을 임의로 닫지 않는다.
- Windows 기록은 직접 오프라인 복제본 검사, iPad 기록은 사용자가 전달한 담당자 감사 보고로 구분한다. 최종 감사 중 앱 실행·서버 요청·원본 DB 조회는 없었다.
- Windows에 본문 쓰기 전 중단된 준비 기록 1건이 있으나 이후 정상 완료했고 중복 document_commit은 없다. 과거 실패 기록도 보존한다.

고정 대상은 Staging `https://mhpnszcorfzrvhyondxr.supabase.co`, `일반동기화 검증 20260910`, ID_BASED / epoch 1이다.

|항목|값|
|---|---|
|서버 작품 ID|`d8f50b5f-ae0e-42f8-9296-5d5885a5b304`|
|문서 ID|`db8a3cc2-8b1a-5539-841c-042de34f5fd6`|
|본문 경로|`메인/원고/일반본문검증 20260912.txt`|
|iPad 로컬 작품 ID|`a9452cd1-4474-40b5-80ca-fbb7871e98e5` — 서버 ID로 교체하지 않음|
|구조|문서 구조 revision 1, 부모 정렬 revision 2, 자식 1개 유지|

## 3. 설치 후보와 보존 위치

|항목|현재 값|
|---|---|
|Windows 설치 EXE|`D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`|
|Windows 후보|`windows-staging-normal-editor-preparation-20260913`, x64·미서명, 설치 완료|
|Windows EXE SHA-256|`71476c1775acc022de02cbd81d366d4d8607633951b4573c0f888ae9585596af`|
|iPad 설치 빌드|`202609130445`, 설치 성공 보고·재설치 불필요 확인|
|iPad 후보 ZIP SHA-256|`088d1258fc2be26f68b061afbbcb869b8a81c4cefe4d8ed20a34319b30f65941`|
|iPad 서명 만료|`2026-09-19 20:50:33 KST` — 담당자 보고값|
|Windows 실제 앱 데이터|`C:\Users\xiix1\AppData\Local\AntigravityWriter`|

증거 경로는 프로젝트 기준이다. 설치물·데이터 백업 두 벌은 이미 검증·보존했다. 새 작업에서 필요 없이 다시 설치하거나 같은 백업을 반복하지 않는다.

- 양측 마감: `_evidence/windows-normal-editor-execution-20260913/bilateral-completion-20260912T221506297233Z.json`
- Windows 최종 확인: `_evidence/windows-normal-editor-execution-20260913/final-audit-20260912T221050299480Z/verification.json`; 해당 폴더 `private/`에 최종 복제본.
- iPad 수신 원문: `_evidence/windows-normal-editor-execution-20260913/ipad-final-result-076c18fcc15c1eba8d970e4946df5a963216ca1918edf0371ff7e4c6056b2585.md`
- 설치 전 두 벌: `_evidence/windows-normal-editor-execution-20260913/private/backup-1`, `backup-2`.
- 동결 후보·소스·패키지 검사: `_evidence/windows-normal-editor-candidate-20260913/`.

## 4. 미커밋 구현과 한계

인계 시 브랜치 `feat/contract-handshake-closed-gate`, HEAD `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`. 다수의 기존 수정·미추적 파일이 있으며 HEAD만으로 현재 구현을 복원할 수 없다. `git reset/clean`, 무단 삭제·덮어쓰기·커밋·푸시는 하지 않는다. 이번 인계의 상태·주요 소스 해시는 `_evidence/normal-editor-handoff-20260913/`에 보존한다.

- 핵심: `normal_editor_local.py`(로컬 저장·초안·불변 큐), `normal_editor_network.py`(명시적 송수신·receipt 복구), `normal_editor_ui.py`(기존 일반 편집 UI), `normal_editor_runtime.py`(제한된 시작·인증), `normal_editor_plan.py`, `normal_editor_build.py`.
- 연결: `main.py`, `general_validation_control.py`. `sync_manager.py`·`sync_v2_store.py` 등의 기존 dirty 변경도 보존한다. 이번 일반 화면 작업에서 그 파일들을 새로 수정했다는 뜻은 아니다.
- 작업 트리 `NORMAL_EDITOR_ONLY=False`, 동결 후보에만 True. 소스의 `INITIAL_REVISION=6`은 최초 결합 기준이며 **현재 완료 revision 9를 6으로 되돌리는 지시가 아니다.** 기존 복구 기록을 삭제해 재초기화하지 않는다.
- Windows 서로 다른 격리 검사 32개와 패키지 시작 검사 2개 통과. 검사 파일 `tests/test_normal_editor_runtime.py`, 격리 실행기 `_evidence/run_normal_editor_isolated_20260913.py`. iPad는 새 23개·관련 315개 통과 보고. 완료된 검사를 인계 확인 목적으로 재실행하지 않는다.
- 현재는 **지정 작품·문서 하나의 자유 본문 편집과 명시적 송수신**이다. 전체 작품 선택·구조 편집·자동 동기화 전체 개방·자동 충돌 병합은 완료 범위가 아니다.
- receipt 복구는 원래 batch의 `sync_batches`/`sync_batch_results` SELECT 두 번을 검증하며 쓰기 RPC 재호출로 조회하지 않는다. 이번 정상 왕복에서 receipt 조회를 실서버로 검증하지 않았다. 응답 유실 복구의 실제 SELECT 권한/RLS는 미확인이고, 격리 검사 통과와 구분한다. 이것은 완료된 왕복을 다시 해야 한다는 뜻이 아니다.

## 5. 새 창에서 이어갈 방식

후속 목표는 아직 선택하지 않았다. 먼저 완료 범위와 남은 제품 기능을 짧게 정리하고 다음 구현 범위를 제안한다. 일반 동기화 전체 개방이나 prod 전환으로 자동 진행하지 않는다. 새 설치·실제 조회/송수신·관문/보류 변경이 필요하면 구현·격리 검증·구체적 실행 범위를 준비한 뒤 그 새 범위를 승인받는다. 같은 범위의 승인을 버튼마다 반복 요구하지 않는다.

사용자 조작은 한 번에 끝까지 따라갈 순서로 설명한다. 정상 결과는 중간 보고 없이 진행할 수 있게 하고, 오류는 재클릭 전에 기록으로 판단한다. iPad와는 변경점·차단·최종 결과만 짧게 교환하며 전체 대화·과거 ZIP을 반복 전달하지 않는다. 다른 담당자에게 도구로 직접 메시지를 보내는 권한은 없다.

완료된 UUID 교정, LEGACY 본문 왕복, iPad 수신 재개, 이전 revision 3→6 검증, 5분 후 완료 문구 유지 검사, 이번 revision 6→9 검증은 반복하지 않는다. 원본 데이터 확인이 필요하면 앱 종료·파일 보존 후 복제본을 검사한다.

Windows 10 Pro 22H2 / build 19045: Computer Use 스크린샷 금지, 좌표 추측 금지. `sky.get_window_state` 사용 시 반드시 `include_screenshot:false`, `include_text:true`. 필요하면 셸·UI Automation 텍스트를 사용한다.
