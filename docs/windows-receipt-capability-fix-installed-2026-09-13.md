# Windows receipt 수정판 설치 완료

**후속 상태:** 사용자 추가 요청으로 Windows 로그인 갱신 1회를 완료했습니다. 새 토큰은 **14:08:18 KST까지**, 시험 제한은 **15:00 KST까지**입니다. 갱신을 전송 전에 기존 run 사용량에 반영해 현재 HTTP **175/280**, Auth **24/30**, 쓰기 **19/21**, HTTP 잔여 **105**입니다. 본문 송수신은 하지 않았습니다. 아래 174회·로그인 미갱신은 설치 완료 당시 기록입니다. [갱신 결과](../_evidence/windows-integrated-editor-execution-20260913/refresh-after-receipt-patch-result.json).

사용자 「윈도우측에서 진행 할 수 있는 내용 진행」 요청에 따라 통합 후보의 capability 순서 비교를 수정하고 검사·패키징·기존 EXE 백업 후 교체를 완료했습니다. **실제 앱 실행·로그인 갱신·서버 요청은 0회**이며 iPad S2 복구 완료 신호를 기다립니다.

- **수정:** receipt 양쪽 capability가 중복 없는 문자열 배열이고 동일 구성원인지 확인합니다. 순서 차이만 허용하며 원래 요청 JSON/해시는 변경하지 않습니다. 다른 metadata·요청/응답 해시·응답 계약 검증은 유지합니다. 제품 변경은 `integrated_editor_sync.py` 한 파일이며 완료된 일반 집필 모드는 이번 수정 대상이 아닙니다.
- **검사:** 통합 관련 33개 검사 통과. 합성 서버가 실제 SQL처럼 capability를 정렬하도록 고쳐 본문·구조 응답 유실/재시작 복구와 POST 재전송 방지를 확인했습니다. 누락/추가/중복/잘못된 타입/metadata·해시 변경 거부를 확인했습니다. EXE 바이트코드·리소스 대조 및 Qt/통합 화면 격리 시작 검사 2개도 통과했습니다. 실제 Windows receipt SELECT 권한 확인으로 해석하지 않습니다.
- **기존 실행 호환:** 15:00 연장 상태 복제본에서 같은 실행 재연결 후 전체 state가 동일했고 기존 완료 batch 19개가 새 코드 검증을 통과했습니다. 설치 전후 실제 전용 저장소 전체 파일·실행 설정·원래 보호 파일 해시가 일치합니다. 사용량 HTTP **174/280**, Auth **23/30**, 쓰기 **19/21**, 종료 **15:00 KST**, 자동 꺼짐을 그대로 보존했습니다.

| 구분 | 값 |
|---|---|
| 수정 패키지 ID | `windows-staging-integrated-editor-20260913-receipt-v2` |
| 통신용 build ID | `windows-staging-integrated-editor-20260913-v1` 유지 — 기존 실행/원래 요청과 호환 |
| EXE SHA-256 | `4edfd3812d538dae72f4a370f6202083d64064ae2c8da404404a677e391e807c` |
| EXE 크기 | 79,676,459바이트 |
| 설치 경로 | `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe` |
| 소스 묶음 SHA-256 | `96d9ad27f90595a9753103c44eee986a11d37b96b92bbd961adc84483c19411a` |

**iPad 전달 사항:** Windows 수정판 설치 완료·미실행입니다. 기존 시험 명세 v2 해시 `78405ea61ffcc1ae0491676dc63389ba47f46db56586f576f1f78974a3649669`와 시험 ID·실행 ID·원본 요청·누적 사용량은 그대로입니다. 해당 명세에 적힌 이전 Windows EXE 해시는 과거 후보 기록으로 보존하고, 현재 설치물은 위 수정 패키지 ID/EXE 해시를 별도 변경 기록으로 대조해 주세요. iPad 완료된 S1·기존 진단을 반복하지 않으며 원래 batch의 한정 복구 준비/완료와 차단만 회신하면 됩니다. 이 문서는 iPad의 추가 설치·조회 승인을 대신하지 않습니다.

Windows 로그인 토큰은 이미 만료돼 실기기 재개 시 기존 계정 갱신이 필요합니다. 이번에는 갱신하거나 한도를 늘리지 않았습니다.

증거: [_evidence/windows-integrated-receipt-candidate-20260913](../_evidence/windows-integrated-receipt-candidate-20260913/)의 `source-manifest.json`, `build-verification.json`, `smoke-result.json`, `preserved-run-compatibility.json`, `installation-result.json`. 이전 EXE는 `private-install-backup/`에 보존했습니다. 검사 결과는 `../_evidence/windows-integrated-editor-20260913/20260913T040046979821Z/result.json`입니다.
