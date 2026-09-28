# Windows 조정 빌드 설치·실제 비송신 관찰 결과 — iPad 전달용

2026-09-07 KST. 승인된 새 빌드 설치와 실제 앱의 준비 상태 조회·내보내기를 완료했다. **새 11개 조건 모두 충족, 관문 닫힘, 원본과 두 stopped 기록 보존**을 확인했다. 이번 단계에서 새 복구 회차·송신·재전송·관문 개방은 하지 않았다.

## 설치 식별

- iPad 회신 ZIP: `7543e89c88f4e65a2c877184a0dfb1a752b8570402ee3c121964a79142342c6b`.
- 검토한 Windows 조정 결과 ZIP: `c25f6c569771dd375dcc4f1a822037397eb34ce0e78d8a99acaa2ad8b9bde633`.
- 검토된 변경 파일 5개와 빌드 입력 해시가 일치한다. 변경 없는 142개 전체 검사를 반복하지 않았다.
- 15:33:21 KST 사용자 앱 종료 확인 후 기존 EXE를 백업하고 동일 위치에 교체했다. 새 설치 EXE SHA256은 `3d7b0daf780a3aa63d9cf06bc135f60f54c2830a7b48ace96c2bf0777dc365dd`, 79,306,850 bytes다.
- 핵심 모듈 9개의 패키지 코드가 검토 소스의 컴파일 결과와 일치했다(co_filename만 정규화). 빌드 및 설치된 EXE 모두 프로젝트·인증 초기화 전 Qt 시작 검사 종료 코드 0이었다.
- 실제 앱 프로세스는 설치 이후인 15:34:20~21 KST 시작했고 기존 설치 위치를 사용했다. 프로세스 경로·시각과 설치 파일 해시를 확인했으며, 실행 메모리 전체를 덤프해 검증한 것은 아니다.

## 실제 앱의 비송신 조회

사용자 제출 파일명은 안내 예시와 다른 `windows-contract-readiness.json`이지만, 내용의 새 진단·관찰 시각·고정 식별자가 맞아 이번 결과로 대조했다. 원본 파일을 바이트 그대로 보존했다.

- 관찰 시각: **2026-09-07 15:34:46.089994 KST**.
- JSON SHA256: `3c8a6b15fed4035845bf324c3dc458bc3596c51ec4b295e895100121d0fece22`.
- kind: `reviewed_contract_readiness_observation`, format_version 1.
- 조건: 기존 10개와 새 `pull_idle`을 포함한 **11개 모두 true**. failed_conditions는 빈 목록이다.
- gate_open=false, active_server_work_count=0, stale=false, context_changed_since_observation=false.
- authority의 accepted/allowed/identity_matches/project_active/project_context_matches는 모두 true다.

| 새 coordination 필드 | 결과 |
|---|---|
| authority_state / authority_reason | contract / accepted |
| transition_sequence / write_epoch | 7 / 7 |
| context_generation / transition_generation | 1 / 1 |
| auth_generation / transition_auth_generation | 0 / 0 |
| pulling / pull_pending | false / false |
| pull_worker_present / pull_worker_current | false / false |
| review_preparing | false |

이는 실제 설치 앱에서 새 진단 형식으로 조회·내보내기가 작동하고, 해당 순간 수신이 진행 중이거나 보류돼 있지 않았음을 보여 준다. 전환 순번 7은 메모리 기록 순번이며 복구 회차나 HTTP 횟수가 아니다. 이 관찰은 전체 서버 기준 검증이나 새 서버 원장 RPC 증명이 아니며, 실제 동시 실행을 다시 시험한 결과도 아니다. 과거 중단 순간의 호출자는 여전히 확정하지 않는다.

## 원본·두 중단 기록·관문 보존

설치 전, 설치 직후, 이번 조회 후의 immutable 읽기 전용 선별 상태를 대조했다. **protected_state 전체가 동일**하다. 각 조회는 WAL 부재 및 조회 중 DB 크기/수정 시각 불변을 확인했고 제품 store를 초기화하지 않았다.

- 원요청 bytes 및 원본 준비 전체 행 SHA256 `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4` 유지.
- 최초 stopped 실행 전체 행 SHA256 `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4` 유지. 최초 실행은 stopped/HTTP 0이다.
- 별도 회차 `http0-074b0dc9-007c-4f0e-8516-a84804850e77`의 **전체 행 해시 및 사건 해시**도 세 시점 모두 동일하다. raw approval/owner token은 내보내지 않았다.
- 별도 회차 1개, stopped/HTTP 0, 사건 4개, 영수증 0개, 진단 3개 유지.
- Windows 관문 열림 **0/15**, schema **8011**, contract_batches 0개 유지. 기존 폴더/문서 선별 메타데이터·원고 순서·큐 ID 및 사건도 동일하다.
- 이번 결과 확인에서 서버 RPC/SELECT, 원고 본문 읽기, 새 회차 생성, 송신, 관문 setter를 호출하지 않았다. 이전 서버 0건 자료를 이번 시점의 새 서버 조회로 표현하지 않는다.

고정 작품은 최종검증03 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`, batch는 `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256은 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`다. 빈 3권 생성/base 0과 기존 원고 순서 갱신/base 1의 원요청을 유지한다.

## 현재 가능한 다음 단계

비송신 설치·관찰 단계는 완료됐다. JSON의 execution_authorized=false, local_candidate=false, receipt_status=null을 유지한다. 이미 소모된 별도 복구 회차를 되살리는 정책이나 추가 실행 경로는 구현하지 않았다. `already_attempted=false`는 HTTP 시도 부재이며 회차 재사용 허용이 아니다. 조건 11개 충족만으로 복구 버튼을 다시 누르지 않는다.

**사용자는 `windows-pull-review-install-result-20260907.zip`을 iPad 측으로 보내고 설치 식별·새 진단·원본 및 두 중단 기록 보존의 대조 회신을 받아 주세요.** 다음 재개가 필요하다면 기존 두 기록을 보존하는 정책을 먼저 별도 설계안으로 검토해야 한다. 지금 앱에서 누를 추가 버튼은 없으며 양쪽 관문은 계속 닫아 둔다.

ZIP에는 실제 사용자 JSON, 설치/빌드 식별, 패키지 코드 포함 확인, 실제 프로세스 메타데이터, 세 시점의 보존 근거, 읽기 검증 및 포장 스크립트와 manifest를 포함한다. EXE·전체 DB·원고 본문·인증 토큰은 포함하지 않는다. `installation.json`의 actual_app_readiness_observation_performed=false는 설치 직후의 역사적 기록으로 보존하며, 이번 실제 관찰 완료는 별도 `verification.json`에 기록했다.
