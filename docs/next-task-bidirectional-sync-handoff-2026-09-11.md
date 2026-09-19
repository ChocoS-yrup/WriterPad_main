# 새 작업 인계: 양방향 송신 검증 준비부터

작성: 2026-09-11. 작업 루트 `D:\안티그래비티\scratch\작가님 힘내세요`.

## 가장 먼저 알아야 할 현재 상태

사용자는 **양방향 송신 검증부터 새 창에서 진행**하도록 인계를 요청했다. 이 작업에서는 후속 송신 코딩/실행을 시작하지 않고 문서와 증거 묶음만 준비했다. 직전 “다음 진행”은 문서·파일 목록을 읽던 중 인계 요청으로 중단됐다. 중단된 턴에서 새로운 송신 코드 수정·시험·설치·서버 변경은 없었다.

**정렬 UUID 오류 교정 → iPad 같은 journal 수신 재개 → 오프라인 본문 3줄 관찰까지 완료됐다.** 실패 복구나 해시 보완으로 되돌아가지 않는다. 다음은 아직 수행하지 않은 일반 양방향 송신의 코드/후보/실행 범위를 구체화하는 일이다. 앱 전체 완성 또는 일반 동기화 전체 통과로 확대하지 않는다.

사용자는 단계가 지나치게 잘게 나뉘고 검토 ZIP·승인을 반복하는 데 불만을 표했다. 완료한 검사·빌드·수신을 반복하지 않고, 준비·검증·증거 작성은 묶어서 자율 진행한다. 실제 적용 승인 전에는 코드·격리 검증·정확한 대상/내용/횟수/복구 조건을 완성해 검토 가능한 결과를 제시한다. 막연한 승인 요청이나 불필요한 iPad↔Windows 사전 검토 왕복을 만들지 않는다.

## 권한과 현재 사용자가 할 일

- Staging 개발 완료 후 prod 이전이라는 결정은 유지된다. 지금 prod 전환·비활성화·데이터 삭제를 하지 않는다.
- 원래 설치·실제 송신·관문 개방 금지 이후, 사용자는 개별 후보 설치, 지정 초기 업로드, 첫 수신, UUID 교정, iPad 재개를 해당 범위별로 승인했고 그 작업은 소진·완료됐다. **이를 새 양방향 송신/관문 해제/설치의 포괄 승인으로 사용하지 않는다.** 새 증거 문서의 제안도 사용자 승인과 구분한다.
- 새 작업에서 자료 대조, 필요한 로컬 코딩, 격리 시험, 후보/실행안 준비는 진행할 수 있다. 실제 설치·시험 원고 편집/송신·관문/보류 변경은 구체적인 새 범위를 준비한 뒤 승인받는다. 이미 승인된 같은 범위의 세부 작업에 재승인을 요구하지 않는다.
- 현재 사용자는 앱을 열거나 버튼을 누를 필요가 없다. 마지막 보고상 Windows 종료, iPad 사용자 직접 종료·오프라인·USB 연결 상태다. 인계 작성 중 실기기/프로세스/서버를 재조회한 상태는 아니다.
- iPad에 직접 메시지를 보내지 않는다. 사용자 전달 파일을 준비한다. 새 작업 자체도 이 인계에서 생성하지 않았다.

## 필수 읽기 순서 — 오래된 시작 문서부터 되짚지 않기

아래 경로는 모두 작업 루트 기준이며 같은 로컬 작업 폴더에서 접근할 수 있다.

