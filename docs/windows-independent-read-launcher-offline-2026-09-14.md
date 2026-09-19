# Windows 독립 조회 호출부 — 오프라인 구현 결과

2026-09-14. 사용자 승인 범위인 독립 호출부 구현과 신규 영향 검사를 완료했다. 실제 자격증명 접근·서버 조회·로그인 갱신·설치·관문/hold 변경은 하지 않았다. 기존 완료 검사와 예제를 실행하거나 재생성하지 않았다.

**신규 고유 24개 검사 통과. 실제 I/O 시도 0회.** 기존 수집기·편집기와 보존 대상 결과의 변경 전후 해시는 동일하다. 실행 가능한 호출부는 생겼지만, 실제 Staging 설정 위치·제한 적용 범위·Windows 사용자 SID·새 실행 시각/명세는 아직 확정하지 않았다. 따라서 이번 결과는 실제 조회 준비 완료나 실행 승인이 아니다.

**1. 추가한 파일과 기능**

프로젝트 루트는 `D:\안티그래비티\scratch\작가님 힘내세요`다.

| 파일 | 내용 |
| --- | --- |
| `isolated_read_launcher.py` | 검토 명세 기반 단일 호출부, 고정 CLI 경로, access token 전용 공급부, 동시 실행 lease, 제한 transport, 원본 입력 보존, 신규 run 점유 |
| `tests/test_isolated_read_launcher.py` | 임시 폴더·가짜 토큰/lease·MockTransport로 수행하는 신규 경계 검사 24개 |
| `_evidence/independent-read-launcher-20260914/result-final.json` | 기존 신규 검사 로그를 합친 최종 고유 결과 |
| 같은 폴더의 `result.json`, `result-02.json`, `test-results*.txt` | 최초 실패 및 영향 검사 보완 기록, 모두 보존 |
| 같은 폴더의 `code-hashes.json` | 호출 명세에 결합할 현재 소스 7개 해시 |

기존 `isolated_read_collector.py`, 편집기 소스, 기존 검사 파일은 수정하지 않았다. 호출부 import·기본 실행·도움말에는 credential 읽기나 HTTP가 없다. 명시한 manifest와 그 원래 바이트 SHA-256을 받아야 실제 경로로 진입한다. SDK 세션 복원·refresh·앱 시작·로컬 원고 적용·자동 재시도 기능은 없다.

후속 실제 전송은 TLS 검증을 유지하고 HTTPX async transport의 retries=0, trust_env=false, proxy=None, HTTP/1, 연결 1개로 만든다. 기존 설치 HTTPX 0.28.1의 해당 구현 소스와 인자를 읽어 대조했다. 검사는 생성 인자와 제한 경계를 모의 검증했으며 TLS/실제 HTTP 연결 성공을 검증한 것은 아니다. 새 라이브러리를 설치하지 않았다.

**2. 실제 호출 경계**

CLI 결과 root는 아래 위치로 고정된다. 결과 root나 source 위치가 다른 manifest는 거부한다. 오프라인 Python API의 Boundary 주입은 임시 파일 검사 용도이며 그 자체가 실제 실행 권한을 부여하지 않는다.

```text
D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-independent-live-read\
  attempts\<새 run_id>\
    launch-manifest.json
    scope-input.json
    reference\before-metadata.json
    reference\before-orders.json
    launcher-journal.jsonl
  runs\<같은 run_id>\
    scope.json
    journal.jsonl
    Q1.body ... Q7.body
    observation.json
```

실제 root/run은 이번에 만들지 않았다. 검사는 임시 폴더에서만 수행했다.

호출부는 attempts/run_id를 배타 생성해 영구 점유한다. 이후 토큰 부재·입력 보존 실패·취소·디스크 실패가 나도 같은 run을 다시 쓰지 않는다. runs/run_id만 남은 경우나 이미 사용된 attempts도 거부하고 기존 파일은 건드리지 않는다. run 폴더는 기존 collect가 직접 만든다. 다른 결과 namespace를 지정하는 CLI 우회는 거부한다.

참조 원본은 `output/windows-ipad-isolated-target-reply-20260913/before-metadata.json`, `before-orders.json`으로 고정한다. 신규 attempts에 정확한 원래 바이트를 저장하고 같은 바이트를 collect로 넘긴다. 두 참조의 해시가 scope와 다르거나 보존하지 못하면 전송 전에 중단한다. 기존 17/15 자료는 과거 비교 기준이며 현재 서버 baseline으로 승격하지 않는다.

