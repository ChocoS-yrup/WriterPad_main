# Python 3.11 → 3.14 전환 프롬프트 (재사용)

새 프로젝트 세션에 아래 `---` 사이를 통째로 붙여넣으면 바로 진행된다.

2026-09-20에 WriterPad_main을 실제로 전환하며 겪은 것들을 넣었다. 일반론이 아니라 **그때 실제로 물린 함정**이다.

---

# Python 3.11 → 3.14 전환

이 프로젝트를 Python 3.11에서 3.14로 옮긴다. 목표는 **3.11 설치본을 지워도 되는 상태**를 만드는 것이다.

## 원칙

- **3.11을 먼저 지우지 않는다.** 전 과정에서 3.11이 롤백 지점으로 살아 있어야 한다. 제거는 맨 마지막이고 별도 승인 사안이다.
- **각 단계가 끝나면 검증 결과와 다음 변경 범위를 제시하고 승인을 받는다.** 중간에 범위를 넓히지 않는다.
- **추정해서 보고하지 않는다.** "될 것이다"가 아니라 실행한 명령과 그 출력으로 말한다.
- **통과했다는 보고가 가장 위험하다.** 아래 "조용한 실패" 절을 먼저 읽어라.

## 단계 0 — 현재 상태 동결

옮기기 전에 되돌아올 지점을 만든다.

1. `git status`로 미커밋 변경을 확인한다. 쌓여 있으면 **그것이 파이썬 버전보다 급한 리스크다.** 분류해서 커밋하거나 최소한 브랜치로 묶는다.
2. 소스·빌드산출물·캐시·진단자료를 분류한다. **`git clean` 계열을 쓰지 않는다.** 자동 일괄 삭제 금지.
3. Git 밖의 것(빌드 산출물, 진단 스냅샷, 로컬 데이터)이 있으면 별도로 복사해 보존한다.

> Git 커밋은 `core.autocrlf` 설정에 따라 줄바꿈이 정규화되므로 **바이트 단위 복원 지점이 아니다.** 바이트 그대로 필요하면 파일 복사본을 따로 둬라.

## 단계 1 — 조사 (아직 아무것도 고치지 않는다)

### 1-1. 현재 환경을 기록한다

```bash
python -c "import sys; print(sys.version, sys.executable, sys.prefix)"
python -m pip list --format=freeze > _baseline-pip-311.txt
wc -l _baseline-pip-311.txt
```

**이 숫자를 기억해라.** 시스템 파이썬이면 수십~수백 개가 나온다. 그 대부분은 이 프로젝트와 무관하고, 바로 그게 단계 3의 함정이다.

### 1-2. 실제 임포트를 전수 조사한다

요구사항 파일을 믿지 마라. **코드가 무엇을 임포트하는지 직접 본다.**

**대상 파일은 `git ls-files`로 잡는다.** `os.walk`로 훑으면 `.venv`의 벤더 코드와 보관 디렉터리(`_evidence`, 스냅샷, 과거 소스 사본)까지 섞여 목록이 쓸모없어진다. 실제로 이 프로젝트에서 walk 방식은 서드파티 26종에 벤더 코드 경로를, git 방식은 실제 19종을 냈다.

