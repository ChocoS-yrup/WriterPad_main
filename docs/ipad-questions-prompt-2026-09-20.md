# iPad 쪽에 물어볼 것 — 그대로 붙여넣는 프롬프트

아래 `---` 사이를 통째로 복사해서 맥북의 iPad 작업 세션에 붙여넣으면 된다.

---

# Windows 쪽에서 묻는다 — 계약 0.3.0 전환 전 확인

너는 `ChocoS-yrup/Writerpad`(iPad 앱 + 서버 마이그레이션 + 계약) 쪽을 보고 있다. 나는 `ChocoS-yrup/WriterPad_main`(Windows 클라이언트) 쪽이다.

## 무슨 일이 있었나

Windows가 2026-09-20에 계약 **0.2.0 → 0.3.0**(`abbd234c7b65d422c2e43d468f4f724e069ede26a3d24be22eb8b35cce8ebf2c`), **storage-name-v1 → v2**, **Python 3.11 → 3.14**로 넘어갔다. `sync-contract/`는 너희 저장소 `3843b05a`에서 그대로 가져왔고 두 저장소의 canonical digest가 이제 같다.

**서버는 아직 아무것도 바꾸지 않았다.** Staging(`mhpnszcorfzrvhyondxr`)의 allowlist에서 0.3.0 행은 여전히 `enabled=false`다. 그래서 지금 당장 iPad에 영향은 없다.

내가 Staging을 읽기 전용으로 실측한 것들이다.

- `private.storage_name_v2_result`를 포함한 v2 함수 4종이 **이미 배포돼 있다**
- 계약 0.3.0 벡터 29건을 서버에서 직접 돌려 **29/29 일치**, 오류 코드 불일치 0
- 실제 이름 374건(Staging 폴더·문서 + Windows 로컬)으로 **서버 v2 대 Windows v2 → 374/374 동일**
- 같은 374건에서 **v1과 v2가 다른 키를 내는 것은 0건**. 즉 0.3.0을 켜도 기존 `storage_name_key`는 하나도 안 바뀐다
- `public.validate_contract_request`가 0.3.0 다이제스트를 이미 분기 처리하고 `storage_name_v2` capability를 요구한다

## 내가 찾은 지뢰 하나

이전 인계 문서는 "0.2.0 다이제스트 하드코딩이 RPC 4곳"이라고 적었는데, **배포된 Staging에서 전체 스키마를 검색하니 1곳뿐이었다.** 그것도 RPC가 아니라 트리거다.

```
CREATE TRIGGER document_versions_contract_boundary
  BEFORE INSERT ON public.document_versions
  FOR EACH ROW EXECUTE FUNCTION private.enforce_document_write_boundary()
```

```sql
if v_mode <> 'LEGACY' and not exists (
  ... and batch.sync_protocol_version = 3
      and batch.canonical_contract_sha256 = '416c1b99…'   -- 0.2.0 고정
) then
  raise exception using errcode = 'P0001', message = 'PROTOCOL_TOO_OLD';
```

작품이 `LEGACY`가 아니면 문서 쓰기가 0.2.0 계약 배치에서 온 것이어야 한다. **이건 iPad만의 문제가 아니다.** 새 Windows 0.3.0 빌드도 배치 다이제스트가 `abbd234c…`라 똑같이 막힌다. 작품이 `LEGACY/0`인 동안에는 검사 자체를 하지 않으므로 지금은 무해하다.

그리고 인계 문서는 "모든 작품이 LEGACY/0"이라고 했는데 아니다. `일반동기화 검증 20260910`이 이미 `ID_BASED / epoch 1 / contract=416c1b99…(0.2.0)`다.

## 물어볼 것

**추정으로 답하지 말고 저장소를 실제로 읽고 답해 달라.** 모르면 모른다고 해라. 각 항목에 확인용 명령을 같이 적었다. 결과를 그대로 붙여 주면 가장 좋다.

### 1. iPad가 `storage_name_key`를 직접 계산하는가

**이 답 하나로 전환 일정이 갈린다.** 직접 계산한다면 iPad도 v2 구현과 검증이 필요하고, 서버가 채운 값을 받기만 한다면 iPad는 손댈 것이 없다.