1. `docs/ipad-control-repair-resume-reviewed-2026-09-11.md` — 가장 최신 완료 판정 및 한계.
2. `_evidence/ipad-control-repair-resume-review-20260911/cross-verification.json` 및 `received/resume-result-summary.json`, `received/resume-completion-audit.json` — 수신 완료 증거.
3. `docs/windows-control-document-repair-completed-2026-09-11.md`와 `_evidence/windows-control-document-repair-20260911/target-baseline.json` — 현재 문서/폴더/본문 기준.
4. `docs/windows-general-test-checkpoint-release-2026-09-10.md`, `docs/windows-checkpoint-final-acceptance-2026-09-10.md`, `docs/windows-staging-read-preparation-complete-2026-09-10.md` — 기존 일반 시험의 보류 해제 코드와 닫힌 관문 상태에서 완료한 준비.
5. `docs/windows-uuid-candidate-and-install-preparation-2026-09-11.md` — 현재 Windows 후보 입력. 실제 설치 및 이후 보존은 9/11 업로드/교정 증거와 함께 판단한다.
6. 필요할 때 `docs/windows-remaining-coding-review-2026-09-07.md`의 R4 작업표를 확인한다. **9/7의 미확인·잔여 상태를 현재 상태로 복사하지 않는다.** 후속 catalog/수신/UUID 개발과 검증이 완료됐다.

## 완료한 작업 — 재구현·재실행하지 않음

- R1 AI 응답의 비용/이력 저장 실패 보호, R2 전체 작품 백업 UI/자료 범위, R3 Qt와 독립된 복구: 구현·격리 검사 완료. 관련 소스는 현재 작업 트리와 90개 동결 Windows 후보에 포함된다. 모든 실사용/배포 완료로 확대하지 않는다.
- 과거 두 고정 계약의 빈 2권/3권+순서 왕복, 원본·중단·성공 기록 보존: 완료. 일반 본문 왕복의 증거와는 다르다.
- Windows checkpoint 수용, schema 8013, 일반 시험 준비 UI/보류 해제 코드, purge 보완, 작품 UUID 등록 수정: 완료. **실제 관문 개방/송신 보류 해제는 아직 수행하지 않았다.**
- 서버 작품 catalog/identity 대조, iPad 수신 전용 보호 후보 설치/시작, 빈 작품 최초 가져오기/오프라인 바인더 관찰: 완료.
- Windows 시험 작품 1개·합성 원고 1개의 초기 업로드: 완료. 당시 보조 코드가 내부 정렬 UUIDv5 등록을 빠뜨린 것은 이 작업 에이전트의 실수였으며 iPad 수신 실패 원인이었다. 설치 제품 `record_tree_order`는 UUIDv5를 사용했다.
- 보조 코드 수정 및 6개 격리 검사, 서버·Windows UUID 교정: 완료. 잘못된 UUID tombstone을 남기지 않고 문서/버전/완료 이력의 UUID 참조만 교정했다. 원본 증거와 백업은 유지한다.
- iPad 시험 요약 해시 불일치: 원시 996바이트와 공유용 187바이트 파일의 구분 누락이었다. 정정본과 공유 파일 동등성을 대조해 **종결**했다. 재검증/정정 요청하지 않는다.
- iPad 같은 journal 재개 1회 및 오프라인 본문 관찰: 완료. 마지막 수신 결과의 UI ‘가져옴’, journal 0, 대상 공개 항목/binding 각 1, 원고 baseline 1/정상 정렬 baseline 1, 폴더 11개, 대상 operation/batch 0을 확인했다.

## 세 작품을 혼동하지 말 것

| 작품 | UUID 및 용도 | 마지막 확인 기준 |
|---|---|---|
| 본문수신검증 20260911 | `9a78c51c-7de9-43a8-be54-d25a22d08a28` | Windows/iPad/server 동일 UUID. LEGACY 초기 업로드 작품, 서버 project_sync_settings 행 없음. 본문 수신 성공. **새 계약 ID_BASED로 전환됐다고 추정하지 않는다.** |
| 일반동기화 검증 20260910 | Windows/server `d8f50b5f-ae0e-42f8-9296-5d5885a5b304` | ID_BASED/epoch 1, contract 0.2.0/protocol 3. 폴더 11·정렬표 12·C9 준비 확인 완료. 관문 닫힘/영속 송신 보류 유지. 과거 iPad local UUID `a9452cd1-4474-40b5-80ca-fbb7871e98e5`와 서버 연결은 의도된 기존 binding. 임의 교정하지 않는다. |
| 최초가져오기 | `d21b8876-e93e-42d3-a792-8f49c5471be9` | 빈 원고 작품의 catalog 최초 가져오기/오프라인 확인 완료. 다시 가져오지 않는다. |

