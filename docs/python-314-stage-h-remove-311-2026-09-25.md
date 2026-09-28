# 단계 H 증거 — Python 3.11 제거

> 개발지시서: [python-314-transition-development-order-2026-09-19.md](python-314-transition-development-order-2026-09-19.md)
> 단계 G 증거: [python-314-stage-g-rebuild-2026-09-20.md](python-314-stage-g-rebuild-2026-09-20.md)

2026-09-25 시스템 Python 3.11.9를 지웠다. 앱, 빌드, 복구 도구, 다른 프로젝트 모두 3.14.7로 돈다. 제거 전후 상태는 `_evidence/python-311-removal-20260925/`의 `pre-state.txt`와 `post-state.txt`에 있다.

## 제거 전 확인

| 항목 | 결과 |
| --- | --- |
| `unicodedata2 15.0.0` cp311 휠 | `_evidence/python-314-v1-golden-20260920/unicodedata2-preservation/`에 있음, `sha256=302b56485f4e…` 기록과 일치 |
| v1 golden vectors | `manifest.jsonl` 992건, `v1-golden-vectors.json`, `sync-contract/conformance_vectors/storage-name-v1.json` 보존 |
| 3.11로 실행 중인 프로세스 | 0 |
| 3.11 기반 가상환경 | D:\ 와 `C:\Users\xiix1` 전수 검색 6개 중 1개 — 아래 참조 |
| 단계 G | 완료 (실사용 11항목 통과, 설치본은 3.14.7 빌드) |

3.11 기반 가상환경은 `D:\Download\코딩 백업\_사용안함_과거테스트흔적_20260815\_stage8_package_env` 하나였다. 원래 이 저장소의 `_stage8_package_env`였고 2026-08-12 이후 쓰이지 않았다. 제거 후 `No Python at …Python311\python.exe`로 실행되지 않는 것을 확인했다. 나머지는 이 전환의 3.14 venv 3개와 `comic-translate`의 3.12 환경 2개(uv 3.12.13, SVP 4 내장 3.12.9)다.

## 제거

Windows 설정 > 앱에서 **Python 3.11.9 (64-bit)**를 제거했다. Claude Code의 자동 권한 판단이 제거 프로그램 실행을 막아 사용자가 직접 했다.

| 지워진 것 | 남은 것 |
| --- | --- |
| 번들과 구성요소 MSI 10개 | **Python Launcher 3.11.9** (`C:\Windows\py.exe`, `pyw.exe`) |
| `HKCU\Software\Python\PythonCore\3.11` | `…\Programs\Python\Python311\` 6,939 MB |
| 시작 메뉴 "Python 3.11" | |
| 사용자 PATH의 `Python311\Scripts\`, `Python311\` | |

Python Launcher는 이제 3.14만 보고 정상 동작한다. 지워도 된다 — 그러면 `py`가 Python 설치 관리자 26.3 쪽으로 넘어간다.

`Python311\` 폴더는 pip로 설치한 패키지다(`torch` 4.4 GB, `PySide6`, `codex_cli_bin` 등). `python.exe`가 없어 실행할 수 없다. 지울지는 사용자가 정한다.

## 제거 후 명령 연결

새 터미널 기준(레지스트리의 machine + user PATH)이다.

| 명령 | 연결 | 버전 |
| --- | --- | --- |
| `python` · `python3` · `pythonw` | `AppData\Local\Python\bin\` | 3.14.7 |
| `pip` | `AppData\Local\Python\bin\pip.exe` | 3.14용 pip 26.2.1 |
| `py` · `pyw` | `C:\Windows\` (Python Launcher) | 3.14.7 |
| `.py` 더블클릭 | `C:\Windows\py.exe` | 3.14.7 |

PATH는 전환 1〜4단계에서 3.14 경로를 스토어 바로가기(`WindowsApps`) 앞으로 옮겨 두었다. 그래서 3.11 두 줄이 빠지자 곧바로 3.14로 넘어갔다.

## 제거 후 재확인

지시서가 "특히 볼 곳"으로 적은 항목을 전부 다시 돌렸다.

| 항목 | 결과 |
| --- | --- |
| 설치본 exe (2차, `1c96da1ad8b5…`) | `--qt-import-smoke-test` 0, `--runtime-report` 3.14.7 · `ok` |
| `.venv314` | 3.14.7, `pip check` 통과 |
| `writerpad-recovery.cmd` 저장소 본체 | 코드페이지 949에서 3.14.7로 복구 도구 시작, cmd 오류 0 |
| `writerpad-recovery.cmd` 독립복구 사본 | 같음 |
| 빌드 | 3.11과 Git mingw64가 없는 PATH에서 `247e1cd`를 다시 빌드했다. 설치본과 248개 항목의 **내용이 모두 같다**(PYZ 모듈, `base_library.zip` 구성원 포함). 스모크 0 |
| 다른 프로젝트 | 오마니 수영예약 `.venv`(playwright 1.62.0, `강좌신청.bat`의 `pythonw.exe` 있음), 히토미꺼라 `.venv`(torch 2.14.0+cu130, CUDA), 전역 3.14 모듈 18/18, `pyw -3.14` 모두 정상 |

빌드가 같다는 것은 이전 빌드들이 PATH의 `Python311\`에서 아무것도 가져오지 않았다는 뜻이다. 3.11을 지워도 이후 빌드 결과가 달라지지 않는다.

## 남은 것

- **3.11을 품은 옛 exe.** `python311.dll`을 품고 있지만 계속 실행된다. 롤백용으로 둘지는 사용자가 정한다(지시서 H "주의").
  - `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요_2.exe` (09-13)
  - `작가님 힘내세요\집필프로그램\작가님 힘내세요.exe` (08-27)
  - `dist\Antigravity_AI_Writer.exe` (08-30)
  - `_evidence/python-314-rebuild-20260920/previous/`의 2개
- **3.14로 되돌릴 빌드.** 09-20 빌드(3.14.6)가 `output/python-3147-usage-test-20260924/previous/`에 있다.
- **단계 C.** 서버 트리거 수정, allowlist 활성화, 작품 전환, iPad 왕복 시험은 이 전환과 별개로 남아 있다. 트리거 수정은 iPad 쪽 저장소 소관이다.

## 판정

단계 H 완료. 로컬 범위의 전환(A · B · D · E · F · G · H)이 끝났다. Windows 클라이언트는 Python 3.14.7에서 개발 · 시험 · 빌드 · 설치 · 복구까지 돌고, 시스템에는 3.11 인터프리터가 없다.
