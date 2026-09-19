# iPad 쪽에 전달 — Windows가 계약 0.3.0 / storage-name-v2 / Python 3.14로 넘어갔다

> 작성 2026-09-20. Windows 저장소 `ChocoS-yrup/WriterPad_main`, 브랜치 `feat/contract-handshake-closed-gate`.
> 근거 커밋: `1e95bad` → `2cefded`. 증거: `_evidence/python-314-v1-golden-20260920/`, `_evidence/python-314-rebuild-20260920/`

**서버의 스키마·데이터·allowlist·작품 상태는 바꾸지 않았다.** 아래는 전부 Windows 클라이언트 쪽 변경과, 서버를 질의해 실측한 결과다.

한 가지만 정확히 적어 둔다. `supabase` CLI 가 Management API 질의를 위해 `cli_login_postgres` 역할을 서버에 만들었다(`member_of={postgres}`, superuser 아님, bypassrls 아님, `rolvaliduntil 2026-09-19 23:30:49+00` 로 이미 만료). CLI 의 표준 접근 수단이고 스키마나 데이터와는 무관하지만, "아무것도 쓰지 않았다" 는 아니므로 남긴다.

## 한 줄 요약

Windows는 계약 0.3.0(`abbd234c…`)을 핀하고 storage-name-v2로 키를 계산하게 됐지만, **서버 allowlist의 0.3.0 행이 아직 `enabled=false`라 실제 동작 경로는 여전히 v1이다.** iPad가 지금 당장 고쳐야 할 것은 없다.

## Windows 쪽에서 바뀐 것

| | 이전 | 지금 |
| --- | --- | --- |
| 계약 | 0.2.0 `416c1b99…` | **0.3.0 `abbd234c…`** |
| canonical bytes | 23,256 | 24,777 |
| storage-name 알고리즘 | `storage-name-v1` | **`storage-name-v2`** |
| client capability | `storage_name_v1` | **`storage_name_v2`** |
| Python | 3.11.9 + `unicodedata2 15.0.0` | **3.14.6, `unicodedata2` 제거** |
| exe 내장 런타임 | `python311.dll` | **`python314.dll`** |

`sync-contract/`는 `ChocoS-yrup/Writerpad@3843b05a`에서 그대로 가져왔다. 두 저장소의 `canonical_contract_sha256`가 이제 같다.

## 이게 iPad에 영향이 있나 — 지금은 없다

**세 개의 관문이 차례로 닫혀 있다.**

| # | 관문 | 현재 상태 |
| --- | --- | --- |
| 1 | 서버 allowlist `0.3.0 enabled` | **`false`** |
| 2 | Windows 로컬 `contract_path_enabled` | 18개 작품 전부 **`0`** |
| 3 | 작품 `project_sync_mode` | 13개 설정행 없음(=LEGACY/0), 1개 `ID_BASED/1` |

관문 1이 닫혀 있는 한 `get_sync_handshake`는 0.3.0에 대해 `supported: false`를 돌려주고, Windows는 계약 경로를 열 수 없다. 서버 함수를 직접 읽어 확인했다 — 허용 목록에 없는 다이제스트는 **예외를 던지지 않고** 정상 응답에 `supported: false`로 답한다. 예외는 `AUTH_REQUIRED` / `INVALID_ARGUMENT` / `FORBIDDEN` 셋뿐이다.

## 검증된 사실 — 전환해도 키는 하나도 안 바뀐다

2026-09-20에 Staging에서 읽기 전용으로 실측했다.

| 검사 | 결과 |
| --- | --- |
| 계약 0.3.0 벡터 29건, 서버 `private.storage_name_v2_result` 대 계약 | **29/29 일치**, 오류 코드 불일치 0 |
| 실제 이름 374건, **서버 v2 대 Windows v2** | **374/374 동일** |
| 같은 374건에서 v1과 v2가 다른 키를 내는 것 | **0건** |
| 같은 374건, Python 3.11 대 3.14 재계산 | **992/992 동일** (행 단위) |

374건은 Staging 폴더·문서 이름 전체와 Windows 로컬 SQLite, 로컬 원고 디렉터리를 합친 고유 집합이다.