새 양방향 시험에서 사용할 작품과 프로토콜은 준비 단계에서 명시적으로 정해야 한다. 본문 수신 작품이 편하다는 이유로 LEGACY↔ID_BASED 규칙을 건너뛰거나, 일반 시험 고정 상수를 다른 UUID로 치환해선 안 된다. 기존 LEGACY 본문 경로 확인과 새 계약 일반 자동 동기화 완료를 구분한다. 확인된 결함·부족한 연결만 구현한다.

### 본문 수신 작품의 확정 기준

- 원고: `메인/원고/1권/1화.txt`, UUID `502cdbe7-814c-42f8-8ed4-81c40cd94902`.
- 로컬 부모 1권 UUID: `1de12e60-f998-48b9-aae9-7675b4b42fb9`. 서버 원고의 parent_folder_id/name/structure_revision은 null인 LEGACY 기준이다.
- 본문 93 bytes, UTF-8 BOM 없음, LF·마지막 줄바꿈 포함, revision 1.
- 본문 SHA `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d`.

```text
본문 수신 검증 20260911
이 문서는 동기화 시험용 합성 원고입니다.
끝.
```

- 정렬 경로 `__antigravity__/tree-order.json`, 올바른 UUIDv5 `ef6e1de1-a3d0-5959-96be-58f87a683cc0`, 385 bytes/revision 1, SHA `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be`.
- 잘못된 UUID `6df5660a-c1ae-492f-bb1d-26b582508074`는 서버 문서/버전 0행이며 tombstone도 없다. Windows 참조도 교정 완료.
- iPad에만 남은 오류 marker는 `Documents/본문수신검증 20260911/집필모드/.writerpad-sync-merge-6df5660a-c1ae-492f-bb1d-26b582508074.json`, 812 bytes, SHA `f0e332c548a99431ce1f2f00a4037c73b30c83610bd3c4266a988574b96a1ceb`. **잔존은 예상값이며 정리를 다음 단계의 선행 조건으로 만들지 않는다.**

## 설치·코드·잠금 기준

### Windows

- 실제 설치 루트: `D:\안티그래비티\scratch\집필프로그램`. 작업 루트 안의 동명 하위 폴더와 다르다.
- EXE: 위 루트의 `작가님 힘내세요.exe`, SHA `76e695051d83977a15b66892557cba13dc4eb95a6124d0710d503337193fb04d`.
- 후보 ID `windows-staging-8013-uuid-20260910`, 동결 source 90파일: `_evidence/windows-uuid-exe-candidate-20260910/source/`, manifest `source-manifest.json`(source의 상위 디렉터리).
- 입력 snapshot SHA `e6a1768a8a00a89e5c98a95b9ac413640c063aafaacd20a7fb9e6c618433158e`.
- 실자료: 설치 루트 `작품목록/`, DB `C:\Users\xiix1\AppData\Local\AntigravityWriter\sync_v2.sqlite3`, schema 8013.
- 마지막 교정 후 모든 contract 관문 닫힘. 일반 시험 hold 파일 `.general-test-send-hold-d8f50b5f-ae0e-42f8-9296-5d5885a5b304`를 수동 삭제하지 않는다. 보존 SHA `f59b82b8159446edcb749753bbe83624cb80027c4ba22b40c6e99cce60920b6f`.
- `general_test_gate.py`의 `prepare_release`/`release`와 세대 검증 코드는 이미 존재한다. 9/10 최초 구현 문서의 “해제 미구현” 문구는 후속 완료로 대체됐다. `general_test_gate_target.py`는 **d8f50b5f 작품에 고정**돼 있다. 다른 작품 송신 보호까지 포괄한다고 가정하지 않는다.
- 관련 검토 시작점: `general_test_gate.py`, `general_test_gate_ui.py`, `general_test_gate_target.py`, `sync_manager.py`, `sync_v2_store.py`, `handshake_lifecycle.py`, `server_checkpoint.py`; `tests/test_general_test_release.py`, `tests/test_general_test_gate.py`, 기존 문서/dispatcher 검사. 전부 재시험하지 말고 변경 영향만 고른다.