```bash
python - <<'PY'
import ast, io, os, subprocess, sys
out = subprocess.run(["git", "ls-files", "-z", "*.py"], capture_output=True, check=True)
paths = [p for p in out.stdout.decode("utf-8").split("\0") if p]
stdlib = set(sys.stdlib_module_names)
local = {os.path.splitext(os.path.basename(p))[0] for p in paths}
local |= {p.split("/")[0] for p in paths if "/" in p}          # 로컬 패키지 디렉터리
found, lazy = {}, []
for p in paths:
    try:
        tree = ast.parse(io.open(p, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError, OSError):
        continue
    inside = {n for fn in ast.walk(tree)
              if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
              for n in ast.walk(fn)}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for n in names:
            if n in stdlib or n in local or n.startswith("_"):
                continue
            found.setdefault(n, set()).add(p)
            if node in inside:
                lazy.append((p, node.lineno, n))
print(f"파일 {len(paths)}개, 서드파티 임포트 {len(found)}종")
for n in sorted(found):
    mark = "  <= 지연 임포트 포함" if any(x[2] == n for x in lazy) else ""
    print(f"  {n:<20} {len(found[n]):>3}개 파일  예: {sorted(found[n])[0]}{mark}")
print()
print(f"=== 함수 안 지연 임포트 {len(lazy)}건 ===")
for p, line, n in sorted(lazy):
    print(f"  {p}:{line}  ->  {n}")
PY
```

git 저장소가 아니면 대상 목록을 직접 지정해라. 보관·스냅샷 디렉터리를 반드시 빼라.

이 목록과 요구사항 파일을 대조한다. **목록에 있는데 요구사항 파일에 없는 것이 있으면 그게 곧 터질 지뢰다.** 지금까지 동작한 이유는 시스템 파이썬에 우연히 깔려 있었기 때문이다.

### 1-3. 지연 임포트 목록을 따로 챙긴다

위 스크립트가 함께 출력한다. **함수 안 임포트는 패키지가 없어도 프로그램이 시작되고 스모크 테스트도 통과한다.** 그 기능을 쓸 때에야 `ImportError`가 난다.

이 목록은 버리지 말고 단계 3-4의 실사용 점검 항목으로 그대로 넘겨라. 이 프로젝트에서 빌드에 LLM 패키지 3개가 통째로 빠진 것을 자동 검사가 전부 놓쳤고, 원인이 정확히 이것이었다.

동적 임포트도 본다.

```bash
rg -n "importlib\.import_module|__import__\(" --glob '!.venv*'
```

### 1-4. cp314 휠이 없는 패키지를 찾는다

```bash
python -m pip download --only-binary=:all: --python-version 314 \
  --implementation cp --platform win_amd64 -d /tmp/wheeltest \
  -r <요구사항파일>
```

이 명령은 간접 의존성까지 전부 받아 보므로, 직접 명시하지 않은 패키지의 휠 부재도 함께 드러난다. 실패 메시지가 **cp314 휠이 있는 버전 목록을 같이 알려주므로** 올릴 수 있는지도 바로 판단된다.

```
ERROR: Could not find a version that satisfies the requirement unicodedata2==15.0.0
       (from versions: 17.0.0, 17.0.1, 18.0.0rc1, 18.0.0)
```

실패하는 패키지가 **유일한 진짜 블로커**다. 각각에 대해 판단한다.

- 버전을 올리면 휠이 있는가
- 그 의존을 없앨 수 있는가 (다른 작업의 부산물로 사라지기도 한다)
- 순수 파이썬이라 휠이 무관한가
- 소스 빌드가 가능한가 (MSVC 빌드 도구가 있는가)

**없앨 수 있다면 그게 최선이다.** 이 프로젝트에서는 `unicodedata2==15.0.0`이 블로커였는데, 알고리즘을 v2로 바꾸니 의존 자체가 사라졌다.

지워질 패키지가 유일본이면 **휠을 먼저 보존해라.** 설치본에서 복원할 수 있다.