**즉 0.3.0으로 올려도 기존 `storage_name_key`는 단 하나도 바뀌지 않는다.** 마이그레이션이 데이터를 건드릴 일이 없다.

## 서버는 이미 v2를 들고 있다

Staging에 아래 함수가 **이미 배포돼 있다.** 따로 배포할 것이 없다.

```
private.storage_name_v2
private.storage_name_v2_is_assigned
private.storage_name_v2_is_excluded
private.storage_name_v2_result
```

`public.validate_contract_request`도 0.3.0 다이제스트를 이미 분기 처리한다.

```sql
if v_allowlist.canonical_contract_sha256 = 'abbd234c…' then
  v_required := array[..., 'storage_name_v2', 'document_commit_v1'];
else
  v_required := array[..., 'storage_name_v1', 'document_commit_v1'];
```

**빠진 것은 allowlist 행의 `enabled` 플래그 하나뿐이다.**

## 인계 문서의 내용 중 정정할 것

이전 인계 문서(`next-task-python-314-local-implementation-handoff-2026-09-19.md`)가 이렇게 적었다.

> `20260811020000_sync_contract_0_1_0_rpcs.sql`의 실행 경로 4곳(`368`, `418`, `1619`, `1798`)에 0.2.0 다이제스트가 하드코딩돼 있다.

**배포된 Staging에서는 1곳뿐이다.** 그 파일 이후에 적용된 마이그레이션 4개(`20260813063251`, `20260906164945`, `20260910062649`, `20260910073750`)가 나머지를 재정의한 것으로 보인다. 전체 스키마에서 리터럴을 검색한 결과다.

```
0.2.0 리터럴을 가진 함수: private.enforce_document_write_boundary  (1개)
```

### 그 1곳이 무엇인가

RPC가 아니라 **트리거**다.

```
CREATE TRIGGER document_versions_contract_boundary
  BEFORE INSERT ON public.document_versions
  FOR EACH ROW EXECUTE FUNCTION private.enforce_document_write_boundary()
```

동작은 이렇다.

```sql
if v_mode <> 'LEGACY' and not exists (
  ... and batch.sync_protocol_version = 3
      and batch.canonical_contract_sha256 = '416c1b99…'   -- 0.2.0 고정
) then
  raise exception 'PROTOCOL_TOO_OLD';
```

**작품이 `LEGACY`가 아니면, 문서 쓰기는 0.2.0 계약 배치에서 온 것이어야 한다.**

### 이건 iPad만의 문제가 아니다

인계 문서는 이걸 "iPad가 차단된다"로 적었는데, 실제로는 더 넓다. 작품이 `MIGRATING`/`ID_BASED`일 때:

| 쓰기 주체 | 결과 |
| --- | --- |
| iPad (legacy 경로, CONTRACT_BATCH 아님) | `PROTOCOL_TOO_OLD` |
| **Windows 0.3.0 클라이언트** (배치 다이제스트 `abbd234c…`) | **`PROTOCOL_TOO_OLD`** |
| Windows 0.2.0 클라이언트 | 통과 |

**즉 이 트리거를 고치지 않으면, 작품을 전환하는 순간 새 Windows 빌드도 iPad와 똑같이 막힌다.** 0.3.0을 허용하려면 이 비교를 allowlist 조회로 바꾸거나 두 다이제스트를 모두 허용해야 한다.

작품이 `LEGACY/0`에 머무는 동안에는 이 트리거가 아무 검사도 하지 않는다. 지금 상태가 그렇다.

### 이미 `ID_BASED`인 작품이 하나 있다

인계 문서는 "이번 범위는 전부 `LEGACY/0`"이라고 했지만 실제로는 아니다.

```
일반동기화 검증 20260910   mode=ID_BASED  epoch=1  contract=416c1b99…(0.2.0)
```

지금은 계약이 0.2.0이라 트리거와 맞아 안전하다. **이 작품의 계약을 0.3.0으로 올리면 트리거 수정 없이는 양쪽 클라이언트가 모두 막힌다.**

## 트리거 수정은 iPad 쪽 저장소 소관이다

Staging에 적용된 마이그레이션 9개는 전부 `ChocoS-yrup/Writerpad`에 있다. Windows 저장소의 `supabase/migrations/` 7개는 **하나도 Staging에 적용돼 있지 않다** — 별개 계보다.

