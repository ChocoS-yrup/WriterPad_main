# iPad 서버 목록 후보 설치 승인 전달 — 2026-09-11

## 승인과 Windows 완료 결과

Windows 작업에서 사용자에게 두 후보의 구체적 설치 범위를 제시했고 사용자가 **“승인”**이라고 답했다. 아래 iPad 설치·설치 직전 백업·설치 후 미실행 검증은 그 승인에 포함된다. 같은 설치 권한을 다시 요청할 필요는 없다. 원고 저장·앱 종료 여부는 실제 사용자 상태를 확인한다.

Windows 후보 `windows-staging-8013-uuid-20260910`은 2026-09-11 00:32:48 KST에 설치됐다. EXE SHA-256은 `76e695051d83977a15b66892557cba13dc4eb95a6124d0710d503337193fb04d`이다. 실행 파일을 교체하고 새 설치 정보 JSON 1개를 추가했다. 이전 설치·AppData의 713개 파일 백업을 직전에 대조했고, EXE 외 기존 파일과 전체 AppData의 바이트·mtime이 설치 후 그대로임을 확인했다. DB 8013·닫힌 관문·송신 보류가 보존됐다. 앱 실행, 기존 DB 업그레이드·UUID 교정·복구 도구 설치 반복, 실제 수신·송신은 하지 않았다.

이 Windows 결과는 첨부 `windows-installation-verification.json`에 있다. iPad 개발 측이 Windows 바이너리나 DB를 직접 검증했다고 표현하지 않는다.

## 승인된 iPad 후보

- 후보 ID: `ipad-staging-catalog-20260911`
- bundle ID: `com.chocos.writerpad.debug` / ChocoS Debug
- Debug / iPhoneOS / arm64, 버전 0.1.0 / 빌드 1, DB V16 유지
- 보존 앱 ZIP SHA-256: `b633e132db2bfa692301c328e9d8104904a5ea3c6531c9803f6bb4d2645f188e`
- 보존 앱 ZIP 크기: 28,592,350 bytes
- 앱 tree digest: `65c659c76b23be2ac7fff4c607a928d2c79726deadbcc09c487b969705252447`
- 기능 dylib SHA-256: `b106892df81c9e4271c6b17b3e5621356416c77f5c47b6bf931dafd1df95e19e`
- 기기 후보: `build/ipad-catalog-candidate-20260911/private/DerivedData/Build/Products/Debug-iphoneos/WriterPad.app`
- 보존 아카이브: `build/ipad-catalog-candidate-20260911/private/ipad-staging-catalog-20260911.app.zip`
- 새 후보 및 복구 후보 개발 프로필 만료: **2026-09-12 18:19:25 KST**

입력 준비 회신 ZIP SHA-256은 `4f908ddcff76316afb23450855689afb2aff94434c502459180dbfbbeaf5626e`이다. Windows는 manifest·소스 목록·앱 목록 digest를 대조했고 빌드·서명 성공은 iPad 보고로 수용했다. 실제 앱 파일은 Mac에서 다시 확인한다. 완료된 57개/22개 검사나 빌드를 같은 입력으로 반복하지 않는다.

## iPad 개발 측 실행 순서

1. 이 ZIP의 CRC·manifest·파일 크기/해시·경로·중복·여분 파일을 확인한다. 실제 후보 파일·보존 아카이브 해시와 앱 manifest, 현재 기기 bundle, 앱 종료 상태를 대조한다. 사용자가 원고를 저장하고 앱을 완전히 종료했음을 확인한다. 과거 PID나 실행 인자를 현재 상태로 간주하지 않는다.
2. 기존 서명·프로필의 현재 유효성, bundle/team/entitlements, 연결된 설치 대상 기기가 프로필 허용 대상인지 확인한다. 만료·불일치면 설치를 중단하고 이유와 필요한 다음 범위를 회신한다. 새 인증서 발급·계정 변경·프로필 갱신·재서명은 이번 승인에 포함되지 않는다.
3. 종료된 기기의 Documents/Library 전체를 새 비공개 백업으로 보존한다. 원고·폴더·DB·WAL/SHM·설정·기록·binding·큐·충돌·수신 journal·관문과 전역 설정을 포함해 파일 manifest와 현재 상태를 확보한다. SQLite 검사는 별도 검사 사본으로 수행하고 원본 백업은 보존한다. Keychain 토큰을 추출하지 않는다. 과거 백업이나 대기 4건·충돌 3건을 현재 값으로 대신하지 않는다. 일관된 백업과 대조가 실패하면 설치하지 않는다.
4. 검증된 **동일 bundle ID의 앱만 제자리 업데이트**한다. 설치 명령이 앱을 자동 실행하지 않는지 확인한다. 앱 삭제·초기화·bundle 변경·기존 작품 UUID 교정·DB 덮어쓰기·관문/전역 설정/보류 변경은 하지 않는다. 기존 iPad local UUID와 server UUID의 분리는 유지한다.
5. 앱을 실행하지 않은 상태에서 설치된 후보 식별과 사용자 자료 보존을 확인한다. 버전 번호가 동일하므로 설치 도구 기록·후보 해시 등 확보 가능한 근거와 한계를 함께 남긴다. Documents/Library·DB·binding·큐·충돌·journal·관문·전역 설정을 직전 백업과 비교한다. OS 설치 메타데이터 차이가 있으면 경로와 이유를 분리해 보고하고 사용자 자료 변화는 성공으로 처리하지 않는다.
6. 앱 미실행 상태를 유지하고 설치 결과 ZIP을 Windows에 회신한다. 자동 롤백·원본 DB 복원은 하지 않는다. 문제가 있으면 현재 상태와 변경 자료를 먼저 보존하고 정확한 실패 지점을 보고한다.

## 이번 승인에서 제외된 동작

첫 앱 실행, 로그인 복원, 서버 목록 조회·선택·가져오기, 작품 생성, 실제 원고·구조 송신, 전역 동기화 활성화, 관문 개방, 송신 보류 해제, Production 전환은 수행하지 않는다. 홈 화면 아이콘으로 실행하지 않는다.

향후 첫 실행은 별도 승인 단계다. 그때 `-writerpad.sync-all-projects-enabled NO`와 `-writerpad.restore-last-project-on-launch NO`를 명시하고 적용 여부를 확인한다. 이 인자는 영구 설정이 아니며 지금 저장된 전역 설정을 수정하라는 뜻도 아니다. 기존 시험 작품 연결을 지우거나 다시 만들지 않는다.

## 회신과 사용자 행동

회신 ZIP 이름은 `ipad-catalog-candidate-install-result-20260911.zip`으로 한다. 실제 설치 여부·시각, 후보 해시/식별 근거, 설치 직전 서명/프로필/기기 적합성, 새로운 종료 시점 백업 검증, 설치 전후 자료 대조, 기존 상태 보존, 미실행 상태와 수행하지 않은 서버 동작을 명시한다. 파일 manifest와 검증 요약만 공유하고 실제 앱·프로비저닝 원문·설정 키·세션·토큰·원고·DB·기기 백업·기기 고유 식별자는 넣지 않는다.

사용자는 이 ZIP을 iPad 개발 작업에 전달하고, 개발 측의 백업 준비 안내에 맞춰 원고를 저장한 뒤 앱을 완전히 종료한다. 설치 후 앱을 열지 말고 회신 ZIP을 Windows 작업에 전달한다. Windows 앱도 종료 상태로 유지한다.
