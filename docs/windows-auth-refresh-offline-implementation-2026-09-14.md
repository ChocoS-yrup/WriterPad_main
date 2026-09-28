# Windows 인증 갱신 전용 호출부 — 오프라인 구현 결과

2026-09-14. 이번 사용자 요청은 종료 run과 분리된 호출부 구현 및 신규 영향 검사다. 실제 토큰 접근·갱신·서버 요청·GUI 추가·설치는 승인되지 않았으며 수행하지 않았다. 기존 문서의 실행 예시와 과거 03:30~06:00 조회 승인을 인증 갱신 승인으로 전용하지 않았다.

**결과: 독립 호출부 구현 완료. 신규 38건 통과 후 예약 기록 내구성 보완에 해당하는 1건만 재확인했고 통과했다. 실제 HTTP·credential 읽기·쓰기는 각각 0회다.**

새 파일은 `isolated_auth_refresh.py`, `tests/test_isolated_auth_refresh.py`, 이번 문서와 `_evidence/windows-auth-refresh-offline-20260914/`의 검사 자료다. 기존 조회 호출부·수집기·앱 소스는 수정하지 않았다. 기존 미커밋 변경, 종료 실행 자료, 원본과 설치물에 쓰기 작업을 하지 않았다. 기존 53건·21건·24건 검사도 재실행하지 않았다.

**구현한 실행 경계**

| 항목 | 구현 동작 |
| --- | --- |
| 진입 | 인자 없는 실행과 `--help`는 안내만 출력. 실제 호출에는 별도 검토된 manifest 절대 경로와 외부 SHA-256 필요 |
| 새 범위 | `format=windows-isolated-auth-refresh-v1`, 새 `auth_id`, 전용 결과 루트 `_evidence/windows-independent-auth-refresh/<auth_id>/` |
| 재실행 방지 | ID 폴더를 배타적으로 생성. 사전 점검 실패·취소·감사 저장 실패도 폴더를 지우거나 복구하지 않음. 같은 ID 재사용 거부 |
| 종료 run 분리 | 종료 통합 ID 및 `64896904-d883-4549-8d9e-3c53cadb6cb8` 조회 ID 거부. 기존 run의 사용량·만료·DB를 읽어 실행 권한으로 사용하지 않음 |
| 사용자·설정 | xiix1 SID `S-1-5-21-2542480074-635446901-4096835241-1001`, 빈 profile, 기존 credential service, 고정 Staging endpoint/account 확인. 실행 환경 override 거부 |
| 보존 조건 | manifest에 명시한 소스·공개 설정·관문/hold 파일 hash 또는 부재 조건을 요청 전과 저장 전에 확인. `preserve_only`와 `block_when_present` 구분. 조건 변경 시 중단 |
| 잠금 | 기존 공유 Windows credential mutex 사용. 확보 실패 시 토큰 읽기 전에 중단. 잠금 삭제·앱 강제 종료 없음 |
| 유일한 요청 | POST `https://mhpnszcorfzrvhyondxr.supabase.co/auth/v1/token?grant_type=refresh_token`, body의 기존 refresh token 한 필드. access Authorization 헤더 없음 |
| 한도 | 새 감사에서 HTTP 1/Auth 1 예약을 파일 기록·fsync한 뒤 전송. 예약 파일 쓰기/동기화 실패도 보수적으로 1회 소진하고 HTTP는 차단. 재시도·환급 없음 |
| 시간 | HTTP 각 단계 timeout 15초, 비동기 HTTP 전체 deadline 최대 30초. 실제 시작부터 계산하며 승인 유효시간이 더 짧으면 그 잔여시간으로 제한 |
| 통신 | TLS 검증, 환경 proxy 미사용, transport retry 0, redirect 미추적. `/user`, handshake, Q1~Q7, receipt, 본문/구조 요청 없음 |
| 감사 | 새 순번별 파일만 생성. 비밀 없는 상태·정적 오류 코드·예약 횟수·저장 확인·성공 시 account/만료만 기록. 원문 토큰·응답 body·사용자 이메일·공개 키·입력 manifest 전체는 복사하지 않음 |

`dispatch_attempted=1`은 transport 호출 시도를 뜻하며 서버 수신을 입증하지 않는다. 전송 뒤 거부·응답 유실·취소·검증 실패·저장 실패는 보수적으로 `session_uncertain=true`로 남긴다. 예약 파일 저장 단계에서 실패해 전송이 차단되면 예약은 1이지만 전송 시도는 0이고, 이 호출로 서버 세션이 바뀌었다고 표시하지 않는다. 프로세스 강제 종료나 디스크 오류로 terminal 파일이 없으면 완료로 해석하지 않으며 ID를 다시 실행하지 않는다.

**세션 확인과 저장**

기존 Windows keyring 소스와 `security_manager.py`의 저장 순서를 바탕으로 다음 기존 항목 배치만 지원한다. 이번에는 실제 저장소를 읽어 배치가 존재하는지 확인하지 않았다.

- `SupabaseAccessToken@Antigravity_WebNovelApp`: UserName이 `SupabaseAccessToken`인 기존 access.
- `Antigravity_WebNovelApp`: UserName이 `SupabaseRefreshToken`인 기존 refresh.

