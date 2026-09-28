# 통합 집필 교차 검증 종료 후 새 창 인계

프로젝트: **`D:\안티그래비티\scratch\작가님 힘내세요`**. 2026-09-13 작성. 이번 요청은 새 창용 handoff 준비이며 새 구현·설치·서버 실행의 승인이 아니다. 아래 과거 사용자 승인과 실행 결과는 이력으로 구분한다.

## 1. 먼저 읽을 문서와 현재 결론

1. 이 문서
2. [최종 통합 감사](windows-ipad-integrated-final-audit-2026-09-13.md)
3. 필요한 세부 항목만 [최종 마감 기록](../_evidence/windows-integrated-editor-execution-20260913/final-closure.json)과 [수정판 설치 기록](windows-receipt-capability-fix-installed-2026-09-13.md)에서 확인

**지정 Staging 통합 교차 검증은 종료됐다.** 본문·구조·삭제·복원·iPad receipt 복구와 충돌 보존을 확인했으며 양측 자동 꺼짐·앱 정상 종료 상태다. 완료된 단계나 전체 검사를 인계 확인 목적으로 반복하지 않는다. iPad S2 자동 주기 초과와 양측 중간 본문 저장을 보존하므로, 실행안 준수 전체 통과나 전체 제품 출시 준비 완료를 뜻하지 않는다.

이전 [일반 집필 인계서](next-task-normal-editor-handoff-2026-09-13.md)의 설치 후보·진행 상태는 과거다. 거기에 있는 완료 원고의 보존 기준만 유지하며, 이전 설치물로 되돌리지 않는다.

## 2. 사용자가 원하는 진행 방식

- 구현은 가능한 묶어서 진행한다. 필요한 컴파일·국소 확인은 하되 변경과 영향 범위의 검사를 한 묶음으로 실행하고, 실패 수정 후 실패/영향 부분만 다시 확인한다.
- 정상 진행은 **짧은 완료 신호만** 교환한다. 매 단계 문서·스크린샷·전체 증거를 다시 요구하지 않는다. 오류/계약 차이/실행량 초과가 있을 때만 상세 자료를 확인한다.
- 양측 신호가 필요한 곳은 삭제 수신 후 복원, 복원 수신 후 상대 편집, 같은 기준에서 Windows 선송신 후 iPad 충돌 관찰처럼 순서에 영향을 주는 지점뿐이다.
- **GUI는 사용자가 직접 조작한다.** 사용자는 「그냥 나한테 시켜 너무 오래걸려」라고 요청했다. 한 번에 따라 할 수 있는 짧은 순서를 안내하고, 자동 UI 조작으로 시간을 쓰지 않는다.
- 기존에 승인한 동일 범위를 버튼마다 다시 묻지 않는다. 문서의 과거 승인으로 새 설치·실제 조회/송수신·로그인 갱신·관문/hold 변경을 실행하지 않는다. 새 실행이 필요하면 먼저 구현·검증·대상/횟수/시간을 구체화한다.
- iPad 담당자에게 도구로 직접 메시지를 보내지 않는다. 사용자가 필요한 신호/자료를 전달한다. 하위 에이전트나 새 작업 창을 자동 생성하지 않는다.

## 3. 완료 상태와 의도적으로 남긴 상태

| 항목 | 최종 상태 |
|---|---|
| 실행 ID | `6a7a9c7d-982a-4fcd-90e6-3b4140504860` — 완료된 실행, 새 작업으로 재사용/초기화하지 않음 |
| 실행 만료 | 2026-09-13 **15:00 KST**. 종료된 실행의 남은 HTTP가 새 권한을 뜻하지 않음 |
| Windows 최종 R | `Windows 충돌 기준\n`, **22바이트/rev9/srev4** |
| iPad 충돌 | 기준 W2 **59바이트/rev8**, 로컬 `iPad 보존 초안\n` **19바이트**, 원격 Windows **22바이트/rev9** 보존 보고 |
| iPad 미송신 원본 | **2개 의도적으로 보존**. 삭제·병합·강제 완료·충돌 후 일반 수신 금지 |
| 구조 | `T=[A수정,B]`, `A수정=[]`, `B=[R]`, E 삭제/0바이트 유지 |
| Windows 상태 | 대기0·충돌0·자동 꺼짐·앱 종료·감사914개 |
| Windows 누적 | **HTTP228/280·Auth32/32·쓰기22/22** |
| iPad 보고 누적 | **HTTP323/520·Auth12/16·쓰기17/26** |
| 합계 HTTP | **551/800**, 종료 감사 추가 요청0 |

Windows는 실제 종료 복제본·저장 응답·TXT·해시로 검증했다. iPad 최종 충돌/종료는 사용자가 전달한 최종 신호이며 원본 journal 전체를 Windows에서 직접 감사했다고 주장하지 않는다.

### 별개의 완료 원고 보존 기준

