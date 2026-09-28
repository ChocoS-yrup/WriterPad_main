# 단계 G 증거 — 3.14 exe 재빌드

> 개발지시서: [python-314-transition-development-order-2026-09-19.md](python-314-transition-development-order-2026-09-19.md)
> 단계 F 증거: [python-314-stage-f-verification-2026-09-20.md](python-314-stage-f-verification-2026-09-20.md)

보안 관점의 실제 목표였던 **내장 런타임 `python311.dll` → `python314.dll` 교체**를 달성했다. 산출물과 기록은 `_evidence/python-314-rebuild-20260920/`에 있다.

## 빌드

`.venv314`의 Python 3.14.6 + PyInstaller 6.21.0으로 `작가님힘내세요.spec`을 돌렸다. 지시서가 언급한 `build_exe.ps1`은 이 저장소에 없다 — CI와 동일하게 `python -m PyInstaller`를 직접 호출했다.

비교 기준은 지시서가 지목한 `dist\작가님 힘내세요.exe`(2026-09-13, 79,543,070 bytes)로 잡았다. 설치본(`집필프로그램\`, 2026-08-27, 96,248,175 bytes)은 2주 이상 오래된 빌드라 섞으면 3.14 전환과 무관한 변경이 차이로 들어온다.

이전 빌드 둘 다 `_evidence/python-314-rebuild-20260920/previous/`에 해시와 함께 보존했다.

| 보존본 | bytes | sha256 앞 12 |
| --- | --- | --- |
| `dist-20260913.exe` | 79,543,070 | `e1eb5de53e45` |
| `installed-20260827.exe` | 96,248,175 | `7523921668b4` |

## 빌드 후 필수 확인

| 항목 | 기대 | 실측 |
| --- | --- | --- |
| `python314.dll` | 있음 | **있음** |
| `python311.dll` | 없음 | **없음** |
| `unicodedata2` 확장모듈 | 없음 | **없음** |
| 번들 패키지 버전 = `.venv314` | 일치 | **일치** (아래 자기보고) |
| `--qt-import-smoke-test` | 종료코드 0 | **0** |
| 실행 중 `sys.version` | 3.14.x | **3.14.6** |

최종 산출물: 61,620,659 bytes, `sha256=658696083c4c…`

### 실행 중 자기보고

지시서가 *"이게 가장 확실하다"*고 한 항목이다. exe에 `--runtime-report`를 추가했다.

```json
{
  "frozen": true,
  "python": "3.14.6",
  "stdlib_unicodedata": "16.0.0",
  "unicodedata2": "absent (expected: storage-name-v2 does not use it)",
  "packages": {
    "PyQt6": "6.11.0",          "supabase": "2.31.0",
    "keyring": "25.7.0",        "Markdown": "3.10.2",
    "anthropic": "0.120.0",     "openai": "2.44.0",
    "google-genai": "2.10.0",   "python-dotenv": "imported (version unavailable)"
  },
  "verdict": "ok"
}
```

바이너리에서 `python314.dll` 문자열을 읽는 것은 **무엇이 담겼는지**를 보여주고, 이 보고는 **무엇이 실제로 로드됐는지**를 보여준다. 둘은 다른 질문이다.

`python-dotenv`만 버전이 안 잡히는데 dist-info도 `__version__`도 노출하지 않기 때문이다. 임포트는 성공한다.

이 플래그는 `--qt-import-smoke-test`와 달리 **LLM 공급자를 실제로 임포트해 본다.** 아래의 이유 때문이다.

## 이 단계에서 잡은 결함 — 번들 누락

지시서가 *"문자열 검사만으로는 부족하다"*며 번들 목록 열람과 이전 빌드 대조를 요구한 이유가 그대로 드러났다.

첫 빌드는 문자열 검사 3항목을 모두 통과하고 `--qt-import-smoke-test`도 종료코드 0이었다. 그런데 **79.5MB → 55.5MB로 24MB 작았다.** PYZ 아카이브를 풀어 대조하니 원인이 나왔다.

```
PYZ 모듈  4,780 → 1,128

통째로 사라진 최상위 패키지
  -1245  openai
  -1079  anthropic
  - 342  google
```

`llm_provider.py`가 쓰는 LLM 공급자 3종이 전부 빠져 있었다.

```
llm_provider.py:283      import anthropic
llm_provider.py:319      from openai import OpenAI
llm_provider.py:341      from google import genai
```

**셋 다 함수 내부 지연 임포트다.** 그래서 모듈 로드도, 앱 시작도, Qt 스모크 테스트도 전부 통과한다. 사용자가 요약·초고·평가를 누르는 순간에야 `ImportError`가 난다.

### 원인은 3.14가 아니라 요구사항 파일이었다

`requirements-stage8.txt`에 6개만 적혀 있는데 앱은 9개를 쓴다. 3.11 빌드가 멀쩡했던 것은 그 인터프리터에 153개가 깔려 있어 PyInstaller가 주워 담았기 때문이다. `.venv314`는 깨끗해서 **요구사항 파일이 실제 런타임 의존성을 기술하지 않고 있었다는 사실**이 비로소 드러났다.

세 패키지를 3.11 환경과 동일한 버전으로 고정해 추가했다. 지시서 E-4의 "올리지 않는다" 원칙 그대로다.

```
anthropic==0.120.0
openai==2.44.0
google-genai==2.10.0
```

이 수정은 CI에도 필요하다. 단계 E-3에서 워크플로를 3.14로 올렸으므로, 고치지 않았다면 CI도 같은 결함을 가진 exe를 만든다.

### 재빌드 후 대조

| 패키지 | 이전(3.11) | 이번 빌드 |
| --- | --- | --- |
| `openai` | 1,245 | **1,245** |
| `anthropic` | 1,079 | **1,079** |
| `google` | 342 | **342** |
| `jiter` | 1 | **1** |
| `supabase` | 11 | 11 |
| `postgrest` | 14 | 14 |
| `httpx` | 23 | 23 |
| `keyring` | 20 | 20 |
| `markdown` | 31 | 31 |

모듈 수까지 일치한다.

## 여전히 빠진 31개는 앱이 쓰지 않는다

`pygments`, `numpy`, `rich`, `PIL`, `markdown_it`, `yaml`, `jinja2`, `click` 등이다. 프로덕션 `.py` 전체를 AST로 훑어 확인했다.

- `numpy` · `PIL` · `cv2` → `process_icon.py` 하나만 쓰는데, **그 파일을 임포트하는 모듈이 없다.** 아이콘 처리용 독립 유틸리티다.
- `rich` · `pygments` · `click` · `markdown_it` · `mdurl` · `colorama` → 시스템 3.11에 깔려 있던 `openai` CLI 계열
- `yaml` · `jinja2` · `attr` · `pyparsing` · `markupsafe` → `google-generativeai` 0.8.6 계열. 앱이 쓰는 것은 `google-genai` 2.10.0이고 그쪽은 들어 있다.
- `pkg_resources` · `distutils` · `importlib_metadata` · `zipp` · `imp` · `unittest` → setuptools·stdlib 잔향

`pip check`도 통과한다.

## 크기가 79.5MB 에서 61.6MB 로 줄어든 것은 정상이다

18MB 감소는 놀랄 만한 폭이라 압축 바이트 기준으로 맞춰 보았다.

```
TOC 압축 합계 차이   -17,917,531
exe 실제 크기 차이   -17,922,411     (5KB 차이 = PE 헤더·부트로더)
```

| 항목 | 압축 기준 | 정체 |
| --- | --- | --- |
| `numpy` (openblas 6.4MB 포함) | **-8.4 MB** | `process_icon.py` 만 쓴다. 그 파일을 임포트하는 모듈이 없다 |
| `PIL` (`_avif` 4.3MB 포함) | **-6.7 MB** | 위와 동일 |
| `PYZ.pyz` | **-5.3 MB** | 위 둘의 파이썬 소스 + `rich`·`pygments`·`yaml` |
| `unicodedata2` | -0.4 MB | 의도적 제거 |
| `python311.dll` → `python314.dll` | +0.4 MB | 이번 전환의 목표 |
| `libcrypto`·`libssl`·`_zstd` | +3.0 MB | cryptography 50 + 3.14 표준 라이브러리 |

`openai` 1,245 / `anthropic` 1,079 / `google` 342 / `jiter` 1 은 모듈 수까지 이전 빌드와 같다. 줄어든 것은 전부 앱이 쓰지 않는 쪽이다.

### 숨은 의존성이 없다는 근거

**동적 임포트** — `importlib.import_module` 과 `__import__` 의 사용처가 앱 전체에서 `main.py` 의 `--runtime-report` 두 줄뿐이다. AST 로 잡히지 않는 경로가 없다.

**SDK 내부 사용** — 여기가 실제 위험 지점이었다. `google-genai` 가 `PIL` 을 12곳에서 임포트한다. 전부 확인했다.

```python
# types.py:58            정식 보호
try:
    import PIL.Image
except ImportError:
    PIL_Image = None

# _transformers.py:34    런타임에 실행되지 않는다
if typing.TYPE_CHECKING:
    import PIL.Image

# live.py:278            독스트링 안의 예제 코드
```

`openai` 의 `numpy` 도 `_extras/numpy_proxy.py`(없으면 쓸 때 안내 오류를 내는 지연 프록시)와 오디오 헬퍼뿐이다. `anthropic` 은 둘 다 쓰지 않는다.

**앱이 애초에 그 경로에 닿지 않는다** — `llm_provider.py:125` 가 모델을 거른다.

```python
unsupported_terms = ("image", "audio", "realtime", "transcribe",
                     "tts", "embedding", "moderation", "codex")
```

이미지·오디오 모델을 배제하는 텍스트 전용 설계다.

**실증** — `.venv314` 에는 `PIL` 도 `numpy` 도 없다. 빌드 환경이 곧 exe 의 상황이고, 그 상태에서 `--runtime-report` 가 8개 의존성을 전부 임포트해 `verdict: ok` 를 냈다.

비교하자면 이전 79.5MB 쪽이 비정상이었다. 시스템 3.11 에 153개 패키지가 깔려 있어 PyInstaller 가 안 쓰는 것까지 주워 담았고, 깨끗한 venv 로 빌드하니 실제 필요분만 남았다.

### 이전 빌드 대비 정상적인 치환

| 이전 | 이번 | 사유 |
| --- | --- | --- |
| `python311.dll` | `python314.dll` | 이번 전환의 목표 |
| `...cp311-win_amd64.pyd` | `...cp314-win_amd64.pyd` | ABI 태그 |
| `unicodedata2.cp311...pyd` | (없음) | v2는 쓰지 않는다 |
| `libcrypto-3.dll` / `libssl-3.dll` | `libcrypto-3-x64.dll` / `libssl-3-x64.dll` | cryptography 49 → 50 파일명 변경 |
| (없음) | `_zstd.pyd` | Python 3.14 표준 라이브러리 추가분 |

`pyqt6-qt6` 6.11.1 → 6.11.2와 `pyinstaller-hooks-contrib` 2026.6 → 2026.7 때문에 Qt DLL 크기가 조금씩 다르다. 단계 F에서 예고한 대로이고 3.14 때문이 아니라 3.14용 휠이 더 최신이라서다.

## 남은 것 — 실사용 시나리오와 F-③

자동으로 확인할 수 있는 범위는 여기까지다. 아래는 사람이 실제로 써 봐야 한다.

빌드된 exe는 `dist\작가님 힘내세요.exe`에 있고 **아직 설치하지 않았다.** 설치본(`집필프로그램\`)은 2026-08-27 빌드 그대로다.

### 확인 절차

`dist\작가님 힘내세요.exe`를 그대로 실행한다. 설치본을 덮어쓰기 전에 확인하는 것이 안전하다.

1. **로그인** — keyring에서 자격증명을 읽어 Supabase 인증이 통과하는가 (F-③)
2. **작품 목록 조회** — RPC가 `AUTH_REQUIRED` / `FORBIDDEN` 없이 동작하는가 (F-③)
3. **새 작품 생성** — 기존 작품을 고르지 않는다. 과거 기록의 canary `4df996c8…`은 2026-08-29 영구 삭제됐고 `01c1b72f…`는 실제 사용자 작품이다
4. **화 작성 · 저장 · 자동 저장**
5. **한글 / 일본어 / 이모지 파일명** — storage-name v2 경로
6. **동기화 한 바퀴** — 업로드 · 다운로드 왕복
7. **재시작 후 복원** — crash recovery
8. **AI 기능** — 요약 · 초고 · 평가. **이번에 누락됐던 바로 그 경로다.** 반드시 눌러 본다
9. **Windows 재부팅 후 실행**

문제가 없으면 설치본을 교체한다. 되돌릴 때는 `_evidence/python-314-rebuild-20260920/previous/`의 보존본을 쓴다.

### 서버 상태는 그대로다

이번 단계에서 서버에 쓴 것은 없다. 작품 mode/epoch는 `(설정행 없음) 13` + `ID_BASED epoch 1` 1개로 단계 F 전후와 동일하다. 계약 0.3.0 allowlist는 여전히 `enabled=false`이므로, 이 exe가 Staging에 붙어도 **실제 경로는 v1 그대로**다. v2 코드는 들어가 있지만 서버가 허용하지 않는다.

## 실사용 시험 결과 — 2026-09-24〜25 (Python 3.14.7)

위 확인 절차 9항목에 독립 복구 도구와 모델 새로고침 반복을 더해 사용자가 직접 확인했다. 시험 전에 시스템 Python을 3.14.6 → 3.14.7로 올렸으므로 그 버전으로 다시 빌드한 exe로 했다. 서버에는 아무것도 바꾸지 않았다.

### 시험한 빌드

| 차수 | 커밋 | 산출물 | 설치 |
| --- | --- | --- | --- |
| 1차 | `fe88532` | 59,014,490 bytes, `sha256=7719e01ab2a9…` | 2026-09-24 23:49 |
| 2차 | `76d91c5` | 59,013,230 bytes, `sha256=1c96da1ad8b5…` | 2026-09-25 11:09 |

두 빌드 모두 위 "빌드 후 필수 확인"을 통과했다. `python314.dll` 있음, `python311.dll`·`unicodedata2` 없음, `--qt-import-smoke-test` 0, `--runtime-report`는 `3.14.7` · `ok`다. 패키지와 설치·되돌리기 스크립트는 `output/python-3147-usage-test-20260924/`에 있고, 해시는 그 안의 `build-info.json`에 있다.

### 결과

| # | 항목 | 결과 |
| --- | --- | --- |
| 1 | 로그인 | 통과 |
| 2 | 작품 목록 (RPC) | 통과 |
| 3 | 새 작품 생성 | 통과 |
| 4 | 화 작성 · 저장 · 자동 저장 | 통과 |
| 5 | 한글 · 일본어 · 이모지 이름 | 통과 |
| 6 | 동기화 왕복 | 통과 |
| 7 | 강제 종료 후 복원 | 통과 |
| 8 | AI 기능 (요약 · 초고 · 평가) | 통과 |
| 9 | Windows 재부팅 후 실행 | 통과 |
| 10 | 독립 복구 도구 (`writerpad-recovery.cmd`) | 통과 |
| 11 | 사용 가능 모델 새로고침 반복 | 1차 실패 → `76d91c5`에서 수정 → 2차 통과 |

### 11번 — 두 번째 새로고침에 앱이 꺼졌다

1차 빌드에서 **사용 가능 모델 새로고침**을 한 번 누른 뒤 다시 누르면 앱이 종료됐다. Windows 이벤트 로그에는 `Qt6Core.dll`, 예외 코드 `0xc0000409`가 남았다. PyQt가 슬롯의 미처리 예외로 `qFatal`을 부른 흔적이다.

**3.14 결함이 아니다.** 수정 전 코드를 3.11.9와 3.14.7에서 돌리면 둘 다 같은 종료코드로 꺼진다. `model_selector.py`가 조회 완료 신호(`discoveryFinished`)에 `deleteLater`를 걸어 작업 스레드를 지운 뒤에도 그 객체를 계속 들고 있었다. 다음 클릭의 `isRunning()`이 `wrapped C/C++ object of type ModelDiscoveryWorker has been deleted`를 냈다. 조회 완료 신호는 `run()` 안에서 나가므로, 아직 도는 스레드를 지울 수 있는 경합도 함께 있었다.

`76d91c5`에서 `sync_manager`의 기존 방식으로 바꿨다. 정리는 `QThread.finished`에서 하고 참조를 비운다. `tests/test_model_selector_refresh.py`는 수정 전 코드에서 실패하고, 수정 후 3.11.9와 3.14.7 양쪽에서 통과한다. 설정 탭의 **제공자별 사용 가능 모델 조회**도 같은 메서드라 함께 고쳐졌다.

### 3.14.7 재검증

단계 F를 3.14.7에서 다시 돌렸다. 79개 모듈 결과가 3.14.6과 같고, 적합성 벡터 29/29, 골든 매니페스트 992/992다. `76d91c5` 이후에도 80개 모듈(새 회귀 테스트 포함)의 결과가 같다. 기록은 `_evidence/python-3147-reverify-20260924/`에 있다.

### 9/20 빌드보다 2.6MB 작은 이유

9/20 빌드에는 Git for Windows의 `libcrypto-3-x64.dll`·`libssl-3-x64.dll`이 들어 있었다. 해시가 `D:\Program Files\Git\mingw64\bin`의 파일과 같다. Git Bash에서 빌드해서 PyInstaller가 PATH에 있던 OpenSSL을 Qt용으로 함께 담은 것이다.

앱은 QtNetwork를 로컬 소켓(`QLocalServer`/`QLocalSocket`)에만 쓰므로 쓰이지 않던 파일이다. 파이썬 `ssl`이 쓰는 `libcrypto-3.dll`·`libssl-3.dll`(3.14.7은 OpenSSL 3.5.7)은 양쪽 빌드 모두에 있다. 셸에 따라 산출물이 달라지므로 **빌드는 PowerShell에서 한다.**

### 정정 — 설치 위치

위 "남은 것"은 exe를 아직 설치하지 않았고 설치본(`집필프로그램\`)은 08-27 빌드라고 적었다. 그것은 저장소 안의 `작가님 힘내세요\집필프로그램\`이다. 바탕화면 바로가기가 여는 실제 설치 폴더는 `D:\안티그래비티\scratch\집필프로그램\`이고, 2026-09-20 08:01에 이미 3.14.6 빌드(`658696083c4c…`)가 들어가 있었다. 지금은 2차 빌드다. 되돌리기용 보관본은 `output/python-3147-usage-test-20260924/previous/`의 09-20 빌드다.

같은 혼동으로 단계 E-3는 독립복구 사본이 없다고 적었다. 사본은 실제 설치 폴더의 `독립복구-20260910\`에 있었고 `fe88532`에서 함께 고쳤다.

### 판정

단계 G 완료. 실사용 시나리오 11항목이 통과했고 설치본은 3.14.7 빌드다. 단계 H(3.11 제거)의 전제 조건을 충족한다.