```bash
rg -n --type swift 'storage_name|storageName'
rg -n --type swift 'normaliz|casefold|caseFold|NFKC|precomposed|decomposed|folding'
```

- 계산한다면: 어느 알고리즘인가, 어떤 표를 쓰는가, 결과를 서버로 보내는가 아니면 로컬 비교용인가
- 받기만 한다면: 어느 RPC 응답에서 받는가

### 2. iPad가 호출하는 RPC 전체 목록

트리거 수정의 영향 범위를 정확히 잡으려면 필요하다. Windows 쪽 기록에는 "7종"이라고만 적혀 있고 어느 것인지 확정돼 있지 않다.

```bash
rg -n --type swift '\.rpc\(|rpc\(|functions\.invoke|from\("'
```

서버에 있는 public 함수 24종은 이렇다. 이 중 어느 것을 부르는가?

```
acquire_edit_lease            atomic_structure_commit       atomic_structure_commit_legacy
begin_project_sync_migration  cancel_sync_operation         commit_document
commit_folder                 complete_project_sync_migration  document_commit
document_commit_legacy        ensure_project                get_contract_recovery_preflight
get_edit_lease                get_project_status            get_sync_handshake
list_trashed_projects         purge_project                 release_edit_lease
renew_edit_lease              restore_project               trash_project
validate_project_sync_migration
```

특히 이 둘을 구분해서 답해 달라.

- `commit_document` / `commit_folder` (이름 기반 구형)
- `document_commit_legacy` / `atomic_structure_commit_legacy` (legacy 어댑터)

### 3. iPad가 테이블에 직접 쓰는가

문제의 트리거는 `public.document_versions`의 `BEFORE INSERT`에 걸려 있다. RPC를 통하지 않고 PostgREST로 테이블에 직접 insert 하는 경로가 있으면 거기서도 막힌다.

```bash
rg -n --type swift 'document_versions|documents|folders|folder_versions|tree_orders' -g '!*Tests*'
rg -n --type swift '\.insert\(|\.upsert\(|\.update\(|\.delete\('
```

### 4. iPad가 계약·모드 관련 컬럼을 읽는가

```bash
rg -n --type swift 'active_contract_sha256|project_sync_mode|migration_epoch|contract_enforcement_started_at|get_sync_handshake|sync_contract'
```

읽는 곳이 있으면 0.3.0 전환 시 그 경로도 같이 봐야 한다. Windows 쪽 기록으로는 "Swift에 `contract` 언급 0개"인데 확인해 달라.

### 4-b. 이 트리거를 고치는 마이그레이션은 너희 쪽에서 써야 한다

Staging에 적용된 마이그레이션 9개는 전부 `ChocoS-yrup/Writerpad`에 있다. Windows 저장소의 `supabase/migrations/` 7개는 하나도 적용돼 있지 않은 별개 계보다. 그래서 트리거 수정은 Windows 쪽에서 만들어 보낼 수 있는 것이 아니다.

- 마이그레이션 작성·적용 담당이 누구인가
- 0.2.0 리터럴 비교를 allowlist 조회로 바꾸는 방향에 동의하는가, 아니면 두 다이제스트를 모두 허용하는 쪽이 나은가
- 적용 전에 Windows 쪽에서 확인해 줬으면 하는 것이 있는가

### 5. 이미 `ID_BASED`인 작품을 iPad가 지금 동기화하고 있는가

`일반동기화 검증 20260910` (mode=`ID_BASED`, epoch=1). 이 작품은 **이미 비-LEGACY라 트리거 검사를 받고 있다.**

- iPad에서 이 작품이 보이는가
- 이 작품에 iPad로 문서를 쓰면 성공하는가, `PROTOCOL_TOO_OLD`가 나는가
- 난 적이 있다면 앱이 어떻게 처리했는가

**이건 이미 존재하는 실제 시험 사례다.** 답에 따라 트리거 수정의 긴급도가 달라진다.

### 5-b. 오늘 만들어진 시험 작품이 iPad에서 보이는가