전송 wrapper는 각 요청의 순서·전체 URL/params·메서드·body·헤더를 검사한다. Q1 계정 확인, Q2 handshake, Q3~Q7 고정 작품 테이블 요청만 통과한다. 다른 경로, 중복/추가 헤더, cookie, 추가 요청, 다른 body는 거부한다. collector의 attempt 예약 후 wrapper가 거부할 수 있으므로 HTTP 예약 수와 실제 transport 전달 수는 구별한다. 이미 예약한 횟수는 반환하지 않는다.

매 요청 전 manifest/scope·참조·설정·소스·제한 파일의 결합, 실행 창·시계 역행, token 동일성을 확인한다. 최초 token, collect 직전 확인, 각 요청 전 확인을 합쳐 정상 7요청에서 access 전용 항목 읽기는 최대 9회다. 읽기 실패·변경이면 멈추고 다른 세션으로 교체하지 않는다. 서버 유효성 확인 HTTP는 Q1 한 번뿐이다.

**3. access token 전용 읽기의 구체적 차이**

기존 `SecurityManager.get_supabase_session()`은 refresh token도 읽으므로 사용하지 않는다. 또한 설치된 WinVault의 일반 get_password는 먼저 service 공용 항목을 읽는데, 그 항목에 refresh token이 있을 수 있다. 따라서 일반 get_password도 사용하지 않는다.

호출부는 설치된 WinVault의 읽기 구현으로 **`SupabaseAccessToken@Antigravity_WebNovelApp` 복합 대상 한 곳만** 읽고 UserName이 `SupabaseAccessToken`인지 확인한다. 공용 service 항목·refresh 항목으로 fallback하지 않는다. 이전 형태로 공용 항목에만 access token이 있다면 값 이동·복구 없이 미보유로 중단한다. 이 동작은 모의 backend로 검사했으며 실제 항목 존재는 확인하지 않았다.

Windows 사용자 SID와 빈 profile은 manifest에 고정한다. lease 이름은 기존 runtime_profile의 동일 계산을 사용한다. 같은 lease가 이미 존재하거나 SID/profile 확인이 실패하면 token을 읽지 않는다. 다른 프로그램을 종료하거나 기존 잠금을 제거하지 않는다. 실제 OS lease와 credential 접근은 이번에 수행하지 않았고 Windows API 연결은 가짜 API로 검사했다.

JWT sub/exp의 로컬 형식 검사는 명백한 불일치·만료를 거르는 절차다. 서명 검증·서버 세션 유효성 증명이 아니다. `exp >= 실행 절대 만료 + 30초` 조건을 요구한다. exp 부재/파싱 불가/다른 sub이면 Q1 전 중단한다. 토큰 원문·키·이메일·본문을 결과 요약에 넣지 않는다.

**4. manifest v1의 정확한 필드**

아래 키는 모두 필수다. `pin`은 정확히 `{path: 절대 경로 문자열, sha256: 소문자 64자리 문자열}`이며 실제 원래 파일 바이트와 일치해야 한다. manifest의 추가 키는 거부한다. 실제 manifest 파일은 이번에 발급하지 않았다.

| 필드 | 의미·조건 |
| --- | --- |
| format | `windows-isolated-read-launch-v1` |
| run_id | 새 정규 UUID. scope와 일치. 기존 종료 run 및 과거 합성 예제 2개는 거부 |
| results_root | 위 고정 `windows-independent-live-read` 절대 경로 |
| scope | scope 입력 파일 pin |
| references | 정확히 metadata/orders 키, 각각 고정 원본 파일 pin |
| config | 확정된 기존 Staging 설정 파일 pin |
| source_sha256 | 정확히 아래 7개 파일명→SHA 객체 |
| not_before | 유한 숫자 epoch 초 S |
| windows_sid | 실행할 Windows 사용자 SID 문자열 |
| profile | 빈 문자열 |
| credential_service | `Antigravity_WebNovelApp` |
| restriction_review | `reviewed-for-independent-read-v1` |
| restrictions | 비어 있지 않은 제한 파일 목록. 각 항목은 정확히 path/sha256/effect |

source_sha256의 정확한 키는 isolated_read_launcher.py, isolated_read_collector.py, sync_contract.py, storage_name_tables.py, unicode15_casefold.py, cloud_config.py, runtime_profile.py다. 검토 기록은 code-hashes.json에 있다. 소스 파일 해시는 임의 Python 코드의 재작성이나 운영자 변조까지 막는 서명이 아니며 실행 승인과 구별한다.