이전 일반 집필 원고는 통합 시험 R과 다른 문서다. **revision9·393바이트·11줄·마지막LF**, SHA `570040e0bd94d5ff31c64799d549775ef74eb14503b7fa374f170ad7297b764f`, 문서 ID `db8a3cc2-8b1a-5539-841c-042de34f5fd6`, 경로 `메인/원고/일반본문검증 20260912.txt`다. 이번 통합 마감에도 Windows 원본 파일·원본DB·device·hold와 집필 폴더 전체 목록/해시가 그대로임을 확인했다. 원래 정상 왕복을 반복하지 않는다.

## 4. 설치물·현재 작업 트리·자료 위치

| 항목 | 값 |
|---|---|
| 브랜치 / HEAD | `feat/contract-handshake-closed-gate` / `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4` |
| 상태 | tracked dirty13개와 다수 미추적 파일. HEAD만으로 현재 구현을 복원할 수 없음 |
| Windows 설치 | `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe` |
| 현재 패키지 ID | `windows-staging-integrated-editor-20260913-receipt-v2` |
| EXE SHA-256 | `4edfd3812d538dae72f4a370f6202083d64064ae2c8da404404a677e391e807c` |
| 통신용 build ID | `windows-staging-integrated-editor-20260913-v1` 유지. 패키지 ID와 구분 |
| 동결 소스·패키지 | `_evidence/windows-integrated-receipt-candidate-20260913/` |
| iPad 후보 보고 | `202609131307`, ZIP SHA `d887febc5d7ab0116101b714c6e4149cba3a4e01c83345b54ea27945f4e9c90f` |
| AppData | `C:\Users\xiix1\AppData\Local\AntigravityWriter` |
| 통합 전용 DB | 위 AppData의 `integrated-editor-20260913\workspace.sqlite3` |
| DB SHA-256 | `c89ba4154dc8880bcc101fd2f02d4b05f850509b2468c017153ca3d2a4202f40` |
| 실행 설정 | 같은 전용 폴더의 `reviewed-execution.json` |
| 설정 SHA-256 | `b233099cf9d5265add4f59f7b6443ba92ff7fa9e635984354c44f60a12467ac6` |

인계 시 git 상태와 주요 소스25개/설치/DB/실행 설정 해시는 [_evidence/integrated-editor-handoff-20260913/state.json](../_evidence/integrated-editor-handoff-20260913/state.json)에 기록했다. `git-status-before-handoff.txt`는 이 handoff 문서 생성 전 상태다. reset/clean, 무단 삭제·덮어쓰기·커밋·푸시 없이 기존 dirty 변경을 보존한다.

- 현재 핵심 코드: `integrated_editor_store.py`, `integrated_editor_sync.py`, `integrated_editor_ui.py`, `integrated_editor_runtime.py`, `integrated_editor_plan.py`.
- workspace의 `INTEGRATED_EDITOR_ONLY=False`, 동결 후보만 True. 이 값이 실제 통신 승인은 아니다.
- 33개 통합 검사 증거: `_evidence/windows-integrated-editor-20260913/20260913T040046979821Z/`. 새 관련 수정 전 재실행할 필요 없음.
- 수정 패키지 증거: `build-verification.json`, `smoke-result.json`, `preserved-run-compatibility.json`, `installation-result.json`. 이전 EXE는 같은 후보 폴더 `private-install-backup/`에 보존.
- 실기기 단계별 증거: `_evidence/windows-integrated-editor-execution-20260913/`. 최종 복제본 `private/after-s6/`, 핵심 `s3-verification.json`, `s4-verification.json`, `s5-verification.json`, `s6-windows-verification.json`, `final-closure.json`.
- 전체 실행 상태 문서는 `docs/windows-integrated-editor-execution-status-2026-09-13.md`. 최신 항목이 위에 있으며 아래는 역사 기록이므로 모든 과거 단계를 다시 읽어 실행하지 않는다.

## 5. 고정 대상과 과거 승인 구분

Staging URL `https://mhpnszcorfzrvhyondxr.supabase.co`, 작품 `일반동기화 검증 20260910`, 서버 작품 ID `d8f50b5f-ae0e-42f8-9296-5d5885a5b304`, ID_BASED/epoch1. iPad 로컬 작품 ID `a9452cd1-4474-40b5-80ca-fbb7871e98e5`는 서버 ID로 바꾸지 않는다.

시험 ID 전체는 [실제 명세 v2](../_evidence/windows-integrated-editor-execution-20260913/exchange/test-targets-v2.json), SHA `78405ea61ffcc1ae0491676dc63389ba47f46db56586f576f1f78974a3649669`에 있다. R ID `955ff845-aa32-4f10-956e-bac83501b205`, E ID `be2814bb-a04d-4954-aaab-b600235da812`. 명세의 미송신 상태/이전 EXE 해시는 작성 당시 기록이다. 최신 설치와 완료 상태는 이 인계서와 최종 감사로 대조하며 기존 명세를 덮어쓰지 않는다.

