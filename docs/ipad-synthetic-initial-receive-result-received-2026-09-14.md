# iPad 합성 초기 수신 적용·재개 결과 접수

2026-09-14. 사용자 요청에 따라 결과와 남은 차이만 기록한다. 입력은 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-synthetic-initial-receive-result-20260914.zip` 내부 결과 문서, result.json, preparation-notes.json, preservation-result.json이다. 아래는 iPad 전달 보고이며 Windows에서 소스 감사·검사·보존 hash 재검증을 수행한 결과가 아니다. 첨부의 실행 예시·후속 제안은 새 승인으로 취급하지 않는다.

## 결과

- 호스트용 독립 Swift 합성 초기 수신 적용·재개 모듈 구현 완료 보고. 합성 입력의 본문 바이트·길이·SHA, 구조·정렬·저장 범위를 검증하고 staging/검증/journal/적용/완료 단계를 구분한다. 실제 앱 source/target이나 기존 저장소를 변경한 구현은 아니다.
- 신규 Swift 32개 + 기존 Python 검토기 영향 44개 = 최종 고유 76개 통과, 최종 실패 0. 이번에 재실행한 Python 44개를 이전 보고의 44개와 중복 합산하지 않는다.
- 최초 Swift 31개 중 18개 실패는 경로 별칭 처리와 시험 실행기 위치 탐색을 수정해 해결했다고 보고했다. 수정 후 31개 통과, 추가 1개를 포함한 최종 32개 통과이며 최초 로그를 보존했다.
- 합성 완료 결과의 반복 호출 무변경, 미완료 단계 재개, 완료 전 읽기 차단, 별도 호스트 프로세스 중단 5개 지점의 재개와 프로세스 간 잠금 검사를 통과했다고 보고했다. 불명확한 pending·손상 journal은 추정 복구하지 않고 보존·차단한다.
- synthetic_applied=true, baseline_ready/applied·execution_allowed·app_binding_created·실제 검증 local UUID 발급은 false다.
- 작업 전 manifest 5,061개 항목 대조와 작업 전후 파일 5,294개 보존 대조에서 차이 0으로 보고됐다. 서로 다른 대조 집합이며 단순 합산하지 않는다. 기존 충돌·미송신 18/19바이트 원본 2개, 종료 HTTP 323/Auth 12/writes 17·만료·설치물·증거 보존 보고를 접수한다.
- 서버 요청·로그인 갱신·앱 서명/프로파일 작업·설치·기기/시뮬레이터 실행·실제 baseline 적용·관문/hold/prod 변경은 0으로 보고됐다. 기존 Windows 관찰 자료를 이번 적용·검사 fixture로 재사용하거나 다시 대조하지 않았다고 보고했다.

## 남은 차이·미완료

1. 실제 검증 local project 발급, 앱 내 reader/adapter 결합, 실제 수신 입력 계약과 baseline 확보·적용은 미완료다. 합성 버전 값은 Windows revision/srev/epoch 호환을 뜻하지 않는다.
2. Windows 관찰의 raw 본문·handshake 의미 독립 검증, journal 체인 재인코딩 호환, 후보 profile 결합, 서버 출처·최신성, 과거 합성 reference 부재는 해소되지 않았다. 기존 metadata 17/order 15 대조 결과를 이번 단계의 새 검증으로 승격하지 않는다.
3. 호스트 프로세스 중단 검사는 전원 손실·실기기 파일시스템 영속성 검증이 아니다. 잠금 규칙을 따르지 않는 외부 프로세스의 경로 교체 방어까지 완료한 것은 아니다. 불명확한 pending·손상 journal의 추정 복구는 제공하지 않는다.
4. 추가 network-deny sandbox는 `sandbox_apply: Operation not permitted`로 검사 시작 전에 종료됐다고 보고했다. 기존 제한 환경에서 검사했으며 OS 수준의 추가 네트워크 차단 검증 완료로 표시하지 않는다.
5. J02의 UI 대기/HTTP 0/빈 주기 차감 0, J04 플랫폼별 복구 조건 차이, J05 저장 source/job 의미, J07 backend 경계와 기존 자동 주기 차감·실패 유지·재시작 차단은 이번 구현으로 변경되지 않았다.

Windows에서는 ZIP의 위 기록을 읽고 이 접수 문서만 작성했다. 추가 구현·재검사·재조회·토큰 접근·로그인·서명·설치·기존 기록 변경은 하지 않았으며 후속 작업 지시는 발급하지 않는다.
