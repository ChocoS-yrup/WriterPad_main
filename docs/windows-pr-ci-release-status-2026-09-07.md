# 원격 반영·CI·배포 범위 진행 상태

> **최신 CI 확인 완료:** HEAD `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`의 실행 34109016242는 success다. 1198개 시험·빌드·Qt 시작 검사가 모두 통과했다(2026-09-07 19:15:51 KST 완료). 아래의 IN_PROGRESS는 인계 당시 기록이며 현재 상태가 아니다. 기능별 완료/남은 범위는 `docs/windows-completion-audit-2026-09-07.md`를 따른다.

> **최신 후속 결정:** 커밋·푸시는 완료 상태로 유지한다. 사용자는 prod 전환을 보류하고 Staging에서 Windows·iPad 개발과 필요한 검증을 먼저 끝내기로 했다. 다음 작업 인계는 `docs/next-task-staging-completion-handoff-2026-09-07.md`를 따른다. 아래의 서울 이전 계획은 향후 재개할 기록이다.

## 1. PR 생성 완료

공개 저장소 `ChocoS-yrup/WriterPad_main`과 기본 브랜치 main을 확인했다. 기능 브랜치 `feat/contract-handshake-closed-gate`의 기존 PR은 없다. 원격은 e5ff6e0, 로컬은 7f77ac0이며 새로 반영할 커밋은 b90c03f와 7f77ac0 두 개다. main 기준 누적 PR은 69개 커밋·69개 파일이며, 이 중 67개 커밋은 이미 원격에 공개되어 있다. 새 payload는 제품 Python 11개·테스트 Python 7개·CI YAML 1개다.

PR 본문: `_evidence/windows-pr-ci-release-20260907/pr-body.md`.
공개 범위 근거: `_evidence/windows-pr-ci-release-20260907/publication-scope.md`.

공개 범위에 대한 자동 승인 검토 차단 후 사용자가 구체적 저장소와 19개 파일의 두 커밋 공개 범위를 안내받고 ‘진행’으로 승인했다. 동일한 fast-forward push가 정상 완료됐으며 [Draft PR #9](https://github.com/ChocoS-yrup/WriterPad_main/pull/9)을 만들었다. PR head는 `7f77ac05ed997787ddfd3eff725eed41d5ac6c3b`, base는 main이다. GitHub가 병합 충돌 없음(MERGEABLE)을 보고했다. 병합·배포는 하지 않았다.

## 2. CI 오류 원인 보완·푸시 완료, 새 CI 진행 중

PR 생성으로 [Windows Sync Contract 실행 34107230857](https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/34107230857)이 시작됐다. head SHA는 PR과 일치한다. 계약 검증은 통과했으나 Windows 시험 1198개 실행 결과 오류 1개로 실패했다(669.758초). 빌드·Qt 시작·해시 단계는 건너뛰었다. 원인은 `test_sign_in_never_returns_raw_credentials_or_keys`의 SimpleNamespace 테스트 객체에 `_contract_lock` 등 현재 로그인 조정 상태가 빠진 것이다.

로컬에서 같은 오류를 재현하고 `tests/test_cloud_config.py` 한 파일에 8줄을 추가했다. 잠금·인증 세대·handshake mock을 준비하고 실제 가짜 로그인 호출까지 도달하는지 단언한다. 원래의 비밀값 비노출 단언은 유지한다. 제품 코드는 수정하지 않았다. 해당 모듈 17개 시험이 통과했고 로컬 커밋은 `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`이다.

추가 테스트 커밋은 자동 승인 검토에서 공개 범위 확인을 요구받았고, 사용자가 ‘커밋 후 푸시까지 진행하고, 푸시가 끝나면 새창에서 prod로 전환하기 위해 handoff를 준비해줘’라고 명시 승인했다. 이후 동일 기능 브랜치로 fast-forward push를 완료했다. 로컬 HEAD·origin 추적 ref·PR #9 head가 모두 `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`로 일치한다. 추가 승인을 다시 요구할 필요가 없다.

[수정 후 CI 34109016242](https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/34109016242)가 자동 시작됐다. 인계 준비 시점에는 IN_PROGRESS이며 아직 통과로 판정하지 않는다. 새 작업에서 이 실행의 결과를 먼저 확인하며, 실행 중인 CI를 중복 실행하지 않는다. 테스트 단독 변경이므로 현재 설치본을 다시 빌드·설치하지 않았으며 설치 EXE 해시 `cd7ccebf125864b63ac8a9552585c44221e1086d0e6fc3e2617c6fa89a12fd89`도 유지됐다.

## 3. 개인 사용·서울 prod 전환 준비로 결정

현재 확인된 설치본은 기존 개인 Staging 사용을 위한 빌드다. 일반 집필·자동저장·일반 동기화의 기존 사용 범위와 닫힌 계약 관문을 유지한다. CI는 로컬 release 설정을 전송하지 않으므로 cloud-disabled 패키지를 검증하며, 이를 현재 Staging 설치본과 같은 설정의 배포물로 취급하지 않는다.

사용자는 개인 사용을 유지하면서 한국 리전 prod로 옮기는 방향을 선택했고, 비활성 prod를 직접 재개했다. 서울 prod의 ACTIVE_HEALTHY 상태를 확인하고 Staging과 읽기 전용 대조를 진행했다. prod는 계정·작품 데이터가 없으며 현재 Staging과 함수 정의 차이가 있어 계정 UUID·작품 이력 보존을 포함한 이전안이 필요하다. 세부 대조와 다음 순서는 `docs/windows-personal-seoul-prod-transition-plan-2026-09-07.md`에 정리했다. 실제 Production 데이터 이전·기기 접속 전환·PR 병합·외부 배포는 하지 않았다.

## 다음 사용자 행동

새 창에서 `docs/next-task-seoul-prod-handoff-2026-09-07.md`를 읽고 CI 확인과 서울 prod 전환 준비를 이어간다. 앱 종료나 iPad 전달은 아직 필요 없다. prod 재개, 기존 커밋·푸시·설치, 완료된 계약 검증을 반복하지 않는다.
