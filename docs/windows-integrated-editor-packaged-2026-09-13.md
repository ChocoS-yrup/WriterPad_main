# Windows 통합 EXE 패키징 완료

사용자 요청에 따라 Windows 통합 후보를 패키징했다. iPad 구현은 진행 중이며 **설치·양측 실기기 검증은 진행하지 않았다.** 기존 설치물·본문·DB·관문/보류·이전 증거를 변경하지 않았고 prod 전환도 없다.

| 항목 | 결과 |
|---|---|
| 후보 ID | `windows-staging-integrated-editor-20260913-v1` |
| EXE | `_evidence/windows-integrated-editor-candidate-20260913/candidate/작가님 힘내세요.exe` |
| 크기 | 79,678,195바이트 |
| SHA-256 | `35057621d198a6d523e092e6c92942fd410ef8e027db5da4aaa1c3be37490612` |
| 형식 | Windows x64, 미서명, Staging 설정 |
| 소스 묶음 SHA-256 | `a76746695b97a98676c104e42345c1bf92ecbf1c1ba0c7a3fe3eea34880ed1f5` |
| 빌드 | 기존 PyInstaller 6.21.0·PyQt6 6.11.0 등 설치된 동일 의존성 재사용, 성공 |

현재 미커밋 소스를 별도 `source/`에 동결했다. 그 복사본에서만 `INTEGRATED_EDITOR_ONLY=True`로 설정했다. 작업 트리 플래그는 False이며, 다른 단일 문서/검증 모드는 후보에서도 False다. EXE에 포함된 프로젝트 모듈의 바이트코드·main·리소스가 동결 소스와 일치하고 Qt/MSVC 런타임 DLL이 포함됐음을 확인했다.

## 이번에 수행한 검사

- **Qt 시작 검사 통과:** 패키지의 Qt 모듈·플랫폼 플러그인·런타임 로딩 확인.
- **통합 화면 시작 검사 통과:** 새 가상 문서 2개로 저장·문서 전환·한글/Unicode/LF 보존 확인. 실행 범위 없이 자동 동기화를 켜도 인증/통신 0회.
- 통합 시작 검사에는 실제 네트워크·키체인 차단과 가상 작업공간 외 SQLite 연결 차단을 적용했다. 설치 경로·실제 AppData를 사용하지 않았으며, 격리된 설치 루트/AppData도 비어 있음을 확인했다. 스크린샷은 촬영하지 않았다.

기존 기능 검사와 완료된 왕복은 반복하지 않았다. 패키지 확인용 `integrated_editor_smoke.py`와 명시적 시작 검사 분기만 추가했고, 빌드/검사 스크립트는 `scripts/build_integrated_editor_candidate.py`, `scripts/verify_integrated_editor_candidate.py`에 남겼다.

증거는 `_evidence/windows-integrated-editor-candidate-20260913/`의 `source-manifest.json`, `build.log`, `build-result.json`, `build-verification.json`, `smoke-result.json` 및 검사별 로그에 보존했다. 원고·실제 DB·세션·실행 승인 파일은 EXE에 포함하지 않았다.

**다음:** iPad 구현·검사 결과를 대조하고, 시험 대상과 양측 요청량/시간 한도를 포함한 통합 실행안을 정한다. 이후 승인된 범위에서 설치·실기기 확인을 진행한다. 양측 실제 receipt SELECT 권한/RLS는 계속 미확인이다. 과거 후보·23회 요청 상한을 재사용하지 않는다.