이 배치가 아니거나 항목이 없으면 요청 전에 중단한다. 일반 `get_password`의 generic fallback 및 `set_password`의 다른 항목 이관 동작을 사용하지 않는다. 기존 compound refresh 복제본과 다른 계정/API 항목은 수정하지 않는다. 기존 앱은 UserName이 일치하는 generic refresh 항목을 먼저 선택하므로 이 지원 배치에서는 새 refresh를 읽는다.

기존 access는 sub/account와 issuer를 로컬에서 구분하는 근거이며, 만료됐어도 refresh 입력 확인에는 사용할 수 있다. JWT 서명이나 서버 세션을 독립 검증했다는 뜻은 아니다. 갱신 응답은 HTTP 200, 중복 키 없는 JSON, 일치하는 user.id와 새 access의 sub/issuer, bearer 타입, 유효한 refresh 형태 및 새 access의 만료 여유 60초 이상을 요구한다. 응답 크기는 256KiB로 제한한다.

요청 직전과 저장 직전에 기존 쌍의 변경을 확인한다. 새 refresh를 먼저 저장하고 해당 쌍을 읽어 확인한 다음 access를 저장한다. 각 쓰기 직전에도 예상 쌍이 그대로인지 확인하고, 최종 쌍을 다시 읽어 성공을 판정한다. 단계별 쓰기 의도 감사 기록이 실패하면 해당 쓰기를 시작하지 않는다.

Windows의 두 credential 쓰기는 원자적 교체가 아니다. 공유 lease를 따르지 않는 외부 프로세스와의 비교/쓰기 사이 경쟁을 OS 차원에서 완전히 없앤 것으로 주장하지 않는다. 부분 저장·읽기 불일치 시 자동 복원·재시도 없이 불확실 상태로 중단한다. 동기 credential API를 30초 시점에 강제로 중단한다는 보장도 하지 않는다. 30초는 HTTP 구간의 한도다.

**신규 영향 검사와 증거**

`_evidence/windows-auth-refresh-offline-20260914/result-final.json`이 이번 결과 색인이다. `result.json` 및 `test-results.txt`에 최초 신규 38건 통과가 기록되어 있다. 최종 검토에서 예약 파일이 저장된 뒤 오류가 나는 경우의 카운터 보존을 보완했고, `result-02.json` 및 `test-results-02.txt`에 해당 1건의 통과를 추가했다. 원래 결과를 덮어쓰지 않았다. 최종 코드 pin은 `code-hashes.json`에 있다.

검사 범위는 정상 회전, 만료된 기존 access, ID 재사용, 사용자/profile/대상/한도 오류, 코드 pin·hold 변경, 지원하지 않는 credential 배치, 세션 경쟁, 예약 감사 실패, HTTP 거부/redirect/timeout/유실/취소, 응답 크기·계정·issuer·만료 오류, 첫/둘째 토큰 저장 실패, readback 실패, 저장/종료 감사 실패, 추가 요청 차단 및 help 경계다.

검사는 임시 폴더·합성 세션·메모리 credential backend·HTTPX MockTransport만 사용했다. 실제 socket 연결/DNS, 실제 HTTPX transport, keyring 읽기·쓰기·삭제, WinVault 저수준 읽기·쓰기, 사용자 SID 조회 및 WinDLL 접근을 검사 중 차단했다. 차단 경계에 도달한 시도도 0건이다. 검사 대상 기존 소스·문서·이전 결과 파일의 전후 hash는 두 결과 JSON에 남아 있고 모두 일치한다. iPad 원본이나 설치물 전체를 다시 검사했다는 뜻은 아니다.

**다음 단계와 사용자 작업**

다음은 이 구현을 기준으로 **기존 계정 인증 갱신 1회 승인안 작성**이다. 실제 실행용 새 auth ID·manifest·보존 pin·승인 유효시간·정확한 명령을 먼저 문서로 준비한다. 이번 구현에서는 실제 실행용 ID/manifest나 실행 승인을 만들지 않았다. 승인 유효시간과 실제 HTTP 시작 후 30초 한도는 별개이므로 시작 시각을 초 단위로 예약할 필요는 없다.

사용자는 지금 앱/GUI를 조작하거나 비밀번호·토큰을 전달할 필요가 없다. 이어서 다음과 같이 지시하면 된다.

> 구현된 호출부 기준으로 xiix1 문맥의 기존 계정 인증 갱신 1회 승인안을 작성해 주세요. 새 인증 ID, 승인 유효시간, HTTP/Auth 최대 1회, 기존 쌍 읽기·동일 계정 확인·새 쌍 저장 범위, 결과 폴더와 정확한 명령을 고정해 주세요. 문서만 작성하고 실제 토큰 접근·갱신·서버 요청은 하지 마세요.

승인안을 검토하고 한 번 실행을 승인하면 그 동일 범위에서 버튼·단계별로 다시 묻지 않는다. 성공 후에도 조회는 자동 시작하지 않고 새 조회 범위를 따로 정한다. iPad는 계속 대기하면 되며, 이번에 전달할 실제 인증·서버 관찰 결과는 없다.
