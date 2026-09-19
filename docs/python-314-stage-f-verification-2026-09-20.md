# 단계 F 완료 증거 — 3.14 별도 환경 검증

> 개발지시서: [python-314-transition-development-order-2026-09-19.md](python-314-transition-development-order-2026-09-19.md)
> 단계 B 증거: [python-314-v1-golden-manifest-2026-09-20.md](python-314-v1-golden-manifest-2026-09-20.md)

3.11은 롤백 지점으로 그대로 두고 `.venv314`를 따로 만들어 검증했다. 서버에 쓴 것은 없다.

## 인터프리터 대조 — 차이 0건

79개 테스트 모듈을 3.11과 3.14에서 각각 돌려 모듈 단위로 대조했다.

| | 모듈 수 |
| --- | --- |
| 양쪽 OK | 71 |
| 양쪽 동일하게 비-OK | 8 |
| **결과가 다른 모듈** | **0** |

기록은 `_evidence/python-314-v1-golden-20260920/stage-f-module-comparison.json`에 있다.

### 비-OK 8개의 내역

**4개는 호출 방식 문제였다.** `test_general_editor_runtime`, `test_general_validation_control`, `test_general_validation_runtime`, `test_normal_editor_runtime`은 형제 테스트 모듈을 `tests.` 접두어 없이 임포트한다. `-m unittest tests.<module>`로 주소를 지정하면 `sys.path`에 `tests/`가 없어 `ImportError`가 난다. CI가 쓰는 `-m unittest discover -s tests`로 돌리면 **양쪽 모두 OK**다.

**1개는 정상이다.** `test_independent_project_backup`은 양쪽 `OK (skipped=1)`.

**3개가 실제 기존 문제이고 양쪽에서 똑같이 재현된다.**

| 모듈 | 양쪽 결과 | 성격 |
| --- | --- | --- |
| `test_main_window_constraints` | 요약 없음 | Qt 위젯 테스트가 인터프리터를 죽인다. `.github/workflows/windows-contract.yml`이 `-X faulthandler`를 쓰는 이유로 이미 주석에 적어둔 현상이다 |
| `test_post_coordination_resume` | `failures=1` | 기존 |
| `test_sync_v2` | `failures=1, errors=169` | 기존. 추가로 환경 의존이다 — untracked 원고 디렉터리가 있는 작업 트리에서만 `failures=1`이 붙고, HEAD 프로덕션 코드로도 동일하게 재현된다 |

## 완료 기준 판정

| 항목 | 결과 |
| --- | --- |
| F-① 실제 실행 버전 | `sys.prefix` = `.venv314`, 3.14.6, stdlib Unicode 16.0.0 |
| F-② 간접 의존성 | `pip check` 통과. **21개가 3.11 환경과 버전 상이** (아래) |
| F-③ 자격증명 | 양쪽 `WinVaultKeyring` 25.7.0, 같은 자격증명을 읽는다 |
| F-③ Supabase 인증·RPC | **미수행 — 단계 G로 이월** (아래) |
| F-④ 작품 상태 불변 | 시험 전후 동일. `(설정행 없음) 13` + `ID_BASED epoch 1` 1개 |
| 적합성 벡터 29건 | 3.14에서 **29/29** |
| 골든 매니페스트 992건 | 3.14에서 **992/992 동일** |
| `--qt-import-smoke-test` | 3.14에서 **종료코드 0** |

검증기: [scripts/verify_python314_runtime.py](../scripts/verify_python314_runtime.py). 단계 F의 위험은 3.14가 시끄럽게 실패하는 것이 아니라 **조용히 시스템 3.11로 되돌아가 아무것도 증명하지 못하는 통과를 보고하는 것**이다. `py -3.14`는 venv가 아니라 시스템 3.14를 실행한다는 지시서 경고가 그 지점이다. 그래서 모든 결과에 실행 인터프리터를 함께 기록한다.

## 3.14로 옮겨서 비로소 드러난 결함 1건

`test_sync_contract_stage8.test_frozen_casefold_preserves_previous_runtime_output_for_all_scalars`가 동결 캐스폴드 표와 호스트 CPython이 **모든 스칼라에서** 같다고 단언하고 있었다.

| 인터프리터 | stdlib Unicode | 어긋나는 스칼라 |
| --- | --- | --- |
| 3.11.9 | 14.0.0 | **0** |
| 3.14.6 | 16.0.0 | **27** |