2026-09-20에 Windows 3.14 빌드를 시험하면서 새 작품이 하나 만들어졌다.

```
53b96759…   생성 2026-09-19 23:07 UTC   mode=(설정행 없음, LEGACY/0)   epoch=0
            폴더 11   문서 26   storage_name_key 채워진 문서 0
```

실제 원고가 아니라 시험용이므로 왕복 시험 대상으로 그대로 쓸 수 있다.

- iPad에서 이 작품이 보이는가
- 폴더·문서 개수가 맞는가
- iPad에서 여기에 폴더 하나를 만들면 Windows에서 보이는가

### 6. 서버 오류를 iPad가 어떻게 처리하는가

트리거가 막으면 무슨 일이 벌어지는지가 데이터 안전과 직결된다.

```bash
rg -n --type swift 'PROTOCOL_TOO_OLD|CONTRACT_NOT_ALLOWED|MIGRATION_LOCKED|P0001|postgrestError|PostgrestError'
```

- 쓰기가 거부되면 로컬 변경을 **큐에 남기는가, 버리는가**
- 사용자에게 보이는가, 조용히 재시도하는가
- 무한 재시도 루프가 되는가

### 7. iPad가 어느 Supabase 프로젝트에 붙는가

```bash
rg -n 'supabase\.co|SUPABASE_URL|supabaseURL|anonKey' -g '!*.lock'
```

Windows 설치본은 `release_cloud_config.json`을 통해 **Staging `mhpnszcorfzrvhyondxr`**에 붙는다. iPad도 같은 곳인가, 아니면 `lrnbklzvxpwnaschrakf`(prod)나 다른 곳인가?

같은 서버가 아니면 왕복 시험 자체가 성립하지 않으므로 먼저 맞춰야 한다.

### 8. 서버 마이그레이션은 누가 적용하는가

Staging과 prod의 적용 목록이 다르다.

```
Staging(9): 20260811000000 20260811010000 20260811020000 20260813063251
            20260814182850 20260820113209 20260906164945 20260910062649 20260910073750
prod(6):    20260811000000 20260811010000 20260811020000 20260814182850
            20260820113209 20260825000000
```

- prod에만 있는 `20260825000000`은 무엇인가
- Staging에만 있는 4개(특히 `20260813063251` — 0.3.0 allowlist 삽입 시점과 일치한다)는 prod에 갈 예정인가
- 마이그레이션 적용 주체와 절차는 어떻게 되는가

### 9. iPad 앱의 배포 상태

- 실기기에 이미 설치돼 쓰이고 있는가, 아니면 개발 중 빌드만 있는가
- 설치돼 있다면 서버 변경 시 앱을 같이 올려야 하는 제약이 있는가

### 10. iPad 쪽의 Unicode 정규화

v2는 호스트 Unicode 버전에 의존하지 않도록 **동결 표**를 쓴다. Windows에서 Python 3.11(Unicode 14.0.0)과 3.14(16.0.0)가 같은 답을 내는 것을 992건으로 확인했다.

- iPad가 이름을 NFC/NFD 중 무엇으로 들고 있는가 (iOS 파일시스템은 NFD 계열이다)
- 이름 정규화에 `Foundation`/ICU를 쓴다면 iOS 버전에 따라 결과가 달라질 여지가 있는가
- v2가 v1과 갈리는 두 지점을 iPad가 어떻게 다루는지 확인이 필요하다
  - v2는 Unicode 14.0.0 배정 baseline 밖 문자를 `STORAGE_NAME_UNASSIGNED`로 거부한다
  - v2는 구분자(`/`, `\`) 검사를 NFKC **이후에** 한다. v1은 이전에 했다. 그래서 `U+2100`(℀ → `a/c`)처럼 정규화로 `/`가 생기는 문자를 v1은 통과시키고 v2는 거부한다

## 답변 형식

항목 번호를 붙여서, 확인 명령의 **실제 출력**과 함께 답해 달라. 추정한 부분은 추정이라고 표시해 달라. 소스를 못 찾았으면 "해당 없음"이 아니라 "찾지 못함"이라고 적어 달라 — 둘은 다르다.

---