```bash
python - <<'PY'
# site-packages 의 .pyd + dist-info 로 설치 가능한 휠을 되만든다. 네트워크 불필요.
import base64, csv, hashlib, io, os, sys, zipfile
SITE = next(p for p in sys.path if p.endswith("site-packages"))
PYD, DIST, OUT = "<이름>.cpXXX-win_amd64.pyd", "<이름>-<버전>.dist-info", "<이름>-<버전>-cpXXX-cpXXX-win_amd64.whl"
def u(d): return base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b"=").decode()
members = [(PYD, os.path.join(SITE, PYD))] + [
    (f"{DIST}/{e}", os.path.join(SITE, DIST, e))
    for e in sorted(os.listdir(os.path.join(SITE, DIST)))
    if e not in ("RECORD", "INSTALLER", "REQUESTED")]
rec = io.StringIO(); w = csv.writer(rec, lineterminator="\n")
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for arc, path in members:
        data = open(path, "rb").read()
        z.writestr(arc, data); w.writerow([arc, f"sha256={u(data)}", len(data)])
    w.writerow([f"{DIST}/RECORD", "", ""]); z.writestr(f"{DIST}/RECORD", rec.getvalue())
print(OUT, os.path.getsize(OUT))
PY
```

### 1-5. 표준 라이브러리 제거분을 확인한다

**3.11에 있고 3.14에 없는 모듈 25개** (2026-09-20 실측).

```
aifc  asynchat  asyncore  audioop  cgi  cgitb  chunk  crypt  distutils
imghdr  imp  lib2to3  mailcap  msilib  nis  nntplib  ossaudiodev  pipes
smtpd  sndhdr  spwd  sunau  telnetlib  uu  xdrlib
```

```bash
rg -n '\b(aifc|asynchat|asyncore|audioop|cgi|cgitb|chunk|crypt|distutils|imghdr|imp|lib2to3|mailcap|msilib|nis|nntplib|ossaudiodev|pipes|smtpd|sndhdr|spwd|sunau|telnetlib|uu|xdrlib)\b' --glob '*.py' --glob '!.venv*'
```

`distutils`는 `setuptools`가 끌고 오기도 하므로 **의존 패키지 쪽도** 본다.

### 1-6. 사라진 API를 확인한다

3.14에서 **AttributeError가 되는 것들** (실측).

| 호출 | 3.11 | 3.14 |
| --- | --- | --- |
| `ast.Str` (`ast.Num`, `ast.Bytes` 등 포함) | 동작 | **AttributeError** |
| `importlib.abc.ResourceReader` | 동작 | **AttributeError** |
| `pkgutil.find_loader` | 동작 | **AttributeError** |
| `datetime.utcnow()` | 조용함 | DeprecationWarning |
| `datetime.utcfromtimestamp()` | 조용함 | DeprecationWarning |
| `locale.getdefaultlocale()` | DeprecationWarning | DeprecationWarning |

```bash
rg -n 'ast\.(Str|Num|Bytes|NameConstant|Ellipsis)\b|importlib\.abc\.ResourceReader|pkgutil\.find_loader|utcnow\(\)|utcfromtimestamp\(' --glob '*.py' --glob '!.venv*'
```

### 1-7. Unicode 의존을 확인한다

`unicodedata.unidata_version`이 **3.11은 14.0.0, 3.14는 16.0.0**이다.

```bash
rg -n 'unicodedata|unidata_version|casefold|normalize\(|NFKC|NFKD|NFC|NFD' --glob '*.py' --glob '!.venv*'
```

키·해시·식별자를 유니코드 정규화로 만드는 코드가 있으면 **버전이 바뀌는 순간 값이 달라질 수 있다.** 그런 코드가 있으면 전환 전에 현재 값을 전수 기록해 두고, 전환 후 같은 입력으로 재계산해 대조해라. 동결 테이블을 쓰는 설계가 아니라면 이게 가장 위험한 항목이다.

## 단계 2 — 이동

### 2-1. 3.11은 그대로 두고 별도 venv를 만든다

```bash
py -3.14 -m venv .venv314
.venv314/Scripts/python.exe -m pip install --upgrade pip
.venv314/Scripts/python.exe -m pip install -r <요구사항파일>
```

`.gitignore`에 `.venv314/`를 넣는다.

> **`py -3.14`는 venv 생성에만 쓴다.** 그 뒤로 `py -3.14`는 venv가 아니라 **시스템 3.14**를 실행한다. 이후 모든 명령은 `.venv314\Scripts\python.exe`를 명시한다.