3.11에서 통과한 것은 우연이다. 어긋나는 27개(`U+1C89`, `U+A7CB`, `U+A7CC`, `U+A7DA`, `U+A7DC`, `U+10D50`〜`U+10D65`)는 **전부 v2 baseline(Unicode 14.0.0)에 배정되지 않은 스칼라**라, `normalize_storage_name_v2`가 캐스폴드에 닿기 전에 `STORAGE_NAME_UNASSIGNED`로 거부한다.

**차이는 존재하지만 계약을 통해 도달할 수 없다.** 동결이 설계대로 작동한다는 증거다. 계약이 실제로 받아들이는 스칼라로 범위를 한정하도록 고쳤고, 반대편에서 같은 불변식을 검사하는 [tests/test_frozen_casefold_scalars.py](../tests/test_frozen_casefold_scalars.py)를 함께 두었다.

## F-② 버전이 달라진 간접 의존성

`pip check`는 통과하지만 지시서가 경고한 그대로 버전은 달라졌다. 고정한 6개는 정확히 핀된 버전이다.

| 패키지 | 3.11 | 3.14 | 왜 보는가 |
| --- | --- | --- | --- |
| `cryptography` | 49.0.0 | **50.0.1** | 메이저 상승. keyring·인증 경로 |
| `setuptools` | 65.5.0 | **84.0.0** | 19 메이저 점프 |
| `pyqt6-qt6` | 6.11.1 | 6.11.2 | **Qt 런타임 DLL 자체가 다르다** |
| `pyqt6_sip` | 13.11.1 | 13.12.0 | PyQt 바인딩 |
| `pyinstaller-hooks-contrib` | 2026.6 | 2026.7 | 번들 구성에 직접 영향 |
| `cffi` | 2.0.0 | 2.1.1 | cryptography 하부 |

나머지 15개는 패치 수준이다. 이 비교는 **시스템 3.11(153개)** 대 **venv 3.14(48개)**라 완전한 동일 조건이 아니다. 3.11 쪽이 깨끗한 venv가 아니므로 공유 패키지의 버전만 의미가 있다.

## 단계 G 진입 전 짚어둘 것

### 1. 위 두 패키지는 빌드 산출물에 그대로 반영된다

`pyqt6-qt6` 6.11.1 → 6.11.2와 `pyinstaller-hooks-contrib` 2026.6 → 2026.7이 번들에 들어간다. 지시서가 요구한 "이전 빌드와 번들 목록 비교"에서 Qt DLL 차이로 나타날 것이고 **그건 정상이다.** 3.14 때문이 아니라 3.14용 휠이 더 최신이라서 생기는 차이다.

### 2. 설치본이 최신 빌드가 아니다

| 경로 | 날짜 | 크기 |
| --- | --- | --- |
| `집필프로그램\작가님 힘내세요.exe` | 2026-08-27 | 96,248,175 |
| `dist\작가님 힘내세요.exe` | 2026-09-13 | 79,543,070 |

지시서가 기준으로 삼은 "현재 exe(79MB, 2026-09-13)"는 `dist` 쪽이다. 설치본에는 `python311.dll`과 `unicodedata2`가 둘 다 들어 있고 `python314.dll`은 없다.

**"이전 빌드와 비교"의 기준을 둘 중 어느 것으로 잡을지 먼저 정해야 한다.** 섞으면 3.14 전환과 무관한 2주치 변경이 차이로 섞여 들어온다.

### 3. 시험 대상 작품을 새로 만들어야 한다

과거 기록의 canary `4df996c8…`은 2026-08-29 영구 삭제됐고, `01c1b72f…`(코드에 `LIVE_PROJECT_ID`로 박힌 **실제 사용자 작품**)는 스테이징에도 prod에도 없다 — 운영 프로젝트 쪽에 있다고 보는 것이 맞다.

2026-09-20 기준 스테이징에는 이 두 id가 없으므로, **단계 G의 실사용 시나리오 시험은 새 작품을 만들어 수행한다.** 기존 작품을 골라 쓰지 않는다.

### 4. F-③의 나머지 절반

Supabase 인증과 RPC 호출은 저장된 refresh 토큰을 쓰게 되는데, 토큰이 회전하면 앱에 저장된 자격증명이 무효화돼 로그아웃될 수 있다. 별도 프로세스에서 돌리지 않고 **단계 G에서 3.14로 빌드한 exe를 직접 실행해 확인한다.** 확인할 것은 세 가지다.

- 로그인이 되는가 (keyring → Supabase 인증)
- 작품 목록이 조회되는가 (RPC 가 `AUTH_REQUIRED` / `FORBIDDEN` 없이 동작)
- 새 작품 생성·저장·동기화가 한 바퀴 도는가

exe 실행이 곧 F-③의 답이므로 단계 G의 실사용 시나리오와 하나로 묶어 수행한다.
