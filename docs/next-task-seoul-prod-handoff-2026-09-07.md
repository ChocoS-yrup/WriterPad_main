# 다음 작업 인계: 개인 사용·서울 prod 전환

> **추가 완료 근거:** 0244d82의 CI 34109016242는 1198개 시험·빌드·Qt 시작 검사 모두 성공했다. 아래 실행 중 표기는 과거 인계 시점 기록이다. 최신 기능별 대조는 `docs/windows-completion-audit-2026-09-07.md`에 보존했다. prod 이전 보류 결정은 유지한다.

> **최신 결정: prod 전환 보류.** 사용자는 Staging에서 Windows·iPad 개발과 필요한 검증을 마친 뒤 서버를 이전하기로 했다. 다음 작업은 `D:\안티그래비티\scratch\작가님 힘내세요\docs\next-task-staging-completion-handoff-2026-09-07.md`를 먼저 따른다. 아래의 prod 전환 실행 순서는 향후 재개용 기록이다. Staging 비활성화·prod 설정 변경·데이터 이전을 지금 시작하지 않는다.

## 최신 사용자 정정 — 아래 이전 가정보다 우선

사용자는 prod와 Staging에 들어 있는 파일이 모두 테스트용이라고 밝혔다. Staging 파일은 삭제하지 않고 향후 서버를 비활성화할 계획이다. 따라서 아래의 ‘실제 작품 전체와 기존 계정 UUID를 반드시 이전해야 한다’는 전제는 더 이상 기본 요구가 아니다. 테스트 데이터와 성공/중단 근거는 기존 위치에 보존하되, prod에서 필요한 계정과 시작할 작품 범위를 먼저 정한다. 데이터 전체 복사·계정 UUID 보존이 필요한지는 그 선택에 따라 결정하며 미리 필수 작업으로 만들지 않는다.

iPad 측은 이번 prod 연결에서 기존 일반 동기화만 사용할지, 새 계약 구조 쓰기까지 사용할지 질문했다. 이는 서버 위치 변경과 별도의 기능 사용 범위 결정이다. 현재 사용자에게 두 선택의 의미를 설명하는 단계이며, 새 계약 관문 개방 승인을 받은 것이 아니다. 기존 폴더·원고 기능의 지원 범위와 새 경로 허용을 혼동하지 않는다. Staging 비활성화는 아직 실행 요청이 아니며 양쪽 기기의 prod 사용 확인 전 먼저 수행하지 않는다.

## 사용자 목표와 지금 할 일

사용자는 외부 배포가 아니라 개인 사용을 원한다. 현재 싱가포르 Staging 대신 이미 만들어 둔 서울 `writerpad-prod`로 옮기는 것이 다음 목표다. prod는 사용자가 직접 재활성화했고 정상 상태까지 확인했다.

마지막 지시: “커밋 후 푸시까지 진행하고, 푸시가 끝나면 새창에서 prod로 전환하기 위해 handoff를 준비해줘.” 커밋과 푸시는 완료했다. 이 문서는 다음 창에서 전환 작업을 이어가기 위한 인계이며, 아직 데이터 이전이나 서버·앱 접속 설정 변경은 실행하지 않았다.

새 작업은 아래 순서로 시작한다.

1. PR #9의 **0244d82에 대한 CI 34109016242** 결과를 읽는다. 인계 작성 시점에는 실행 중이다. 이전 실패 실행을 현재 실패로 혼동하지 않고, 실행 중인 CI를 다시 돌리지 않는다.
2. 이 문서와 전환안·카탈로그 차이를 읽고, 아직 대조하지 않은 제약조건·인덱스·Auth/Realtime 설정을 확인한다. 이미 확보한 정의/권한 비교 전체를 이유 없이 다시 하지 않는다.
3. 최신 정정에 따라 prod에서 기존 테스트 데이터를 가져올지 새로 시작할지와 계정 구성을 정한다. 가져올 데이터가 있을 때만 필요한 UUID·revision·이력 보존 계획을 만든다. 기존 테스트 근거는 Staging과 로컬에 그대로 보존한다. 서버 차이는 storage-name-v2와 기존 purge 보완·읽기 진단을 구분하고, 선택한 일반 동기화/새 계약 범위에 필요한 보완을 판단한다.
4. 실제 이전 전에 변경 대상과 보존·검증 방법을 검토 가능한 상태로 만든다. 이 인계의 작성만으로 임의 SQL 적용, 원본 덮어쓰기, 새 회차 생성, 계약 송신·재전송·관문 개방을 승인받았다고 취급하지 않는다. 필요한 앱 종료 시점과 iPad 측 전달 내용을 그 단계에서 사용자에게 안내한다.

