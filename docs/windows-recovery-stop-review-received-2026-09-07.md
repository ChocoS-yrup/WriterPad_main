# iPad 복구 중단 회신 대조 결과

2026-09-07 KST. 첨부의 개발 제안과 사용자 실행 요청을 구분하여 수신 자료·현재 소스 대조 및 새 격리 근거 확인을 완료했다. 제품 수정·설치·복구 회차 생성·송신·관문 조작은 하지 않았다.

- 수신 ZIP SHA256: `7a0d8f22225c20697e8de6cfd6a05c65fa8fcdd6c567ac06979a4b2d46fda5a1`.
- 수신 manifest 16개 크기/해시 검증, 이전 Windows 결과 ZIP 해시 및 근거 19개 일치, 첨부된 Windows 근거 8개 bytes 일치.
- 현재 sync_manager.py 전체 SHA256 `aff28a839504f84bf1ef29f381a4dd094db3e735a39b34da2ec2881b3c417e74` 및 발췌 메서드 4개가 일치한다. 관련 sender/recovery/handshake 소스 3개도 이전 제출본과 같다.
- 설치 EXE는 기존 검토본 SHA256 `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`와 일치한다.
- 검토한 격리 스크립트의 입력 경로만 현재 로컬 소스에 맞춰 별도 사본으로 실행했다. 제품 모듈을 import하지 않고 실제 메서드 AST와 합성 의존성으로 메모리 상태 전환만 검사했다. 결과는 iPad 제출 결과와 같다.

## 새로 확인한 코드상 가능성

`_review_execution_busy=true`이고 구조 기준이 contract인 상태에서도 일반 pull 진입이 허용됐다. 실제 pull 시작부의 `_begin_structure_authority_selection` 호출 후 coordinator.pulling=true, authority=unknown, accepted/allowed=false, write epoch +1을 확인했다.

이 검사는 일반 수신과 검토 송신 준비의 진입을 조정할 필요가 있다는 근거다. 실제 Qt worker 전체·파일·DB·네트워크·claim·sender는 실행하지 않았다. 실제 중단 당시 unknown을 만든 호출자나 호출 순서는 여전히 미확정이다. 기존 중단은 실패 조건 authority_allowed=false, 쓰기 HTTP 0회라는 결론을 유지한다.

## 다음 개발 범위에 대한 판단

회신의 최소 범위는 타당하다. 일반 수신 시작과 검토 준비 진입을 동일한 짧은 lock 아래 조정하고, 이미 진행 중인 수신은 claim 전에 감지하며, 송신 준비 중 도착한 수신 요청은 보존해야 한다. 네트워크 동안 lock을 유지하지 않고, 종료·실패·작품/계정 변경에서 현재 세대의 보류 요청만 재개하는 검증이 필요하다.

진단은 허용된 authority enum, 고정 변경 사유 코드, 순번/세대, pull 상태와 write epoch 등으로 제한한다. blocked를 강제로 허용하거나 기존 기준 검사·영속 시도·중복 송신 방지를 약화해서는 안 된다. 제품 수정 후에는 실제 coordinator/worker signal 경로를 포함한 새 경계 시험과 영향받는 회귀만 수행하는 범위가 적절하다.

이번에는 실 DB나 서버를 반복 조회하지 않았다. 원본 요청과 두 stopped 기록·관문 닫힘은 이전 검증 시점의 근거로 대조했으며, 이를 새 시점의 기기 상태 검사라고 표현하지 않는다. 관련 레코드와 앱 설정에 쓰기 작업은 없었다.

## 다음 사용자 행동

앱에서 누를 버튼은 없다. 관문은 계속 닫아 두고 복구/송신을 다시 누르지 않는다.

첨부 안의 개발 요청 예문은 아직 사용자가 직접 보낸 요청과 구분된다. 다음 개발을 시작하려면 “첨부 범위대로 최소 조정·진단과 격리 검증을 진행해 주세요. 원본과 두 stopped 기록을 보존하고 설치·새 회차·송신·관문 개방은 하지 마세요.”라고 이 Windows 작업에 알려 준다. 해당 지시 후 구현 결과와 근거 ZIP을 iPad 전달용으로 준비할 수 있다.

대조 근거: `_evidence/windows-recovery-stop-review-20260907/verification.json`, `pull-interleaving-result.json`, `run_local_probe.py`, `probe.log`, `received/`.
