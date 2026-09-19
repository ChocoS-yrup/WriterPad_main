# Windows 격리 대상 실행 호출부 — 사용자·세션·승인 연결

2026-09-14. 사용자 요청에 따라 `isolated_target_launcher.py`를 추가하고 신규 영향 검사 **25건을 한 묶음으로 통과**했다. 실제 자격증명 접근·HTTP·로그인 갱신·설치·GUI·설정/관문 변경은 수행하지 않았다. 과거 문서의 승인과 종료 실행을 이번 구현의 실행 권한으로 사용하지 않았다.

## 연결한 조건

| 경계 | 실행 시 조건 |
| --- | --- |
| Windows 사용자 | xiix1의 고정 SID `S-1-5-21-2542480074-635446901-4096835241-1001`, 빈 profile. 기존 WindowsLease가 실제 사용자 SID와 profile을 확인하고 credential mutex를 독점해야 한다. 환경 override는 거부한다. |
| 계정·세션 | 기존 `Antigravity_WebNovelApp` 서비스의 compound access credential만 사용한다. JWT sub는 지정 계정, iss는 지정 Staging Auth 주소, exp는 실행 창 종료보다 최소 30초 뒤여야 한다. 최초 및 전송 전/응답 처리 중 같은 토큰인지 확인한다. 로그인·refresh·다른 credential fallback은 없다. 로컬 JWT 대조만으로 서버 인증 완료를 주장하지 않고 Q1 서버 사용자 응답도 확인한다. |
| 승인 입력 | `--manifest`와 별도 `--manifest-sha256`가 모두 있어야 진입한다. manifest는 새 run·시간 창·사용자·writer_device_id·쓰기 대상·입력·코드·공개 설정·보존 조건을 결합한다. 해시 고정은 변경 탐지이며 사용자 승인 자체를 자동 생성하거나 증명하지 않는다. |
| 범위 | 생성 전 7회 + 생성 RPC 4회 + 생성 후 5회 = HTTP 최대 16회, 쓰기 RPC 최대 4회, 180초. 과거 조회 전용 7회 범위와 분리한다. 새 폴더·본문·빈문서 및 기존 부모/새 폴더 정렬만 허용한다. |
| 보존 | 기존 hold 파일과 종료 reviewed-execution 파일의 승인 시 고정한 hash를 계속 확인한다. `preserve_only`는 별도 생성 범위의 명시적 검토를 요구하며 앱의 hold를 해제하지 않는다. 추가 `block_when_present` 파일이 존재하면 토큰 접근 전에 중단한다. |
| 전송 | 실제 직전 URL·메서드·본문·헤더·예약 기록과 순서를 다시 대조한다. 생성 요청의 대상·내용·revision·operation/batch 중복을 확인한다. 쿠키 주입·추가 요청·범위 변경은 차단한다. |
| 종료 | 신규 attempt 폴더를 독점 생성하고 claim/terminal을 남긴다. 실패한 실행도 같은 run으로 재개하지 않는다. 부분 생성·응답 유실은 자동 재시도·삭제·롤백하지 않는다. 엔진에서 결과를 돌려받지 못하면 예약 수는 0으로 추정하지 않고 null로 기록한다. |

## 호출 및 결과 경로

CLI 형태는 다음과 같다. **이번에는 실제 승인 파일·실행 시각·실기기 writer_device_id를 발급하거나 아래 명령을 실행하지 않았다.**

```powershell
& 'C:\Users\xiix1\AppData\Local\Programs\Python\Python311\python.exe' -B 'D:\안티그래비티\scratch\작가님 힘내세요\isolated_target_launcher.py' --manifest '<새 생성 승인 manifest 절대경로>' --manifest-sha256 '<승인한 manifest SHA256>'
```

고정 결과 루트는 `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-independent-target-create`다. `attempts/<새 run UUID>`에는 호출부 승인 원본·진입·종료 기록, `runs/<같은 UUID>`에는 기존 bootstrap 엔진의 예약·응답 원문·생성 요청·기준본 후보·종료 기록을 남긴다. 과거 조회·인증 결과 폴더를 사용하지 않는다.

