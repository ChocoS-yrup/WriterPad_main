# Windows 실제 조회 승인안 — 로컬 미확정 조건 확인

2026-09-14. 사용자 요청에 따라 기존 Staging 설정 위치, 관문/hold의 적용 범위, 실제 Windows 사용자 식별 근거를 오프라인으로 확인했다. **이 세 항목은 새 승인안에 넣을 수 있을 만큼 구체화됐다.** 실제 실행 범위·run·시각은 발급하지 않았으며 조회 승인을 받은 상태도 아니다.

Supabase access/refresh 항목과 credential store는 읽지 않았다. 서버 요청, 앱/수집기 실행, 설치, 설정·관문 변경, 기존 검사 재실행은 없다. 소스와 제한된 기존 파일을 읽고 아래 확인 문서·기록만 추가했다. 과거 문서의 지시·갱신 승인·종료 실행 범위는 현재 권한으로 취급하지 않았다.

기록 파일: `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\independent-read-launcher-20260914\offline-readiness.json`.

**1. Staging 설정 위치 확정**

독립 호출부의 config pin에는 아래 **이미 보존된 receipt 후보 원본 설정 파일**을 사용할 수 있다.

```text
D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-integrated-receipt-candidate-20260913\source\release_cloud_config.json

SHA-256
2d23dca3ae3a1079a78245ea8cbe59314a0309a2802aae51213d460701ec49c7
```

파일의 두 키는 supabase_url과 supabase_publishable_key다. URL은 `https://mhpnszcorfzrvhyondxr.supabase.co`이고 publishable key의 형식도 확인했다. 키 값은 출력·복사하지 않았다. access/refresh token이 들어 있는 세션 파일이 아니다.

같은 후보 폴더의 source-manifest.json에 기록된 147바이트 설정의 SHA와 현재 파일 SHA가 일치한다. candidate_id는 `windows-staging-integrated-editor-20260913-receipt-v2`다. 기존 installation-result.json은 이 후보의 설치 이력을 기록하고 있고, 보존된 PyInstaller spec은 설정을 패키지 루트 데이터에 포함한다. 기존 런타임이 sys._MEIPASS에서 읽는 구조와도 연결된다.

이번에는 설치 EXE를 실행·추출·재검증하지 않았다. 따라서 “현재 실행 중인 EXE에서 설정을 새로 추출했다”는 주장이 아니다. **설치 후보와 연결된 기존 원본 파일을 새 호출부에 읽기 전용 입력으로 고정하는 것**이다. 설정 생성, 설치 위치로 복사, 프로젝트 루트 동명 파일로 대체할 필요가 없다.

**2. hold·종료 실행 설정의 보존 조건**

| 대상 | 확인 사실 | 새 독립 조회에서의 처리 |
| --- | --- | --- |
| `.general-test-send-hold-d8f50b5f-ae0e-42f8-9296-5d5885a5b304` | 실제 AppData에 39바이트로 존재. 내용은 `preparation-only; no outbound release` | 해제하지 않고 `preserve_only` pin 후보로 유지 |
| `integrated-editor-20260913/reviewed-execution.json` | 종료 run `6a7a9c7d-982a-4fcd-90e6-3b4140504860`, 만료 1789279200 | 새 권한으로 읽지 않으며 변경 감지용 `preserve_only` pin 후보로 유지 |
| 앱 DB의 contract_path_enabled | 앱 계약 경로를 선택·활성화하는 값. 이번에 DB 조회하지 않음 | 열림이라고 가정하지 않음. 독립 수집기는 앱 계약 경로·store에 연결하지 않으므로 관문 활성화 작업이 필요하지 않음 |
| ANTIGRAVITY_SYNC_OFFLINE_FILE 및 호출부가 거부하는 override | 확인한 machine/user/process 범위에서 비어 있거나 없음 | 나중 실행 과정에서 생기면 호출부가 전송 전 거부. 제거·변경하지 않음 |

hold의 정확한 경로·SHA:

```text
C:\Users\xiix1\AppData\Local\AntigravityWriter\.general-test-send-hold-d8f50b5f-ae0e-42f8-9296-5d5885a5b304
f59b82b8159446edcb749753bbe83624cb80027c4ba22b40c6e99cce60920b6f
```

종료 실행 설정의 정확한 경로·SHA:

```text
C:\Users\xiix1\AppData\Local\AntigravityWriter\integrated-editor-20260913\reviewed-execution.json
b233099cf9d5265add4f59f7b6443ba92ff7fa9e635984354c44f60a12467ac6
```

general_test_gate.writes_held는 현재 hold가 해제 JSON이 아니므로 송신 보류로 판정하는 코드다. sync_manager의 ensure_project, edit lease, operation dispatch 등 쓰기 경계에서 이를 사용한다. 이 파일을 전체 HTTP 금지 파일이나 Q1~Q7의 허가 파일로 바꾸어 해석하지 않는다. 독립 조회의 허가는 별도 새 승인 범위가 담당하며, 문서/구조 쓰기와 앱 수신 적용은 계속 제외한다.