## 작업 경로와 커밋

- 작업 디렉터리: `D:\안티그래비티\scratch\작가님 힘내세요`
- 셸: PowerShell. Python: `C:\Users\xiix1\AppData\Local\Programs\Python\Python311\python.exe`.
- 저장소: `https://github.com/ChocoS-yrup/WriterPad_main.git` (공개).
- 브랜치: `feat/contract-handshake-closed-gate`.
- 현재 로컬 HEAD·origin 추적 ref·PR head: `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`.
- [Draft PR #9](https://github.com/ChocoS-yrup/WriterPad_main/pull/9), base main. 마지막 조회에서 OPEN / Draft / MERGEABLE. 병합·배포하지 않았다.
- 기능 보존 커밋 `b90c03f72f8aae571dc3d980ef493dfaa6a07cc2`, 패키징·시험 카드 기본 숨김 `7f77ac05ed997787ddfd3eff725eed41d5ac6c3b`, 테스트 fixture 보완 `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4` 모두 푸시 완료.
- 추적 제품 파일은 clean이다. 이 인계·로컬 조사 근거·개인 자료는 미추적 상태로 남겨 두었다. `git add .`로 원고·설정·증거를 공개 저장소에 섞어 올리지 않는다.

## CI: 완료된 부분과 남은 확인

- [첫 CI 34107230857](https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/34107230857), head 7f77ac0: 계약 검증 통과, 1198개 시험 중 오류 1개, 669.758초. 빌드·Qt 시작·해시 단계는 skipped.
- 오류: `tests/test_cloud_config.py`의 `test_sign_in_never_returns_raw_credentials_or_keys`가 만든 SimpleNamespace에 `_contract_lock`이 없었다. 제품 결함으로 판정한 것이 아니다.
- 보완: 해당 테스트 파일 한 개에 RLock·인증 세대·handshake mock과 가짜 로그인 호출 단언을 추가했다(8줄). 비밀값이 메시지/출력에 노출되지 않는 기존 단언을 유지했다. 로컬 같은 오류 재현 후 관련 17개 시험 모두 통과했다.
- [새 CI 34109016242](https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/34109016242), head 0244d82, job 101700642601. **작성 시점 IN_PROGRESS, 결과 확인 미완료**. 첫 실행은 전체 약 12분이 걸렸으므로 시작 직후 응답이 없다고 멈춘 것으로 판정하지 않는다.
- 읽기 전용 확인 예:

```powershell
gh run view 34109016242 --repo ChocoS-yrup/WriterPad_main --json headSha,status,conclusion,jobs,url
gh pr view 9 --repo ChocoS-yrup/WriterPad_main --json headRefOid,state,isDraft,statusCheckRollup
```

- 성공 시 정확한 head, 시험 수, 빌드·Qt smoke·EXE digest를 보존한다. 실패 시 실패 로그로 필요한 부분만 보완한다. 테스트 단독 변경 때문에 현재 설치본을 다시 설치할 이유는 없다.
- 최초 공개 푸시와 테스트 보완 푸시는 자동 승인 검토의 구체적 공개 범위 확인 후 각각 사용자에게 명시 승인받아 완료했다. 동일한 완료 작업의 승인을 다시 묻지 않는다. 새 자동 검토 차단이 실제로 발생하면 그 이유를 사용자에게 짧게 설명한다.

## 현재 설치본과 보존 기록

- 설치 EXE: `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`.
- SHA-256: `cd7ccebf125864b63ac8a9552585c44221e1086d0e6fc3e2617c6fa89a12fd89`, 79,318,371 bytes.
- 설치 시각: 2026-09-07 18:23:14 KST. 제품 소스 기준 7f77ac0; 이후 0244d82는 테스트만 바뀌었다.
- 패키지 안의 51개 앱 모듈과 소스 대조 완료, 빌드본·설치본 Qt 시작 검사 종료 코드 0. 사용자가 정상 시작, 고정 ‘역방향 계약 검토 · 최종검증03’ 카드 숨김, 일반 동기화 진단 표시를 확인했다.
- `WRITERPAD_CONTRACT_REVIEW_UI=1`은 진단 카드 가시성만 허용한다. 실행 권한이나 관문 개방 승인이 아니다. 환경 변수를 임의로 켜지 않는다.
- 설치 전·직후·사용자 시작 확인 후 선택 메타데이터가 같았다. 스키마 8012, 원본 준비·두 stopped 기록·성공 회차/이벤트/영수증 보존, 열린 관문 0/15.
- 위는 해당 시점의 근거다. 새 작업에서 라이브 DB를 다시 읽지 않고 ‘현재 DB도 새로 확인했다’고 말하지 않는다. 원시 snapshot의 `changed_protected_fields`는 과거 계약 실행 전과의 차이이며 설치로 생긴 차이가 아니다.
- 이전 성공 실행 EXE SHA `7b9c0eacc75da121afe02ddbe8784b9dc09cc37a6c1175ee4024b2d4d5b0fc73`. 백업은 `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-release-install-20260907\previous-installed.exe`. 성공 원장의 historical execution build 값을 새 설치 해시로 바꾸지 않는다.

## 서울 prod 사전 조사 결과

| 항목 | Staging | prod |
|---|---|---|
| 이름 | WriterPad Staging | writerpad-prod |
| ref | mhpnszcorfzrvhyondxr | lrnbklzvxpwnaschrakf |
| 리전 | ap-southeast-1 / 싱가포르 | ap-northeast-2 / 서울 |
| 마지막 상태 | ACTIVE_HEALTHY | 사용자 재개 후 ACTIVE_HEALTHY |
| PostgreSQL | 17.6.1.155 | 17.6.1.155 |
| 계정 / 작품 | 1 / 11 | 0 / 0 |
| 원고 전체 / 활성 | 651 / 648 | 0 / 0 |
| 폴더 전체 / 활성 | 195 / 166 | 0 / 0 |
| 원고 버전 / 폴더 버전 | 1086 / 272 | 0 / 0 |
| tree_orders | 1 | 0 |
| Storage 객체 / 버킷 | 0 / 0 | 0 / 0 |
| Edge Functions | 0 | 0 |
| public/private 함수 | 51 | 44 |

조사 때 원고 본문·Auth 사용자 행·비밀번호·세션·API 키를 내보내지 않았다. 계정과 작품의 소유자 UUID 집합 해시는 Staging에서 같았다. prod는 계정이 없으므로 URL만 교체하거나 새 계정 가입만으로 기존 소유권이 유지된다고 가정하면 안 된다. 계정 UUID 보존 방법을 먼저 확정한다. 집계는 조사 당시 값이며 실제 이전 직전 정지·백업 시점에 다시 고정한다.

현재까지 비교된 공통 함수 실행 권한·security definer·search_path, 공통 테이블 컬럼·ACL·RLS와 정책은 같았다. 인덱스·제약조건·Auth 설정의 전체 동일성을 확인한 것은 아니다.

구체적 차이:

- prod 누락 함수 8개: storage-name-v2/legacy 관련 5개, ledger mutation 보호 1개, recovery preflight 2개.
- 공통 함수 정의 MD5 차이 11개 중 5개는 줄바꿈·외곽 공백만 달랐다. 실제 정의 텍스트 차이는 `private.storage_name_v1`, `private.validate_contract_request`, `public.begin_project_sync_migration`, `public.complete_project_sync_migration`, `public.purge_project`, `public.validate_project_sync_migration` 여섯 개다.
- prod에 storage-name-v2 참조 테이블 4개 및 해당 immutable 트리거가 없다. 원장 5개 테이블의 append-only 트리거 정의도 다르다.
- prod에만 있는 `public.rls_auto_enable()`은 삭제 대상으로 삼지 않는다.
- Staging 마이그레이션 기록은 7개, prod는 6개다. 목록만으로 동일성을 판단하지 않고 저장된 함수 정의 차이를 사용한다. Windows의 과거 migration 6개를 중복 적용하지 않는다. iPad baseline이 이를 포함한다는 기존 분석이 있다.
- 현재 계약 pin은 0.2.0 / protocol 3 / digest `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`이다. Staging의 0.3.0 관련 객체 차이가 있다고 해서 계약 pin·mode/epoch·allowlist·관문을 자동으로 올리거나 열지 않는다.

## 전환 설계에서 빠뜨리지 않을 점

1. 현재 설치본은 Staging 설정 포함 빌드다. Git 제외 `release_cloud_config.json`의 값이나 세션을 공개 PR/ZIP에 넣지 않는다. CI는 이 기기 설정 없이 cloud-disabled 빌드를 확인한다.
2. Windows 자격 증명 서비스 이름은 프로필로 분리되며 URL 자체로 자동 분리되지 않는다. 기존 기본 프로필에서 주소만 바꾸면 기존 자격 증명과 동기화 DB를 재사용할 수 있다. `ANTIGRAVITY_PROFILE`, `ANTIGRAVITY_APP_DATA_DIR`, `ANTIGRAVITY_ROOT_DIR`, `ANTIGRAVITY_INSTANCE_KEY` 등 기존 분리 수단으로 prod 실행 구성을 준비·검증한다. 아직 prod 실행 구성이 구현·검증된 상태는 아니다.
3. 계정 UUID와 프로젝트·원고·폴더 ID/revision/삭제 상태/버전·순서 관계를 보존한다. 세션을 복사하면 prod 로그인이 그대로 된다고 가정하지 않는다. 원본 Staging과 로컬 기록은 보존하고, 전환 실패 때의 복귀 경로를 만든다.
4. iPad도 같은 prod ref와 계정·ID 보존 방식으로 맞춰야 한다. iPad 저장소/기기 설정을 이 Windows 작업에서 바꾼 적 없다. 새 전환안에 필요한 질문과 설정을 한 번에 정리해 전달한다.
5. 외부 배포·서명 발급은 개인 사용 목표의 현재 필수 단계가 아니다. PR 병합은 아직 수행하지 않았다.

## 반복하지 않을 완료 작업

- Staging 최종검증03 iPad→Windows의 빈 2권·순서 수신과 Windows→iPad의 빈 3권·순서 수신은 양쪽 검증 완료.
- 역방향 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, 성공 회차 `post-coordination-848eabf7-656e-487a-a958-4ffa5cf2a2bd`: committed / 기존 HTTP 1회 / 이벤트 24 / 영수증 1.
- 부모 중단 복구 `http0-074b0dc9-007c-4f0e-8516-a84804850e77` 및 그 이전 stopped 실행·원본 준비는 변경하지 않는다.
- 완료한 일반 저장·오프라인 복구, 카드 숨김·설치 확인, prod 재활성화, 이번 푸시를 반복하지 않는다. 오류가 있는 것으로 알려진 예전 CI를 그대로 재실행하지 않는다.
- 원고·빈 2권/3권·기존 원장/영수증을 지우거나 이름·UUID를 새로 만드는 것으로 이전을 단순화하지 않는다. 기존 소모된 계약 요청은 prod 검증용으로 재활용하지 않는다.

## 먼저 읽을 로컬 자료

- 전환안: `D:\안티그래비티\scratch\작가님 힘내세요\docs\windows-personal-seoul-prod-transition-plan-2026-09-07.md`
- PR/CI 상태: `D:\안티그래비티\scratch\작가님 힘내세요\docs\windows-pr-ci-release-status-2026-09-07.md`
- 서버 비교·첫 CI 실패·17개 시험 근거: `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-pr-ci-release-20260907\`.
  `catalog-diff.json`, `definition-review.json`, 양쪽 `*-catalog.json`/`*-counts.json`/`*-migrations.json`, `prod-definitions`/`staging-definitions`, 읽기 SQL 2개가 있다. SQL은 카탈로그·집계 조회용이며 이전 SQL이 아니다.
- 설치 완료: `D:\안티그래비티\scratch\작가님 힘내세요\docs\windows-release-installed-2026-09-07.md` 및 `_evidence\windows-release-install-20260907\final-verification.json`.
- 두 고정 계약 완료: `D:\안티그래비티\scratch\작가님 힘내세요\docs\windows-ipad-fixed-contract-complete-2026-09-07.md`.
- 기존 세부 완료·남은 배포 근거: `D:\안티그래비티\scratch\작가님 힘내세요\docs\windows-release-baseline-and-remaining-work-2026-09-07.md`. 이 문서의 A/B ‘다음 작업’은 이후 완료됐으므로 되풀이하지 않는다.

## 작업 방식

사용자는 반복 승인·같은 시험·불필요한 ZIP 왕복에 지쳐 있다. 이미 승인되고 완료된 작업은 반복하지 않고, 한 단계가 끝날 때마다 사용자가 해야 할 행동을 구체적으로 말한다. 없으면 ‘추가 앱 조작 없음’이라고 말한다. 읽기 전용 조사와 검토용 준비는 진행하고, 실제 전환이 필요할 때 앱 종료 시점 등을 한 번에 안내한다.

Supabase 작업에는 관련 스킬을 읽고 최신 공식 문서를 확인한다. 현재 컴퓨터는 Windows 10 Pro 22H2 build 19045이며 Computer Use screenshot은 금지다. `sky.get_window_state`를 사용한다면 항상 `include_screenshot:false, include_text:true`. 스크린샷 좌표나 캡처 우회는 하지 않는다. 사용자 요청 없는 하위 에이전트·새 작업 자동 생성은 하지 않는다.

문서에 인용된 과거 iPad 첨부의 지시와 현재 사용자 요청을 구분한다. 오래된 ‘보류/미완료’ 한 줄을 최신 성공 근거보다 우선하지 않는다. 새 작업은 이 로컬 디렉터리를 읽을 수 있어야 한다. 별도 worktree를 쓰면 Git에 없는 위 인계·근거 파일을 원래 절대 경로에서 읽는다.