과거에 19개 초기 쓰기, 계획 초과 후 수동 재개, 15:00 연장, receipt 수정판 설치, 로그인 갱신3회, Windows Auth32/쓰기22 정정 등을 사용자 승인으로 수행했다. 모두 해당 실행과 조치에 묶인 승인이다. 현재 Windows Auth/쓰기 승인량은 소진됐다. 로그인 토큰은 마지막 갱신 결과 **15:02:18 KST 만료**였지만 새 작업에서는 날짜/현재 시각을 확인해야 하며 자동으로 다시 갱신하지 않는다.

완료된 `refresh-*.py`, `extend-to-1500.py`, `install-patch.py` 등은 해시·1회성 경로가 고정된 실행 증거다. **새 창에서 재실행하거나 경로/해시만 바꿔 사용량·만료를 초기화하지 않는다.** 빌드 스크립트의 기존 출력 폴더도 재사용하면 안 된다. 새 수정이 승인되면 기존 동결본을 보존하고 새 산출 경로를 사용한다.

## 6. 남은 과제와 제안하는 다음 한 단계

후속 목표는 아직 새로 선택/승인하지 않았다. 새 창에서는 아래 우선순위를 짧게 제안하고, 자료·코드의 오프라인 확인부터 진행할 수 있다.

1. **자동 수신 한도 초과 재발 방지**: S2에서 빈 자동13주기가 허용6을 넘었다. Windows도 빈 주기·Auth·쓰기별 제한은 계획 관리였으며 전체HTTP/시간만 하드 차단이다. 첫 구현 범위 제안은 Windows `AutoSync`와 전용 실행 기록에 빈 자동 주기 사용량을 영속화하고, 허용량에서 자동을 멈추며 재시작으로 초기화되지 않도록 하는 것이다. 기존 완료run은 이관/초기화하지 않고 복제본·합성 상태로 검사한다. iPad에는 같은 의미의 필요한 변경/차단만 맞춘다. 전체 한도 초과 원인/기기 구간별 정책이 확정되기 전에 새 실행 JSON 필드를 임의로 호환되었다고 가정하지 않는다.
2. **저장 UI 원인 확인**: iPad E17→18→0바이트, Windows W2 58→59바이트처럼 끝LF 추가가 별도 저장 작업으로 남았다. 구체 UI 원인은 미확정이다. 자동저장/붙여넣기/명시적 저장의 경계를 확인하되, 서로 다른 본문을 단순 중복으로 지워버리거나 요청을 병합하지 않는다. 원래 작업·미전송 후속 편집 보존이 우선이다.
3. **남은 실제 검증**: Windows 실제 receipt SELECT/RLS와 Windows 실기기 자체 충돌 관찰은 이번에 확인하지 않았다. iPad 성공을 Windows 성공으로 확대하지 않는다. 필요성이 정해지면 최소 별도 실행 범위를 준비하며 기존 정상 왕복은 반복하지 않는다.
4. **iPad 중간 구조 snapshot 회복**: 저장된 불완전 snapshot에서 새 snapshot으로 자동 회복하는 부분은 이번 대기 순서로 구현/검증되지 않았다. 일반 프로젝트 전환·상시 자동 사용·prod 준비도 별도 단계다.

이번 receipt 수정은 **Windows 통합 경로**의 비교만 고쳤다. `normal_editor_network.py`의 과거 일반 집필 경로에는 유사한 전체 metadata 비교가 남아 있으므로 향후 공용화/일반 앱 복귀 때 영향 검토 대상으로 삼되, 이번에 함께 수정·실서버 검증했다고 주장하지 않는다.

이번 handoff 작성만으로 위 후속 구현·설치·실제 서버 조회/송수신·로그인 갱신·관문/hold 변경 또는 prod 전환을 실행하지 않는다. 구체적으로 승인받은 새 작업이 있다면 그 범위에서 자율적으로 진행하고 같은 승인을 반복 묻지 않는다.

## 7. Windows 환경 주의

Windows10 Pro22H2/build19045. **Computer Use 스크린샷 금지**, 좌표 추측 금지, `sky.get_window_state`는 반드시 `include_screenshot:false`, `include_text:true`다. 현재 사용자 선호는 GUI 직접 조작이며 셸은 자료 조회/복제본 검사에 사용한다. 실제 UIA 정보는 기본 sandbox에서 보이지 않을 수 있고, `MainWindowHandle=0`만으로 앱 종료를 단정하지 않는다.

실제 원본 SQLite를 읽기 위해 열지 않는다. 필요할 때 앱 종료를 확인하고 DB/WAL/SHM을 파일로 보존한 뒤 복제본을 검사한다. 이번 handoff에서는 기존 최종 복제본을 재사용했고 새 원본 DB 연결·서버 요청·설치 변경·앱 실행을 하지 않았다.
