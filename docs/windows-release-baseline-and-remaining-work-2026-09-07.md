# 검증된 Windows 소스 기준과 남은 배포 작업

2026-09-07 KST. 성공한 설치본과 소스를 연결하고 이번 변경의 커밋 준비 및 기존 근거 대조를 완료했다. **완료한 두 고정 계약과 기존 시험은 다시 실행하지 않았다.** 이 작업에서 앱·DB·서버·관문을 조작하거나 제품 소스를 고치지 않았다.

## 1. 고정한 소스와 커밋 후보

- 현재 브랜치 `feat/contract-handshake-closed-gate`, HEAD `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`.
- 실제 설치 EXE SHA256 `7b9c0eacc75da121afe02ddbe8784b9dc09cc37a6c1175ee4024b2d4d5b0fc73`, 79,315,384 bytes. 기존 성공 기록과 일치한다.
- 설치 EXE 안에 포함된 루트 프로젝트 모듈 **51개**를 현재 소스의 컴파일 결과와 재귀 비교했다. co_filename만 정규화했고 모두 일치했다. 기존 검토 빌드 입력 15개의 원시 파일 해시도 유지됐다. 제품 코드를 실행한 검사가 아니다.
- 후보 **18개 파일**: tracked 수정 5개 + 신규 제품 모듈 6개 + 신규 시험 파일 7개. 나머지 사용자 자료·기존 evidence·빌드·설정·원고는 후보에 넣지 않았다.
- 전체 후보를 정확한 바이트로 보존하고 Git HEAD부터의 통합 패치를 만들었다. 임시 디렉터리의 HEAD 파일에 적용한 결과가 현재 18개 파일과 개행 정규화 기준으로 일치한다. 마지막 8파일 증분 패치와 혼동하지 않는다.
- 통합 패치 SHA256 `ea46dff76b92850065f1b1f71c4d3198402641cb5f067b5067bc5a48d129666b`.
- Git index·HEAD는 변경하지 않았다. **커밋 준비까지 완료했으며 실제 커밋·푸시·CI 실행은 하지 않았다.** 전체 소스 ZIP은 정식 설치파일이 아니며 기존 HEAD가 필요한 커밋 후보 묶음이다.

후보 목록:

| 구분 | 파일 |
|---|---|
| 기존 파일 수정 | handshake_lifecycle.py, network_recovery.py, settings_panel.py, sync_manager.py, sync_v2_store.py |
| 신규 제품 모듈 | contract_preparation.py, reviewed_contract_sender.py, contract_http_zero_recovery.py, contract_post_coordination_resume.py, contract_readiness_diagnostics.py, contract_recovery_readonly.py |
| 신규 시험 | tests/test_contract_preparation.py, tests/test_reviewed_contract_sender.py, tests/test_contract_http_zero_recovery.py, tests/test_post_coordination_resume.py, tests/test_contract_readiness_diagnostics.py, tests/test_contract_recovery_readonly.py, tests/test_pull_review_coordination.py |

제안 커밋 제목: `fix: preserve reviewed contract history and coordinate one-shot sends with pulls`.

제안 설명: 닫힌 관문에서 고정 요청을 준비·내보내고, 현재 로그인으로 원장을 읽어 검토할 수 있게 한다. 최초 실행·중단 복구·추가 1회 정책의 기록을 서로 보존하면서 일반 수신과 송신 준비를 조정한다. 실행 직전 기존 계약 검사를 유지하고 HTTP 시도와 영수증을 영속화하며 종료 후 관문을 닫는다. 검토 요청은 일반 dispatcher에서 제외한다.

검증 설명: 신규 30개를 포함한 영향 검사 140개 통과 및 준비 기능의 앞선 검사 근거를 재사용했다. 이번 작업에서는 제품 시험을 반복하지 않고 소스/설치 식별, 구문, diff 공백 및 HEAD 패치 재적용을 확인했다. 최종 실통신 근거는 `windows-ipad-fixed-contract-complete-2026-09-07.md`다.