승인 manifest의 형식은 `windows-isolated-target-launch-v1`이다. 필드는 다음과 같이 고정한다.

- `run_id`, `results_root`, `windows_sid`, `profile`, `credential_service`, `writer_device_id`: 이번 생성의 실행·사용자·기기 결합.
- `plan`, `scope`, `config`: 각각 절대경로와 SHA256. scope는 bootstrap 형식, 같은 run과 plan hash, `not_before`/`expires_at`, 16/4/180 한도를 포함한다.
- `inputs`: 기존 proposal 폴더의 metadata/orders/body/empty 네 원본 경로와 SHA256. 빈문서는 0바이트여야 한다.
- `source_sha256`: 호출부·bootstrap 및 기존 조회 호출부가 사용하는 순수 모듈 전체 9개 파일의 hash.
- `writable_ids`, `order_ids`: 고정 후보 3개와 정렬 2개. 기존 부모 정렬의 기존 children은 보존한다.
- `restriction_review`, `restrictions`: 별도 생성 범위 검토 표식 `reviewed-for-independent-target-create-v1`과 보존/차단 파일의 경로·hash·효과. 기존 조회 승인 문서의 검토 표식을 재사용할 수 없다.

공개 Staging 설정은 기존 보존 경로로 고정한다. plan은 기존 reference·본문·명시한 writer_device_id에서 다시 계산한 값과 정확히 같아야 한다. manifest와 plan의 기기 ID 일치는 확인하지만 실기기 ID의 출처 확인은 아직 하지 않았다.

## 검사 결과·한계

신규 25건: 정상 16회/4회 합성 실행, 원본·비밀 보존, 사용자/profile·manifest hash·소스·쓰기 범위·한도·보존 pin, 종료 run·중복 실행, mutex 거절, 시간/세션 만료, issuer/계정 차이, hold/본문/세션 변경, 응답 유실·취소, 쿠키·예약·생성 요청 변조, 차단 파일, 인자 없는 CLI를 확인했다.

실제 socket/DNS·HTTP transport·keyring/WinVault·Windows SID/WinDLL 접근을 검사 실행기에서 차단했다. 해당 경계 접근 시도도 0건이다. 기존 테스트의 합성 helper만 재사용했으며 기존 완료 테스트 메서드는 실행하지 않았다. 검사 대상 기존 소스·문서·이전 검사/실행 증거의 전후 hash는 모두 일치했다. 기존 설치물이나 iPad 원본 전체를 새로 검사했다는 뜻은 아니다.

증거: `_evidence/windows-target-launcher-offline-20260914/result.json`, `test-results.txt`, `run_checks.py`, `code-hashes.json`. 새 호출부와 새 검사 파일만 소스 변경 대상이며 기존 bootstrap/조회/인증/앱 소스는 수정하지 않았다.

실제 Windows credential·실제 서버에서 이 호출부가 동작하는지는 미검증이다. 생성 네 RPC 전체는 원자적이지 않고, 생성 후 조회도 원자적 snapshot이 아니다. 성공해도 기준본은 후보이며 앱 결합·baseline 적용·iPad 수신 권한을 자동으로 부여하지 않는다. iPad의 최종 실제 수신 계약, 물리 저장소·앱 target 결합, 기존 raw/chain/profile 미검증 상태는 유지한다.

다음 Windows 단계는 **실기기 writer_device_id의 근거와 새 실행 창을 포함한 실제 생성 1회 승인안을 한 번에 준비**하는 것이다. 기존 승인으로 실행하지 않는다. iPad는 실제 수신 계약·앱 결합의 오프라인 작업을 병행할 수 있지만 이번 변경만으로 실제 수신을 시작하지 않는다. 지금 사용자가 GUI를 조작하거나 ZIP을 전달할 일은 없다.