### 2-2. 설치 결과를 3.11과 대조한다

```bash
.venv314/Scripts/python.exe -m pip check
.venv314/Scripts/python.exe -m pip list --format=freeze > _new-pip-314.txt
diff <(sort _baseline-pip-311.txt) <(sort _new-pip-314.txt)
```

`pip check`가 통과해도 **간접 의존성 버전은 달라졌을 수 있다.** 달라진 것을 목록으로 뽑아 보고해라. 암호화·네이티브 확장·빌드 도구 계열의 메이저 상승은 따로 짚는다.

### 2-3. 코드를 고친다

단계 1에서 찾은 것만 고친다. 그 이상은 하지 않는다.

## 단계 3 — 검사

### 조용한 실패 — 이 절을 반드시 읽어라

**3.14에서 시끄럽게 실패하는 것은 위험하지 않다. 조용히 통과하는 것이 위험하다.** 실제로 겪은 것들이다.

**① 시스템 파이썬으로 되돌아간 통과**

`py -3.14`나 맨 `python`이 venv를 가리킨다고 가정하면, 시험이 엉뚱한 인터프리터에서 돌고 "통과"가 아무것도 증명하지 않는다.

→ **모든 결과에 실행 인터프리터를 함께 기록한다.**

```bash
.venv314/Scripts/python.exe -c "import sys; print(sys.version.split()[0], sys.executable, sys.prefix, sys.prefix != sys.base_prefix)"
```

`sys.prefix`가 `.venv314`가 아니면 그 결과는 버린다.

**② 요구사항 파일에 없는 의존성**

깨끗한 venv는 요구사항 파일에 적힌 것만 갖는다. 시스템 파이썬에 깔려 있던 덕에 동작하던 것이 전부 사라진다. **지연 임포트라면 앱이 뜨고 스모크 테스트도 통과한다.**

→ 단계 1-2·1-3의 목록을 **한 줄씩 실제로 임포트해 본다.**

```bash
.venv314/Scripts/python.exe -c "
import importlib
for m in ['<1-2 에서 찾은 것 전부>']:
    try:
        importlib.import_module(m); print('  OK  ', m)
    except Exception as e:
        print('  FAIL', m, type(e).__name__, e)
"
```

**③ 호스트 버전에 우연히 맞던 테스트**

호스트 동작을 정답으로 단언하는 테스트는 한 버전에서 우연히 통과한다. 이 프로젝트에서는 동결 캐스폴드 표를 CPython `str.casefold()`와 전 스칼라 비교하는 테스트가 있었다. **3.11에서 어긋나는 스칼라 0개, 3.14에서 27개.**

→ 테스트가 깨지면 **"코드가 틀렸나, 테스트가 틀렸나"를 먼저 판정해라.** 호스트 라이브러리를 정답으로 삼고 있었다면 테스트가 틀린 것이다. 버전 독립적인 불변식으로 다시 써라.

**④ 기준선이 같은 조건이 아니었던 비교**

별도 워크트리·클린 체크아웃에서 "이전"을 재면 untracked 파일이 없어서 다른 결과가 나온다. 없는 회귀를 쫓게 된다.

→ **같은 트리에서** 코드만 이전 버전으로 되돌려 비교해라.

**⑤ 호출 방식이 만든 가짜 실패**

형제 테스트 모듈을 접두어 없이 임포트하는 테스트는 `-m unittest tests.X`로는 `ImportError`, `-m unittest discover -s tests`로는 통과한다.

→ **CI가 쓰는 방식과 똑같이 돌려라.** 다르면 그 차이부터 설명해라.

**⑥ 인코딩 (한국어 Windows에서 특히)**

기본 텍스트 인코딩이 `cp949`다. 한글이 든 출력·파일을 UTF-8로 가정하면 대리 문자(`\udcec`)로 깨진다.