### iPad

- 기준 HEAD `1107b820cd21cc8784a9cc34e4b04f8189fedb19` + 보존된 미커밋 변경.
- 현재 설치 후보 `ipad-staging-receive-auth-loading-fix-20260911`, bundle `com.chocos.writerpad.debug`, 0.1.0(1), Staging, `WRITERPAD_RECEIVE_VALIDATION`.
- 동결 소스 224파일 digest `292241e2d46b7cd15fa2c0be16621fbca2f50ddbde1891a7935e725847760476`.
- 후보 app ZIP SHA `b7ea9ef3e3ddab3735a0ec01f570b8650b5476e141e34f412bd8d877d835d8f3`.
- 정책 SHA `fd72d54b67d3a41e387538b97e2ca9a9ae9e54dee5079152b9ea701594f2ac40`.
- **수신 전용 후보**다. `ReceiveValidationPolicy.sendingAllowed == !enabled`이며 현재 enabled 보호를 제거해 곧바로 일반 송신을 허용하면 안 된다. 대상별 송신 허용/기존 큐 격리/재시작 정책이 준비돼 있는지 iPad 코드 기준으로 확인해야 한다. 필요한 후보 변경/설치는 구체화 후 승인 범위로 다룬다.
- 프로필 만료 보존 기록: **2026-09-12 18:19:25 KST**. 새 작업 실행일에 맞춰 유효성을 확인한다. 만료했다고 자동 재서명/재설치하지 않는다.
- 이전부터 존재하는 큐 pending 4/conflict 3/completed 1265/cancelled 101 보존. 시험 작품 송신을 허용한다고 전역 큐를 배출하지 않는다.
- 소스 manifest: `_evidence/ipad-first-import-evidence-bridge-review-20260911/nested/ipad-receive-auth-loading-fix-reply-20260911/source-manifest.json`.
- 확보된 관련 source: `_evidence/ipad-first-import-evidence-bridge-review-20260911/nested/ipad-catalog-receive-guard-boundary-fix-reply-20260911/source/`. Windows에 iPad 전체 빌드 환경이 있다고 가정하지 않는다.

## 마지막 수신 증거의 한계 — 새 실패로 되돌리지 않기

- 보존 감사 76개 중 75개 true. false 1개는 Documents 디렉터리 전체 mtime 동일성이다. 202개 경로는 모두 보존, ContractReviews와 대상 집필모드만 mtime 차이, 기존 파일 바이트 동일이라는 보고다. 관련 고정 소스와 부합하지만 syscall 추적에 의한 원인 확정은 아니다.
- 완료 문구는 안 보였지만 ‘가져옴’/journal 제거/정상 baseline/본문 관찰로 완료를 판정했다. 문구 관찰을 위해 다시 재개하지 않는다.
- iPad 직전 백업은 파일 열거가 같음을 확인한 이전 실패 후 보존본의 복제였다. 새 독립 전체 바이트 백업이 아니었다. 향후 새 실제 변경 단계에는 그때의 상태와 일관된 백업을 준비한다.
- 원시 iPad DB와 전체 파일 목록은 회신 ZIP에 없다. Windows 검토는 보고·코드·해시 대조이며 원시 감사를 재실행하지 않았다.
- 실제 HTTP 전체 추적/서버 전체 불변 검사를 하지 않았다. 송신 가드·큐·진단 근거를 “서버 쓰기 0회 직접 측정”으로 바꾸지 않는다.