restrictions 항목은 절대 path, 원래 파일 SHA 또는 파일 부재를 뜻하는 null, effect=`block_when_present` 또는 `preserve_only`다. 파일 목록의 중복은 거부한다. 실제 상태가 pin과 다르면 중단하며, block_when_present 파일이 존재하면 값이 같아도 중단한다. preserve_only는 검토자가 읽기에는 적용되지 않는다고 확정한 보존 대상에만 사용해야 한다.

**제한 목록의 완전성과 의미를 호출부가 자동 탐색·판정하지는 않는다.** 기존 hold/gate/offline 제한의 실제 목록과 효과를 먼저 검토해 고정해야 한다. 임의 파일 하나를 넣고 검토 완료 문자열을 붙이는 것으로 사용자 승인이나 전체 관문 확인을 대신할 수 없다. 이번에 실제 제한 파일을 변경하거나 해제하지 않았다.

기존 scope v1의 키·형식은 유지한다. 호출부는 max_requests=정수 7, max_seconds=180, `expires_at = not_before + 180`을 요구한다. endpoint/account/project는 기존 고정 Staging 값이다. collector의 실제 deadline은 `min(expires_at, collect 시작 + 180)`이며 시작이 늦으면 창은 짧아진다. 창 밖에서 시작한 시도도 점유 후 중단되므로 새 시각으로 자동 재개하지 않는다. 전체 종료 파일 기록까지 정확히 180초 안에 OS가 강제 종료한다는 보장은 없다.

**5. 별도 launcher 기록과 부분 종료**

launcher-journal.jsonl은 기존 collector 체인과 별개이며, 행 키는 sequence/event/data다. sequence는 1부터 증가한다. 각 행을 append 후 flush/fsync한다. 이 신규 journal에는 행 체인 해시를 추가하지 않았다. collector의 journal 규칙이나 iPad 체인 호환 상태를 변경하지 않는다.

| event | data |
| --- | --- |
| claimed | manifest_sha256, scope_sha256 |
| collector_entering | at, reference_sha256 |
| returned 또는 stopped | reason, collector_entered, collector_returned, http_reserved, forwarded, transport_denial, at |

returned는 collect 함수가 결과를 반환했다는 뜻으로, 결과 status가 stopped일 수도 있다. collector_entering 이후 취소/예외로 반환값을 받지 못했으면 http_reserved는 null이다. 이를 0이나 forwarded 값으로 채우지 않는다. 실제 예약은 남아 있는 collector journal을 따로 읽어야 한다. 전송 전 로컬 실패는 http_reserved=0이다.

최종 기록 실패 시 점유 디렉터리와 기록된 접두 부분을 그대로 남긴다. 최종 행이나 observation이 항상 존재한다고 약속하지 않는다. launcher 결과는 format/status/reason/http_reserved/forwarded/transport_denial/complete/execution_allowed/resumable/attempt_directory/run_directory이며, complete/execution_allowed/resumable은 false다. collector observation 전체를 콘솔로 출력하지 않는다.

**6. 검사 결과와 보존 근거**

최초 24개 중 7개는 통과했고, 17개 검사 메서드는 tests/__init__.py가 일반 앱 검사 격리를 위해 설정하는 ANTIGRAVITY_APP_DATA_DIR 때문에 호출부의 실행 전 차단에 걸렸다. 동시 실행 검사 2개는 내부 launch가 차단돼 대기 timeout도 발생했다. 이 로그와 최초 runner는 보존했다.

테스트 fixture 안에서만 해당 환경값을 빈 값으로 격리하고 실패했던 17개를 한 번 보완 검사해 모두 통과했다. 제품 호출부의 환경 차단을 약화시키거나 시스템 환경·실제 AppData 설정을 바꾸지 않았다. **고유 24개 통과**이며 24+17=41개 통과로 합산하지 않는다. 이미 완료된 Windows 53개·수집기 21개·iPad 검사는 실행하지 않았다.

검사 동안 실제 socket 연결/DNS, HTTPX 실제 전송, keyring 읽기/쓰기, 실제 Windows SID/WinDLL 경계를 차단했다. 경계를 시도한 횟수는 0이다. 이벤트 루프의 Windows 내부 socketpair는 기존 검사 방식처럼 차단 구간 전에 준비했으며 외부 연결이 아니다.