→ 파일 입출력에 `encoding="utf-8"`을 명시하고, 서브프로세스 출력은 바이트로 받아 직접 디코딩한다. 진단 출력을 파일로 남긴다면 `json.dumps(..., ensure_ascii=True)`가 안전하다.

### 3-1. 테스트를 양쪽에서 돌리고 모듈 단위로 대조한다

```bash
for m in $(ls tests/test_*.py | sed 's|tests/||;s|\.py$||' | sort); do
  a=$(python -X faulthandler -m unittest discover -s tests -p "$m.py" 2>&1 | grep -E '^(OK|FAILED)')
  b=$(.venv314/Scripts/python.exe -X faulthandler -m unittest discover -s tests -p "$m.py" 2>&1 | grep -E '^(OK|FAILED)')
  printf "%-44s 3.11=%-28s 3.14=%s\n" "$m" "${a:-<요약없음>}" "${b:-<요약없음>}"
done
```

**판정 기준은 "전부 통과"가 아니라 "양쪽이 같은가"다.** 원래 실패하던 것은 3.14 탓이 아니다. 차이가 나는 모듈만 파고들어라.

요약이 안 나오는 모듈은 인터프리터가 죽은 것이다. `-X faulthandler`를 붙여 C 스택을 받아라.

### 3-2. 값이 바뀌지 않았음을 증명한다

정규화·해시·직렬화·정렬로 값을 만드는 코드가 있으면, **같은 입력을 양쪽에서 돌려 결과를 대조해라.** 테스트 통과와는 별개 문제다.

이 프로젝트에서는 실데이터 992건을 3.11과 3.14에서 재계산해 992/992 동일을 확인했다.

### 3-3. 배포 산출물을 검사한다 (빌드하는 프로젝트라면)

**크기 변화를 반드시 설명해라.** 이 프로젝트에서 같은 "크기 감소"가 두 번 났는데 성격이 정반대였다.

| | 첫 빌드 | 고친 빌드 |
| --- | --- | --- |
| 크기 | 55.5 MB | 61.6 MB |
| 정체 | **의존성 3개 누락 — 실제 결함** | 안 쓰는 패키지 제외 — 정상 |

**둘 다 문자열 검사와 스모크 테스트를 통과했다.** 크기만으로는 구분되지 않는다.

세 가지를 같이 본다.

1. **번들 목록 직접 열람** — 무엇이 사라지고 무엇이 새로 들어왔는지 이전 빌드와 대조
2. **실행 중 자기보고** — 산출물이 실제로 실행된 상태에서 `sys.version`과 주요 패키지 버전을 출력. **가장 확실하다**
3. **문자열 검사** — 구버전 런타임 DLL이 남아 있지 않은지

자기보고 플래그를 만들 때 **지연 임포트 대상까지 실제로 임포트해라.** 안 그러면 위의 ①번 결함을 못 잡는다.

> 프리즈된 windowed 빌드는 `PYTHONIOENCODING`을 무시하고 stdout을 콘솔 코드페이지로 쓴다. 진단 출력은 ASCII로 내라.

### 3-4. 실사용 시나리오

자동 검사로 닿지 않는 것은 사람이 해야 한다. 무엇을 확인해야 하는지 **목록으로 만들어 제시해라.** 특히 **단계 1-3에서 찾은 지연 임포트 경로**를 빠짐없이 넣어라 — 그게 자동 검사가 놓치는 바로 그 지점이다.

## 보고 형식

각 단계 끝에 이렇게 보고한다.

- 실행한 명령과 **실제 출력**
- 기대값과 실측값을 나란히
- 3.11 대비 달라진 것과 **그 이유**
- 판단이 필요한 지점과 선택지

**아직 안 한 것을 했다고 하지 말고, 확인 못 한 것을 확인했다고 하지 마라.** "통과"라고 적기 전에 그 통과가 무엇을 증명하는지 한 번 더 생각해라.

---