## 새 작업에서 할 순서

1. 이 문서와 위 최신 완료 근거를 읽고 현재 작업 트리/동결 입력 차이만 확인한다. 마지막 승인/관측 시점과 현재 상태를 구분한다. 사용자에게 지금 앱 조작이 필요한지 먼저 명확히 알린다.
2. 양쪽 **기존 본문 수정→큐→송신→수신 적용** 경로와 프로젝트 mode, lease/권한, 관문·보류 범위를 대조한다. 일반 자동 동기화 구현 전체가 없다고 단정하거나, 두 고정 구조 계약과 수신 성공만으로 일반 왕복을 통과 처리하지 않는다.
3. 최소 시험 범위를 정한다: 하나의 지정 합성 원고, Windows 한 번 수정/송신→iPad 확인, 이어 iPad 한 번 수정/송신→Windows 확인. 정확한 전후 UTF-8 내용/SHA, 문서·프로젝트 UUID, 예상 revision/작업 ID·재시도 의미, 상대편 오프라인 관찰, 사후 잠금/종료/보존 조건을 적는다. 이것은 제안 순서이며 아직 실행 권한/대상이 확정됐다는 뜻이 아니다.
4. 실제 결함이나 필요한 보호 연결이 있으면 승인된 개발 범위에서 로컬 구현/격리 검증한다. 대상 밖 작품·기존 대기열·자동 재시도/응답 불명·재시작 상태가 시험 범위를 벗어나지 않는지 필요한 경계만 검증한다. 단순히 테스트를 통과시키려고 guard/trigger/프로토콜을 꺼서는 안 된다.
5. 코드/격리 결과/후보 준비/양쪽 사용자 행동을 묶어 검토 가능한 실행안을 만든다. iPad 확인이 필요하면 구체적 코드/후보/범위만 전달하고 이미 완료된 catalog·수신·해시 왕복을 재요청하지 않는다.
6. 실제 설치/송신/관문 변경이 필요한 경우 정확한 범위를 사용자에게 제시하고 승인받아 수행한다. 실패·불확정 응답에서는 먼저 상태/영수증을 확인하고 무조건 재송신하지 않는다. 시험 결과 후 다음 일반 작업표(생성·빈 내용·삭제/복원·구조·충돌·오프라인/재시작)의 기존 근거와 남은 실사용 항목을 갱신한다.

## Git·증거·환경 보존