## 2. 기존 검증 근거와 현재 판정

시험 수는 시점과 포함 범위가 달라 서로 더하지 않는다. 오래된 문서의 ‘미완료’ 문구는 후속 성공 자료와 연결하고, 그대로 새 결함으로 취급하지 않는다.

| 범위 | 확보된 근거 | 판정 / 남은 의미 |
|---|---|---|
| 빈 폴더·원고 순서 계약 | 9월 6~7일 양쪽 실제 송신·수신·UUID/revision·영수증 대조 완료 | **두 고정 사례 완료. 반복 없음.** |
| 일반 저장·자동 동기화 | e5ff6e0 설치 후 사용자의 자동저장/일반 동기화 정상 확인, 집필·AI 안전성을 포함한 764개 실행/763 통과/1 skip 기록 | 기존 닫힌 관문의 일반 집필 경로에 근거 있음. 최신 고정 계약 성공을 모든 편집 기능의 새 실기기 시험으로 확대하지 않음. |
| 일반 오프라인 복구 | Windows 9월 6일 실제 차단 중 로컬 저장·대기 1건 → 해제 뒤 자동 복구·대기 0, iPad의 기존 오프라인/재실행 보존 보고 | **완료 근거 유지.** 다른 변경 없이 다시 네트워크를 차단할 필요 없음. iPad 과거 지연 1회의 원인은 미확정이지만 이를 새 실패로 재분류하지 않음. |
| 계약 원고 및 구조 API | 8월 12일 Staging E2E에서 document create/update/빈 본문/delete/restore/응답 유실, 폴더 이동·이름·순서·멱등성·권한 거절 통과 | 서버·당시 클라이언트의 기존 근거. 현재 양쪽 앱에서 모든 계약 UI 동작을 끝냈다는 의미는 아님. 이번 두 고정 사례를 반복할 이유도 아님. |
| 충돌·명시적 재시도 | 8월 27일 검증06·07 사용자 통과, 보존 상태 및 CI 성공. C4/C5/C9의 후속 소스·영속 이력 검증과 e5ff6e0 회귀 | 기존 수정 보존. 초기 C1–C12 문서의 보류는 후속 C9 완료 자료로 해소됐음. |
| 삭제·복원·동시 구조 변경 | 삭제/복원 순서·빠른 권 생성에 대한 원인/회귀 근거, 이후 folder delete/restore 관련 커밋이 HEAD에 존재 | 소스·회귀 근거 있음. 읽은 자료만으로 모든 후속 양쪽 UI 재현의 최종 통과를 새로 단정하지 않음. 정식 배포 범위를 정할 때 미확인 실사용 결과만 연결하며 과거 결함을 다시 구현하지 않음. |
| 백업·복구 | 독립 프로젝트 백업 core의 합성 검증 및 보존 규칙 문서 | core 근거와 제품 UI/운영 자동 복구를 구분. 운영 자동 복구 완료 근거로 쓰지 않음. |
| 최신 수신/송신 조정 | 140개 영향 검사와 이번 실제 HTTP 1 성공에서 review 중 pull_pending·epoch 유지, 종료 후 일반 수신 완료 | **현재 경계 수정의 격리·실행 근거 확보.** 재시험 요구 없음. |

주요 근거:

- `docs/next-task-contract-canary-handoff-2026-09-06.md`의 완료된 일반 저장·복구 기록과 후속 완료 문서.
- `docs/windows-handshake-stability-handoff-2026-09-06.md`, `docs/windows-contract-send-policy-implementation-2026-09-06.md`.
- `docs/stage-8-windows-staging-e2e-2026-08-12.md`.
- `_evidence/Windows-Validation0607-PostPass-20260827/POST_STATE.md`.
- `_evidence/Windows-DeleteRestoreOrdering-20260827/RESULT.md`, `_evidence/Windows-RapidCrossDeviceVolumeMerge-20260827/RESULT.md`.
- `docs/windows-reverse-preparation-implementation-2026-09-06.md`, `_evidence/windows-post-coordination-resume-20260907/tests.log`.
- `docs/windows-ipad-fixed-contract-complete-2026-09-07.md`.