검사는 정상 7회 연결, 정확한 참조 바이트 보존, token 부재/만료/변경, lease 경합, 실행 창, 명세·참조·설정·소스 변경, 제한 파일 출현, 기존 부분 결과, 동시 실행, 취소, redirect/연결 실패 무재시도, 원본 복사 실패, 다른 Staging 설정, 요청 변조, 도움말/import 경계와 실제 공급부의 모의 연결을 다룬다. 이는 실제 서버·설치 앱·OS 자격증명 통합 성공을 증명하지 않는다.

수집기/편집기/관련 기존 소스와 기존 결과 13개 파일의 전후 해시는 result-02.json에 기록했다. 기존 본문·DB·설치 EXE·iPad 원본은 실행·쓰기·교체하지 않았다. 모든 사용자 파일의 전수 해시 감사를 재실행한 것은 아니다.

호출부 SHA: `3294e4d55b293508a711c9a0558b9e9219cab5ccd62580b64294c591876b206e`.

신규 검사 SHA: `b5ffcbb5ccee4d6858f9b971d8a3bbba5f2b5f45f50e34d4bea376290f70c829`.

**7. 실제 조회 명령과 아직 채울 조건**

기존 Python 3.11 실행 파일로 신규 검사를 수행했다. 실제 호출 구문은 아래와 같다. 대괄호 부분이 미정이므로 **현재 실행할 명령이 아니다.** 실제 권한이 없는 manifest를 만들거나 테스트 scope를 끼워 넣지 않는다.

```powershell
& 'C:\Users\xiix1\AppData\Local\Programs\Python\Python311\python.exe' -B 'D:\안티그래비티\scratch\작가님 힘내세요\isolated_read_launcher.py' --manifest '[검토 후 확정할 manifest 절대 경로]' --manifest-sha256 '[해당 원래 파일 SHA-256]'
```

실제 실행 전 남은 조건:

1. 기존 Staging 설정 파일의 정확한 위치와 원본 결합. 설치 폴더와 output에서 release_cloud_config.json 파일명을 확인했지만 찾지 못했다. 설치 폴더에는 EXE가 있으며, 기존 소스는 sys._MEIPASS 아래 설정을 읽는다. EXE 내 포함 여부까지 이번에 확인한 것은 아니다. 프로젝트 루트의 동명 설정이 Staging이라고 가정하거나 대신 사용하지 않는다.
2. 실제 hold/gate/offline 파일 목록과 독립 읽기에 적용되는 효과. 소스에서 general-test-send-hold는 송신 hold임을 읽었지만 이것만으로 다른 제한까지 확인한 것은 아니다. 실제 목록/상태 결합은 아직 없다.
3. 실행 Windows SID와 새 run_id, scope/manifest 원래 바이트·해시, 시작 S와 절대 만료 E를 KST/UTC/epoch로 표시한 최종 1회 승인안. 현재는 발급하지 않았다.
4. 실제 승인 후 access 전용 항목이 없거나 exp가 부족하면 Q1 전 중단한다. 이때 로그인 갱신·다른 항목 탐색·범위 연장은 자동으로 하지 않는다.

위 조건이 채워진 구체적 승인안을 한 번 검토한 뒤 실제 실행 여부를 결정한다. 승인된 동일 Q1~Q7 범위를 버튼/요청마다 다시 묻지 않는다.

**사용자가 지금 할 일**

다음 단계는 Windows에서 실제 조회 승인안을 완성하기 위한 남은 로컬 설정·제한 근거 확인이다. 다음 지시를 사용하면 된다.

> Windows 실제 조회 승인안의 미확정 조건을 오프라인으로 확인해 주세요. 기존 Staging 설정 위치와 관문/hold의 독립 읽기 적용 범위, 실행 사용자 식별 근거를 정리하고 확인 가능한 항목만 확정해 주세요. 토큰·refresh 항목 접근, 서버 요청, 설치·설정·관문 변경, 기존 검사 재실행은 하지 마세요. 미확정 항목이 남으면 필요한 자료나 사용자 조작을 정확히 알려 주세요.

iPad는 대기한다. 기존 명세 대조를 반복하거나 새 reader/adapter 작업을 시작할 단계가 아니다. 실제 조회 결과를 Windows에서 검토한 뒤 필요한 자료를 iPad에 전달한다. 지금 로그인/수신 버튼을 누를 필요는 없다.