```
Staging 적용분(9)  20260811000000  20260811010000  20260811020000  20260813063251
                   20260814182850  20260820113209  20260906164945  20260910062649
                   20260910073750

WriterPad_main 로컬(7, 미적용)  20260714000000  20260714010000  20260728010000
                                20260728020000  20260729010000  20260803010000
                                20260829000000
```

따라서 위 트리거를 고치는 마이그레이션은 **iPad 쪽에서 작성하고 적용해야 한다.** Windows 쪽에서 만들어 보낼 수 있는 것이 아니다.

## 왕복 시험 후보가 이미 있다

2026-09-20에 Windows 3.14 빌드를 시험하면서 새 작품이 하나 만들어졌다.

```
53b96759…   생성 2026-09-19 23:07 UTC
            mode=(설정행 없음, 즉 LEGACY/0)  epoch=0
            폴더 11  문서 26  storage_name_key 채워진 문서 0
```

실제 원고가 아니라 시험용이므로 C-④ 왕복 시험 대상으로 그대로 쓸 수 있다. **iPad에서 이 작품이 보이는지 확인하는 것이 왕복 시험의 1단계다.**

참고로 이 작품이 정상 생성·동기화됐다는 것은 3.14 빌드에서 keyring → Supabase 인증 → RPC 경로가 동작한다는 실증이기도 하다.

## iPad 쪽에서 지금 할 일

**없다.** 코드 변경 없이 그대로 두면 된다. 근거는 이렇다.

- iPad Swift에 `contract` 언급이 0개이고, `get_sync_handshake`를 호출하지 않는다. allowlist를 켜도 iPad가 보는 응답은 달라지지 않는다.
- 서버 쓰기 경로(`apply_structure_intent`, `document_commit_legacy`, `document_relative_path`)는 여전히 `storage_name_v1`을 쓴다. v2는 `validate_contract_request`를 통한 계약 경로에서만 닿는다.
- 어차피 v1과 v2가 우리 데이터에서 같은 키를 낸다(374건 0 불일치).

## iPad 쪽에서 알고 있어야 할 것

1. **`storage_name_key`를 iPad가 직접 계산한다면** 언젠가 v2로 맞춰야 한다. 0.3.0 계약 자산(`unicode/` 4개 + `conformance_vectors/storage-name-v2.json` 29건)은 `Writerpad` 저장소에 이미 released로 들어 있다. 다만 우리 데이터 기준으로는 v1과 v2 결과가 같으므로 급하지 않다.
2. **v2가 v1과 갈리는 지점**은 두 가지다. v2는 Unicode 14.0.0 배정 baseline 밖의 문자를 `STORAGE_NAME_UNASSIGNED`로 거부하고, 구분자 검사를 NFKC **이후에** 수행한다(v1은 이전). 그래서 `U+2100`(℀ → `a/c`)처럼 정규화 결과로 `/`가 생기는 문자를 v1은 통과시키고 v2는 거부한다.
3. **v2는 호스트 Unicode 버전에 의존하지 않는다.** 동결 표를 쓴다. Windows에서 Python 3.11(Unicode 14.0.0)과 3.14(16.0.0)가 같은 답을 내는 것을 992건으로 확인했다. iPad 구현도 ICU 버전에 결과가 흔들리면 안 된다.
4. **트리거 수정이 예정돼 있다.** 위의 `document_versions_contract_boundary`. 이건 iPad가 쓰는 문서 쓰기 경로에 걸려 있으므로, 수정 시점을 공유해야 한다.

## 확인이 필요한 것 — iPad 쪽에서 답해 주면 좋겠다

1. iPad가 `storage_name_key`를 직접 계산하는가, 아니면 서버가 채우는 값을 받기만 하는가?
2. iPad가 호출하는 RPC 목록. Windows 쪽 기록으로는 7종인데 어느 것인지 확정되면 트리거 수정 영향 범위를 정확히 잡을 수 있다.
3. iPad가 `project_sync_settings.active_contract_sha256`를 읽는 곳이 있는가? 있다면 0.3.0 전환 시 그 경로도 봐야 한다.