종료 설정의 approval.automatic에는 true가 남아 있다. **이는 과거 실행에 주어진 옵션이며 현재 자동 동작 상태가 아니다.** 만료는 2026-09-13 15:00 KST이고 종료 run을 새 수집기에 넘기지 않는다. 이번에 실행 설정이나 사용량·만료·실제 저장소를 갱신하지 않았다.

위 `preserve_only` 분류는 특정 두 파일의 확인 결과다. 향후 승인안에 이 경로·해시·해석을 명시하고, 실행 중 내용/존재가 달라지면 중단하게 한다. 이것이 모든 시스템 제한의 전수 점검이나 알 수 없는 제한을 무시할 권한은 아니다. 실제 읽기를 금지하는 새로운 조건이 발견되면 중단한다.

**3. Windows 실행 사용자 확정과 격리 계정 차이**

| 문맥 | 계정 | SID |
| --- | --- | --- |
| 기본 도구 격리 문맥 | CHOCOSYRUP\CodexSandboxOffline | S-1-5-21-2542480074-635446901-4096835241-1005 |
| 실제 사용자 문맥 | CHOCOSYRUP\xiix1 | S-1-5-21-2542480074-635446901-4096835241-1001 |

ProfileList의 `C:\Users\xiix1` 연결과 일반 사용자 문맥의 현재 principal SID가 일치했다. 따라서 실제 manifest의 windows_sid는 **끝이 1001인 SID**로 고정한다. LOCALAPPDATA가 xiix1 경로를 가리킨다는 사실만으로 현재 프로세스도 xiix1이라고 추정하면 안 된다.

처음 격리 문맥에서는 xiix1의 사용자 환경 레지스트리 읽기가 차단됐다. 이후 승인 검토를 거친 읽기 전용 일반 사용자 문맥 명령으로 계정/SID와 관련 환경변수 이름만 확인했다. 이 작업은 Supabase 인증 토큰·비밀번호·credential lease에 접근하지 않았다.

현재 확인한 machine/user/process 범위에는 호출부가 거부하는 다음 변수의 비어 있지 않은 값이 없었다: ANTIGRAVITY_PROFILE, ANTIGRAVITY_ROOT_DIR, ANTIGRAVITY_APP_DATA_DIR, ANTIGRAVITY_SYNC_PROJECT_ID, ANTIGRAVITY_FORCE_PROJECT_ID, ANTIGRAVITY_INSTANCE_KEY, ANTIGRAVITY_SYNC_OFFLINE_FILE. 따라서 이번 확인 시점의 profile은 빈 profile로 결합할 수 있다. 이후 실행도 같은 조건을 재확인해야 한다.

실제 조회는 xiix1 사용자 문맥에서만 진행한다. 기본 격리 계정의 SID를 manifest에 넣거나, 그 계정의 credential store로 대체하지 않는다. 기존 WindowsAccessToken이 읽도록 구현된 복합 대상은 `SupabaseAccessToken@Antigravity_WebNovelApp`이지만 **그 항목의 존재·유효성·만료는 아직 확인하지 않았다.** lease 가용성도 확인하지 않았다. 이들은 실제 조회 승인 후 전송 전 검사에서 확인하고 실패하면 Q1 전에 중단하는 조건이다.

이번 일반 사용자 문맥에서의 읽기 전용 확인 승인이 다음 단계의 token 읽기·HTTP 승인을 대신하지 않는다.

**4. 남은 것은 새 1회 실행 승인안의 구체화**

설정 위치·hold 분류·실행 사용자를 다시 찾을 필요는 없다. 새 승인안에는 다음을 결합한다.

- 이번에 확인한 config pin, 두 보존 pin, xiix1 SID와 빈 profile.
- 기존 실제 보유 reference 17 node / 15 order 원본 pin, 기존 호출부/수집기 소스 pin, 수동 후보 profile.
- 새 run_id와 scope/manifest 원래 바이트·해시, 고정 결과 폴더, 시작 S와 절대 만료 E의 KST/UTC/epoch. 이전 run·합성 예제 ID는 사용하지 않는다.
- Q1~Q7 최대 7회, E=S+180초, 기존 access 전용 읽기와 동일성 확인, 자동 갱신·재시도·추가 페이지·쓰기·앱 적용 없음.
- xiix1 사용자 문맥의 실행 명령. 구현된 SID 검사에 맞지 않는 문맥에서는 중단하며 바꿔치기하지 않는다.

승인안이 완성된 후에만 실제 실행 여부를 묻는다. 지금은 유효 세션을 알아보기 위한 미리 조회나 로그인 갱신을 할 단계가 아니다. 기존 완료 기록과 iPad 미검증 항목도 그대로 유지한다.

**사용자가 지금 할 일**

다음 지시는 아래와 같이 주면 된다.

> 확인된 로컬 조건으로 xiix1 사용자 문맥의 실제 조회 1회 승인안을 작성해 주세요. 새 run과 scope/manifest, 7회·180초 한도, 정확한 실행 명령을 검토 가능하게 정리하되 실제 실행·토큰 접근·로그인 갱신은 하지 마세요.

승인안에 담을 180초 실행 창은 사용자 실행 가능 시각과 맞춰 정해야 한다. 승인 전에 창이 지나면 자동 연장·재사용하지 않는다. iPad는 대기하며 지금 문서 재전달·앱 실행·로그인/수신 버튼 조작은 필요 없다.