- 인계 작성 시 직접 확인한 branch `feat/contract-handshake-closed-gate`, HEAD `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`.
- tracked 변경 11개: assistant_workflow.py, handshake_lifecycle.py, main.py, mode_assistant.py, project_dialogs.py, project_manager.py, settings_panel.py, sync_manager.py, sync_v2_store.py, tests/test_server_project_import.py, tests/test_startup_mode.py. 다수 untracked 소스/문서/증거 존재. Git 사용자 ignore 파일 접근 경고가 있었지만 branch/HEAD/status 조회는 완료됐다.
- **HEAD만 checkout하면 현재 작업과 비공개 증거를 잃는다.** 새 창은 같은 로컬 폴더로 연다. 임의 reset/clean/일괄 add/commit/push/merge를 하지 않는다. 다른 worktree가 필요하면 현재 변경/증거를 먼저 보존하고 실제 입력을 별도로 확인한다.
- 실제 교정 증거 `_evidence/windows-control-document-repair-20260911/`의 `server-verified.json`, `local-result.json`, `package-verification.json`을 사용한다. `private/`에는 전후 DB/서버 행/목록이 있으며 외부 공유하지 않는다. 완료한 `run-local.py apply`나 `server-repair-execution.sql`을 재실행하지 않는다.
- 최초 업로드 `_evidence/windows-nonempty-initial-upload-20260911/scoped_upload.py`는 원인 수정까지 완료됐지만 **이미 사용한 업로드 계획/operation은 재송신하지 않는다**. private/live-plan.json은 교정 전 UUID가 담긴 과거 계획이고 ScopeGuard가 거부한다. 새 일반 송신의 제품 경로를 이 도우미로 대신 입증하지 않는다.
- 실제 DB/WAL/SHM 또는 보존 원본을 검사 목적으로 제자리에서 SQLite에 열지 않는다. read-only 모드도 sidecar를 바꿀 수 있다. 앱 종료와 일관된 복사를 확보하고 검사 복제본을 사용한다. 승인된 실제 DB 교정은 이미 완료한 별도 예외였으며 일반 진단 허가로 확장하지 않는다.
- Python: `C:\Users\xiix1\AppData\Local\Programs\Python\Python311\python.exe`, PowerShell. UTF-8 명시, stdout은 필요 시 ensure_ascii 또는 UTF-8 설정.
- Windows 10 Pro 22H2 build 19045. Computer Use 스크린샷 금지. sky.get_window_state를 쓴다면 include_screenshot:false/include_text:true 필수, 좌표 추측 금지. UI Automation/텍스트/CLI로 확인한다.
- 계정·기기 UUID, 토큰, 원시 DB/원고, 프로필·영수증 원본을 출력/전달하지 않는다. 설치 경로/AppData 쓰기는 별도 도구 권한이 필요하지만 사용자의 구체적 승인이 이미 있으면 같은 승인 범위를 다시 묻지 않는다.

## 최신 전달 ZIP 식별

| 파일 | SHA-256 |
|---|---|
| windows-control-document-repair-result-20260911.zip | `1830930b5dcbcb84fc0c099b59e270ba9a432c4f72c3f66a98c79ce4eb7bf896` |
| ipad-control-repair-evidence-hash-clarification-reply-20260911.zip | `366f3d9d3e0935a7bae3d2a4a61e40510aa61db590a66fb0816422f258354bc5` |
| ipad-control-repair-consolidated-handoff-20260911.zip | `a0be858d4426b6c901bbdf31dcf51d33cd0a3a1ab0207823e6789a33fe3f214c` |
| ipad-control-repair-resume-result-20260911.zip | `90facd0b1a85975a0fbdff436381c8a0d6dfcbc9327cd61e156c3098f3b69362` |

iPad 수신 원본 위치: `D:\Download\메모장\개발관련\아이패드 전달내용\교차검증\ipad-control-repair-resume-result-20260911.zip`. 검증된 내용은 `_evidence/ipad-control-repair-resume-review-20260911/received/`에 있다. 이전 ZIP 내 pending/미승인/해시 보완 문구는 당시 기록이며 최신 완료 판정을 우선한다.

## 새 창에 붙여 넣을 요청

> `D:\안티그래비티\scratch\작가님 힘내세요\docs\next-task-bidirectional-sync-handoff-2026-09-11.md`를 읽고, 같은 로컬 작업 폴더에서 양방향 송신 검증 준비부터 이어서 진행해 주세요. 완료된 UUID 교정·iPad 수신 재개·본문 관찰·해시 보완·기존 구현/검사는 반복하지 마세요. 본문 수신 작품의 LEGACY 상태와 일반 시험 작품의 ID_BASED 상태, iPad 수신 전용 후보를 구분해 기존 경로를 대조하고 필요한 코딩과 격리 검증, 정확한 실행 범위를 먼저 준비해 주세요. 실제 설치·송신·관문/보류 변경은 준비된 범위를 승인받은 뒤 진행하고 prod는 전환하지 마세요. 단계마다 제가 할 행동을 알려 주되 불필요한 검토 왕복과 재승인은 추가하지 마세요.