## 3. 실제로 남은 배포 정리

### A. CI와 검증 설치본의 빌드 경로 통일 — 다음 우선 작업

현재 CI `.github/workflows/windows-contract.yml`은 `Antigravity_AI_Writer.spec`와 `dist/Antigravity_AI_Writer.exe`를 사용한다. 실제 검증 설치본은 `작가님힘내세요.spec`로 빌드했다. 두 spec은 이름뿐 아니라 Qt 런타임 누락 검사와 도구 DLL 제외 방식도 다르다. 옛 spec 파일이 없어 CI가 반드시 실패한다고 판정한 것은 아니다. **검증된 배포 기준이 서로 다르다는 구체적 차이**다.

다음 변경은 CI의 spec·산출물 경로·변경 감지 경로를 검증된 기준으로 맞추고, 실행 파일의 해시 및 필요한 Qt 시작 검사를 연결하는 것이다. 현재 feature branch의 push는 workflow의 main push 조건에 해당하지 않는다. 실제 CI는 커밋/PR 또는 명시적 실행 시 대상 커밋으로 확인한다. 이번에는 원격 CI 상태를 새로 조회하거나 실행하지 않았다.

### B. 고정 시험 UI와 일반 사용 화면 구분

`settings_panel.py`는 일반 클라우드 설정에 ‘역방향 계약 검토 · 최종검증03’ 카드 및 소모된 송신/복구 버튼들을 만든다. 내부 고정 ID·승인·원장 검사가 있으므로 이를 임의 송신이 가능한 결함으로 표현하지 않는다. **정식 사용 화면에는 이 시험 전용 카드가 노출되지 않도록 Debug/명시적 진단 모드로 제한할 필요가 있다.** 기존 중단·성공 원장이나 시험 폴더를 삭제할 필요는 없다.

이 UI 변경을 한다면 가시성·설정 연결만 검사하고, 이미 완료한 실제 계약 전송을 반복하지 않는다. 배포 정리 시 보존해야 하는 진단 내보내기 범위를 함께 정한다.

### C. 배포 대상·환경·서명 결정

현재 설치본의 로컬 release 설정은 **Staging `mhpnszcorfzrvhyondxr`**를 가리킨다. URL·publishable key 설정 파일은 Git 제외 상태이며 이 소스 묶음에도 실제 값을 포함하지 않는다. 요구사항의 주요 패키지는 고정 버전이지만, Git 체크아웃만으로 현재 기기의 설정까지 복원된다고 주장하지 않는다.

Authenticode 확인 결과 현재 EXE는 **NotSigned**다. 기존 개인 Staging 사용을 이어가는 것과 외부 사용자에게 정식 배포하는 것을 구분한다. 외부 배포 전 대상 서버와 배포/서명 방식을 확정해야 하며, 서명 미존재를 현재 집필 기능 실패로 취급하지 않는다. 이 작업에서 Production 연결·모드 전환·인증서 발급·게시를 하지 않았다.

## 4. 지금 가능한 사용 범위와 다음 행동

기존 **닫힌 관문·LEGACY 일반 집필/자동저장/일반 동기화**는 기존 실사용·회귀 근거를 유지한다. 새 고정 계약은 이미 소모된 시험이며 일상 작업마다 누르는 버튼이 아니다. 일반 계약 경로 상시 개방, 전체 프로젝트 모드 전환, Production/외부 배포는 아직 확정 범위가 아니다.

**현재 사용자 앱 조작과 iPad 전달 자료는 없다.** 다음 코드 작업은 A의 CI 빌드 기준 통일과 B의 시험 카드 노출 제한이다. 먼저 이번 18파일 커밋 후보를 별도 기준으로 보존한 상태에서 배포 정리를 진행하면 성공한 구현과 배포용 변경을 구분할 수 있다. 다음 변경에서 필요한 검사만 하고, 이번 두 계약을 다시 보내지 않는다.
