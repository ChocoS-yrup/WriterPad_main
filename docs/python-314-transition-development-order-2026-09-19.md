# WriterPad_main Python 3.14 전환 개발지시서

2026-09-19

## 판정 요약

**3.14 전환을 추진한다.** 단, 이것은 파이썬 버전 작업이 아니라 sync contract 작업이다. 실질 목표는 storage-name 알고리즘을 파이썬 런타임의 Unicode 버전으로부터 독립시키는 것이다.

### 단일 블로커

`sync_contract.py`의 `_unicode15_module()`가 `unidata_version == "15.0.0"`을 강제한다. 3.14 내장 `unicodedata`는 16.0.0이고, `unicodedata2==15.0.0`은 cp314 휠이 없으며 이 PC에 MSVC 빌드 도구도 없다.

이 블로커는 별도 과제가 아니다. `normalize_storage_name_v2()`는 `unicodedata2`를 쓰지 않으므로 v2 전환에 흡수되어 사라진다.

### 호재 3가지

1. **클라이언트 v2가 이미 계약에 부합한다.** 0.3.0 적합성 벡터 29건을 3.11·3.14 양쪽에서 전량 통과했다. 새로 쓸 알고리즘은 없다.
2. **prod가 비어 있다.** 계정 0 / 작품 0 / 폴더 0. 다만 이번 구현 범위는 Staging이다.
3. **iPad는 작품이 LEGACY/0에 머무는 한 영향을 받지 않는다.** 무조건 무영향이 아니다 — 바로 아래 조건을 반드시 읽을 것.

### 순서 원칙

변수는 한 번에 하나씩 바꾼다. **3.11 삭제는 시작점이 아니라 마지막 청소 작업이다.** 전 단계에서 3.11은 롤백 지점으로 유지한다.

OpenAI SDK를 비롯한 나머지 패키지는 이번 작업에서 버전을 올리지 않는다.

### iPad 공존 조건 — 확정 사실이 아니라 조건부 판단

iPad는 `commit_folder` / `commit_document`를 쓴다. 이 경로는 `writerpad.contract_sha256`를 설정하지 않으므로 항상 `storage_name_v1_legacy`, 즉 **v1 키**를 계산한다.

그런데 legacy RPC에 **0.2.0 다이제스트가 하드코딩**돼 있다 (`20260811020000_sync_contract_0_1_0_rpcs.sql:418`).

```sql
if v_actual_mode in ('MIGRATING', 'ID_BASED') then
  if v_settings.contract_enforcement_started_at is null
     or v_settings.active_contract_sha256 <>
       '416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670' then
    raise exception using errcode = 'P0001', message = 'CONTRACT_NOT_ALLOWED';
  end if;
end if;
```

작품을 `MIGRATING` 또는 `ID_BASED`로 올리면서 `active_contract_sha256`를 0.3.0(`abbd234c…`)으로 두면 **iPad는 `CONTRACT_NOT_ALLOWED`로 하드 차단된다.**

0.3.0 마이그레이션은 이 함수들을 재정의하지 않고, 이후 보정 마이그레이션도 손대지 않는다. 하드코딩된 0.2.0 비교는 실행 경로 4곳(`368`, `418`, `1619`, `1798`)에 남아 있다.

| 작품 상태 | iPad 결과 |
| --- | --- |
| `LEGACY` / epoch 0 (문서 기록상 11개 전부) | 정상 동작, 영향 없음 |
| `MIGRATING` / `ID_BASED` + 계약 0.2.0 | 정상 동작 |
| `MIGRATING` / `ID_BASED` + 계약 **0.3.0** | **CONTRACT_NOT_ALLOWED 로 차단** |

차단되지 않는 경우에도 문제가 남는다. 작품이 v2 키로 재계산된 뒤 iPad가 v1 키를 써넣으면, v1≠v2인 이름에서 형제 충돌 판정이 어긋난다.

**따라서 작품 mode/epoch 전환은 iPad 측 대응 없이 실행할 수 없다.** 서버 legacy RPC의 다이제스트 허용 범위를 넓히는 변경이 선행되어야 한다. 이것은 이번 로컬 구현 범위 밖이다.

## 이번 작업의 범위

**최종 판단: 전환 추진, 로컬 구현 착수 가능, 서버 변경은 조건부.**

### 포함 — 서버 변경 없이 가능한 작업

| 단계 | 내용 |
| --- | --- |
| A | 현재 상태 동결, Git 기준점 |
| B-0 | 라이브 상태 **읽기 전용** 확인 |
| B-1 | v1 golden vectors / manifest 생성 |
| C-① | legacy RPC 다이제스트 제약 **조사와 설계** (적용 아님) |
| D | 계약 0.3.0 산출물 가져오기, 교차 검증 |
| E | Windows v2 전환, `unicodedata2` 제거, 빌드 파일 수정 |
| F | 3.14 venv 검증 |
| G | exe 재빌드와 내부 확인 |

### 제외 — 별도 승인 후 진행

아래는 **이번 구현 작업에 포함하지 않는다.**

- prod 변경 일체
- 기존 데이터 삭제
- allowlist 활성화 (`enabled=true`)
- 작품 mode/epoch 전환
- legacy RPC 다이제스트 제약 실제 변경
- Python 3.11 제거 (단계 H)

### 진행 방식

각 단계를 끝낼 때마다 **검증 결과와 다음 변경 범위를 먼저 제시하고**, 승인을 받은 뒤에 다음으로 넘어간다. 중간에 범위를 넓히지 않는다.

단계 G까지 끝나면 **3.14로 빌드된 exe가 Staging에서 LEGACY/0 작품을 정상 동기화하는 상태**가 된다. 이게 이번 작업의 도착점이다. v2는 코드에 들어가 있지만, 서버가 0.3.0을 허용하지 않는 한 실제 경로는 여전히 v1이다.

## 검증된 사실과 미검증 사실

이 지시서의 모든 판단은 아래 세 등급 중 하나에 근거한다. 등급을 섞어서 읽지 않는다.

### A. 이번 조사에서 직접 실측함

| 항목 | 결과 |
| --- | --- |
| 클라이언트 v2 ↔ 계약 0.3.0 벡터 29건 | 3.11 일치 29/29, 3.14 일치 29/29 |
| v2 결과의 3.11 ↔ 3.14 동일성 | 실데이터 262 + 적대적 20 = 282건, 불일치 0 |
| v1 ↔ v2 키 차이 (실데이터 262건) | 동일 258, 결과 다름 0, 둘 다 거부 4 |
| v2 신규 충돌 | 0건 (v1 충돌 1건 = v2 충돌 1건, NFC/NFD é 쌍) |
| 기존 고정 버전의 3.14 휠 | 14개 중 13개 존재, `unicodedata2==15.0.0`만 없음 |
| 서버 v2 SQL 전문 | 4041줄 읽고 클라이언트와 단계별 대조 완료 |
| iPad 소스 | Swift 파일 중 `contract` 언급 0개, RPC 7종만 호출 |

### B. 저장소 문서 기록으로만 아는 것

아래는 2026-09-06〜09-07 시점 기록이다. **단계 C 시작 전 라이브 DB에서 재확인해야 한다.**

| 항목 | Staging | prod |
| --- | --- | --- |
| ref | `mhpnszcorfzrvhyondxr` | `lrnbklzvxpwnaschrakf` |
| 계정 / 작품 | 1 / 11 | 0 / 0 |
| 폴더 전체 / 활성 | 195 / 166 | 0 / 0 |
| 원고 버전 / 폴더 버전 | 1086 / 272 | 0 / 0 |
| 0.2.0 allowlist | enabled=true | — |
| 0.3.0 allowlist | **enabled=false** | 객체 자체 없음 |
| 작품 mode/epoch | 11개 전부 LEGACY/0 | — |
| `sync_batches` | 0건 | — |

현재 Windows 설치본은 **Staging**에 연결돼 있다. Staging과 prod의 파일은 사용자 확인상 전부 테스트용이다.

### C. 아예 확인하지 못한 것

- **라이브 Supabase 전수 스캔.** 자격증명이 Windows 자격증명 저장소·service_role 키에 있어 직접 조회하지 않았다.
- **서버 v2 함수의 실제 실행 결과.** SQL 정의만 대조했고 DB에서 벡터를 돌리지 않았다.
- **prod의 현재 마이그레이션 적용 상태.** 문서는 prod 6개 / Staging 7개로 기록한다.

## 단계 A — 현재 상태 동결

**이것이 파이썬 버전보다 급한 리스크다.** 미커밋 변경이 284개(수정 13, 추적 안 됨 271) 쌓여 있고, 브랜치는 `feat/contract-handshake-closed-gate`다. 이 상태에서 마이그레이션을 시작하면 문제 발생 시 돌아갈 지점이 없다.

### 순서

1. **보존이 먼저다.** 전체 폴더를 그대로 복사해 둔다. 이 시점 이후 어느 단계에서든 돌아올 수 있어야 한다.
2. **분류한다.** 271개 untracked를 네 가지로 나눈다 — 소스 / 빌드 산출물 / 캐시 / 임시 파일.
3. **기준점을 만든다.** 현재 정상 작동 상태를 WIP 커밋 또는 별도 브랜치로 묶는다.
4. **`.gitignore`를 정리한다.** 분류 결과 빌드·캐시로 판명된 것만 반영한다.

### 금지 사항

- **자동 정리·일괄 삭제를 하지 않는다.** `git clean` 계열 명령을 쓰지 않는다.
- `_evidence` 아래 진단 스냅샷은 과거 검증 근거다. 임의로 지우지 않는다.
- Unicode 마이그레이션과 기존 284개 변경을 **같은 커밋에 섞지 않는다.**

### 완료 기준

`git status --short`가 비어 있거나, 남은 항목이 의도적으로 무시되는 것임을 설명할 수 있는 상태.

## 단계 B — v1 golden vectors 생성

**3.11 + `unicodedata2 15.0.0` 환경이 살아 있을 때 수행한다.** 이 환경은 v1의 마지막 정상 기준 구현이다.

### v1은 어디에 남는가

| v1 기준 구현 | 3.11 제거 후 |
| --- | --- |
| Windows Python + `unicodedata2 15.0.0` | 사라짐 |
| 서버 `private.storage_name_v1_legacy` | 남음 |
| 계약 0.2.0 적합성 벡터 15건 | 남음 |

완전 소실은 아니다. 다만 **"우리 실제 데이터에 대한 v1의 답"**은 지금만 만들 수 있다.

### 대상

**B-0. 라이브 상태 확인이 먼저다.** manifest는 그 다음이다. 문서 기록(2026-09-07)으로 manifest를 만들면 대상 집합이 틀릴 수 있다. 읽기 전용으로 확인할 것:

- 작품·폴더·문서의 현재 행 수
- 각 작품의 `project_sync_mode` / `migration_epoch` / `active_contract_sha256`
- `sync_contract_allowlist` 의 행별 `enabled` / `revoked_at`
- 적용된 마이그레이션 목록
- **현재 저장된 `storage_name_key` 값 자체**

**B-1. manifest 대상**

- Staging의 폴더·문서 이름 전체 (B-0에서 확인한 실제 집합)
- Windows 로컬 SQLite 작품의 폴더·문서 이름
- `집필프로그램\작품목록` 파일시스템 이름
- 계약 0.2.0 벡터 15건 + 0.3.0 벡터 29건 (별도 집계)

### 필드

```
원본 이름
| 현재 저장된 storage_name_key (hex)   ← 서버에서 읽은 값
| v1 normalized | v1 key(hex) | v1 거부코드
| v2 normalized | v2 key(hex) | v2 거부코드
| 저장값 == v1 재계산값 여부          ← 기존 키의 무결성
| 저장값 == v2 재계산값 여부          ← 전환 시 변경 필요 여부
| 부모 폴더 id                        ← 형제 범위 판정용
| 출처 / 작품 id
```

기존 저장 키를 넣는 이유는 두 가지다. 첫째, 서버가 지금 들고 있는 값이 v1 재계산값과 같은지 확인해야 과거 기록의 무결성을 말할 수 있다. 둘째, 전환 시 실제로 바뀔 행이 몇 개인지는 저장값 기준으로만 알 수 있다.

### 충돌 검사 범위

충돌은 **같은 부모 폴더 안 형제끼리**만 의미가 있다. 전체 이름 집합에서 동일 키를 세는 것은 의미 없는 수치다.

검사 단위는 `(parent_folder_id, storage_name_key)` 쌍이며, 폴더와 문서가 **합쳐 하나의 형제 네임스페이스**를 이룬다. 서버의 `validate_project_sync_migration`이 같은 방식으로 센다.

보고할 수치는 세 가지다.

- v1 기준 형제 충돌 건수
- v2 기준 형제 충돌 건수
- **v2에서 새로 생긴 충돌** = 둘의 차집합

### 봉인

iPad 스캔 문서(`storage-name-v2-ipad-device-scan-2026-08-13.md`)의 선례를 그대로 따른다.

```yaml
record_count: <n>
v1_v2_identical: <n>
v2_newly_rejected: <n>
new_collisions: <n>
manifest_sha256: <파일 해시>
sorted_name_digest_algorithm: >-
  이름 UTF-8 bytes 를 bytewise 오름차순 정렬, 각 항목 앞 4-byte big-endian 길이,
  이어 쓴 뒤 SHA-256
sorted_name_sha256: <해시>
generator_python: 3.11.9
generator_unicodedata2: 15.0.0
```

### 민감도 처리

원고 제목은 사용자 데이터다. iPad 스캔 선례대로 **원본은 `_evidence` 비공개 보관, 공개 증거에는 집계와 해시만** 남긴다. 저장소에 커밋하지 않는다.

### 추가 보존 2가지

1. **휠 파일을 보관한다.** `pip download unicodedata2==15.0.0`로 cp311 휠을 받아 `_evidence`에 둔다. cp314 휠은 없고 소스 컴파일도 불가하므로 사실상 유일본이 된다.
2. **계약 벡터 형식으로도 남긴다.** 29개 v2 벡터와 같은 JSON 스키마로 v1 결과를 써두면, 파이썬 환경이 사라져도 순수 데이터로 v1 동작이 영구 보존된다. 가장 견고한 형태다.

### 완료 기준

성공 기준을 **두 가지로 분리한다.** 섞어서 보면 의도된 거부를 실패로 오독하게 된다.

**① 실데이터 검사 — 거부가 0이어야 한다**

서버·로컬에 실제로 존재하는 이름들이다.

| 지표 | 통과 기준 |
| --- | --- |
| `v2_newly_rejected` | 0 |
| `new_collisions` (v2 신규 형제 충돌) | 0 |
| `stored_key_mismatch_v1` (저장값 ≠ v1 재계산) | 0 |
| `stored_key_mismatch_v2` (저장값 ≠ v2 재계산) | 값과 무관하게 기록 — 전환 시 갱신할 행 수 |

앞 세 지표 중 하나라도 0이 아니면 멈춘다. 네 번째는 합격/불합격 기준이 아니라 **전환 규모 실측치**다.

**② 적합성 벡터 — 거부가 나와야 정상이다**

계약 0.2.0 15건 + 0.3.0 29건은 **거부 케이스를 일부러 포함한다.** 예를 들어 `CON.txt`, `folder/name`, PUA, 상위면+결합부호는 거부되는 것이 정답이다.

| 지표 | 통과 기준 |
| --- | --- |
| 0.3.0 벡터 29건 `valid` / `normalized` / `utf8_hex` 일치 | 29/29 |
| 0.2.0 벡터 15건 v1 결과 일치 | 15/15 |

이 두 집합을 ①의 `v2_newly_rejected` 집계에 **섞지 않는다.**

**③ 봉인**

manifest 파일의 SHA-256과 정렬 규칙이 기록된 상태. B-0에서 읽은 라이브 시각도 함께 남긴다.

## 단계 C — 서버 0.3.0 배포와 allowlist 활성화

**이 단계의 성격을 오해하면 안 된다.** 0.3.0은 저장소에만 있고 서버에 반영되지 않았다. "이미 배포된 것을 검증"이 아니라 **배포하고 allowlist를 켜는 작업**이 포함된다.

서버 문서(`storage-name-v2-server-implementation-2026-08-13.md`)가 명시한다 — *저장소 마이그레이션이 배포된 Supabase 프로젝트에 적용됐음을 증명하지 않는다. 일회용 CI 데이터베이스 밖에서는 어떤 allowlist 행도 활성화돼 있지 않다.*

### 순서

1. **라이브 상태를 먼저 읽는다.** 단계 B 완료 직후, 읽기 전용으로 prod와 Staging의 실제 행 수·마이그레이션 목록·allowlist 상태를 확인한다. 문서 숫자를 그대로 쓰지 않는다.
2. **어느 서버로 갈지 정한다.** Staging에서 진행한다. 프로그램 개발과 검증이 아직 Staging에서 진행 중이므로 0.3.0 배포도 Staging에 먼저 한다. prod 전환은 프로그램이 완성된 뒤로 미룬다.
3. **마이그레이션을 적용한다.** Staging에는 0.3.0 관련 객체가 이미 있을 가능성이 크다. 먼저 적용 상태를 읽고, 누락분만 채운다. prod는 참조 테이블 4개와 함수 8개가 통째로 없으므로, 나중 전환 시점에 따로 다룬다.
4. **allowlist를 켜기 전에 검증한다.** 서버에서 직접 29개 벡터(SN-001〜SN-029)를 돌려 클라이언트 결과와 대조한다.
5. **allowlist 행을 활성화한다.** 0.3.0 digest `abbd234c…`의 `enabled=true`.

### 서버 ↔ 클라이언트 대조 방법

같은 29건을 양쪽에 넣고 `valid` / `normalized` / `utf8_hex` 세 필드를 비교한다. 클라이언트는 이미 29/29로 통과했으므로, 서버 쪽만 돌리면 된다.

서버 함수는 `private.storage_name_v2_result(p_name text)`가 jsonb로 결과를 돌려준다.

### 알고리즘 대조 결과 (이번 조사에서 수행함)

| 단계 | 서버 | 클라이언트 | 일치 |
| --- | --- | --- | --- |
| 배정 검사 | 전체 1회 순회 | 문자별 | 결과 같음 |
| 제외 검사 | 별도 순회 | 같은 순회 안 | 결과 같음, 오류코드 다를 수 있음 |
| 인접 가드 | 동결 `nonzero_ccc` 테이블 | 내장 `unicodedata.combining()` | 결과 같음, 구현 다름 |
| NFKC → casefold → NFKC | 동일 | 동일 | 같음 |
| 사후 baseline 재검사 | 별도 순회 | 구분자 검사와 합침 | 결과 같음 |
| rstrip / 예약어 / UTF-8 | 동일 | 동일 | 같음 |

오류 코드가 갈리는 경우가 있지만 충돌 키는 영향받지 않고, 계약 벡터가 오류 코드를 고정하지 않으므로 위반도 아니다. 자세한 내용은 마지막 절에 적었다.

### 서버 활성화 전 필수 확인 항목

아래 네 가지가 모두 해결되기 전에는 `enabled=true`를 하지 않는다. 이것들은 이번 로컬 구현 범위 밖이며, **각각 별도 승인을 받고 진행한다.**

**C-① legacy RPC 다이제스트 허용 범위**

`20260811020000_sync_contract_0_1_0_rpcs.sql`의 실행 경로 4곳(`368`, `418`, `1619`, `1798`)에 0.2.0 다이제스트가 하드코딩돼 있다. 0.3.0을 허용하려면 이 비교를 allowlist 조회로 바꾸거나 두 다이제스트를 모두 허용해야 한다.

**이 변경 없이 작품을 0.3.0으로 올리면 iPad가 즉시 차단된다.**

**C-② 작품 mode/epoch 전환 설계**

`LEGACY/0` → `MIGRATING` → `ID_BASED` 전환을 언제, 어느 작품부터, 누가 시작하는지 정한다. `MIGRATING` 중에는 `started_by_device_id`가 아닌 기기가 `MIGRATION_LOCKED`로 막히므로, 전환 도중 다른 기기의 작업이 멈춘다.

**C-③ 새 데이터 기록 이후의 복원 절차**

아래 「중단 조건과 롤백」절을 따른다. 전환 이후에는 Git과 3.11로 돌아갈 수 없다.

**C-④ 동일 작품 Windows↔iPad 왕복 시험**

같은 작품 하나를 양쪽에서 번갈아 다룬다. 이것이 단계 C의 실질 완료 기준이다.

1. Windows에서 폴더·문서 생성 → iPad에서 보임 확인
2. iPad에서 이름변경 → Windows에서 반영 확인
3. 양쪽에서 한글·일본어·이모지 이름으로 각각 생성
4. 형제 충돌을 의도적으로 유발해 양쪽이 **같은 판정**을 내는지
5. 전환 전 만든 이름이 전환 후에도 양쪽에서 동일하게 보이는지

### 완료 기준

- 서버 29/29 일치
- C-①〜C-④ 전부 해결
- 동일 작품 왕복 시험 통과
- 배포된 서버에서 직접 읽은 증거 보존

이번 로컬 구현 작업에서는 **C-① 확인과 설계까지만** 하고, 서버 변경·allowlist 활성화·작품 전환은 하지 않는다.

## 단계 D — 계약 0.3.0 등재와 클라이언트 핀

Windows 저장소의 `sync-contract/`는 0.2.0에 멈춰 있고, `Writerpad` 저장소에는 0.3.0이 released로 들어 있다. **새로 쓰는 게 아니라 가져오는 작업이다.**

### 두 저장소의 현재 계약

|  | WriterPad_main (Windows) | Writerpad (iPad·서버) |
| --- | --- | --- |
| `contract_version` | 0.2.0 | 0.3.0 |
| digest | `416c1b99…` | `abbd234c…` |
| canonical bytes | 23256 | 24777 |
| storage-name 벡터 | 15건 (v1) | 29건 (v2) |
| `unicode_assets` | 없음 | 4개, SHA256 봉인 |

### 이미 일치하는 것

Windows의 동결 테이블은 **이미 0.3.0 계약 자산에서 생성된 것**이다. 해시가 정확히 맞는다.

```
storage_name_tables.py  BASELINE_CANONICAL_SHA256
  = aed4e530cc5e0de638310db961f17f174d605282764a6657afd0f70e5515c85c
contract-lock.json (0.3.0)  unicode/assigned-baseline-14.0.0.json
  = aed4e530cc5e0de638310db961f17f174d605282764a6657afd0f70e5515c85c   ✓

storage_name_tables.py  EXCLUDED_CANONICAL_SHA256
  = f56ee73bca1690e842a189336ffdfabe7625128f7f314a7ac567755952571e86
contract-lock.json (0.3.0)  unicode/excluded-scalars.json
  = f56ee73bca1690e842a189336ffdfabe7625128f7f314a7ac567755952571e86   ✓
```

### 작업

1. `Writerpad` 저장소의 `sync-contract/` 0.3.0 산출물을 `WriterPad_main`으로 가져온다 — `protocol.json`, `contract-lock.json`, `conformance_vectors/storage-name-v2.json`, `unicode/` 자산 4개.
2. `sync-contract/scripts/verify_contract.py`로 교차 검증을 통과시킨다.
3. 두 저장소의 `canonical_contract_sha256`가 `abbd234c…`로 같아진 것을 확인한다.

### 주의

계약 산출물은 RFC 8785 정규화 바이트에 SHA-256이 봉인돼 있다. **손으로 편집하면 다이제스트가 깨진다.** 복사해 오거나 생성 스크립트를 쓴다.

### 완료 기준

두 저장소의 계약 digest가 동일하고 `verify_contract.py`가 통과한 상태.

## 단계 E — Windows v2 전환과 3.14 빌드 환경

이 단계에서만 코드를 고친다. 앞 단계가 끝나기 전에 시작하지 않는다.

### E-1. v1 → v2 전환

`normalize_storage_name` 호출 16곳을 `normalize_storage_name_v2`로 바꾼다. 또는 v1이 v2를 위임하게 한다 — 호출부 변경을 줄이려면 후자가 낫다.

함께 바꾸는 상수:

```
CONTRACT_VERSION         "0.2.0" → "0.3.0"
STORAGE_NAME_ALGORITHM   "storage-name-v1" → "storage-name-v2"
CLIENT_CAPABILITIES      "storage_name_v1" → "storage_name_v2"  (2곳)
```

제거 대상:

```
STORAGE_NAME_UNICODE_VERSION
_unicode15_module()
import unicodedata2 경로 전체
```

`handshake_lifecycle.py`의 capability 집합 일치 검사가 자동으로 따라간다.

### E-2. `unicodedata2` 의존 제거

**이 시점에 3.14 블로커가 소멸한다.** v2는 내장 `unicodedata`의 NFKC와 동결 테이블만 쓴다.

```
requirements-stage8.txt       unicodedata2==15.0.0 제거
작가님힘내세요.spec:47        hiddenimports 에서 제거
Antigravity_AI_Writer.spec:47 동일
```

### E-3. 3.14 빌드 환경

```
.github/workflows/windows-contract.yml:70   "3.11.9" → "3.14"
writerpad-recovery.cmd:3-4                  하드코딩 경로 → py -3.14 기반
```

복구킷은 `집필프로그램\독립복구-20260910\`에도 사본이 있다. 둘 다 고친다.

### E-4. 패키지 버전 — 올리지 않는다

기존 고정 버전이 전부 3.14 휠을 갖는다. 확인 완료:

| 패키지 | 버전 | 3.14 휠 |
| --- | --- | --- |
| PyQt6 | 6.11.0 | 있음 |
| supabase / postgrest | 2.31.0 | 있음 |
| pyinstaller | 6.21.0 | 있음 |
| keyring | 25.7.0 | 있음 |
| Markdown | 3.10.2 | 있음 |
| python-dotenv | 1.2.2 | 있음 |
| anthropic | 0.120.0 | 있음 |
| **openai** | **2.44.0** | **있음 — 올리지 않는다** |
| google-genai | 2.10.0 | 있음 |
| httpx | 0.28.1 | 있음 |
| numpy | 2.4.4 | 있음 |
| pillow | 12.2.0 | 있음 |
| opencv-python-headless | 4.13.0.92 | 있음 |

OpenAI SDK 3.x 업그레이드는 **이번 작업에서 제외한다.** 파이썬·Unicode 계약·SDK를 동시에 바꾸면 원인 구분이 안 된다.

### 완료 기준

코드에 `unicodedata2` 참조가 0개이고, 3.11에서 기존 시험이 여전히 통과하는 상태. **이 시점까지는 아직 3.11에서 돌린다.**

## 단계 F — 3.14 별도 환경 검증

**휠이 있다는 것은 설치 가능성일 뿐 작동 검증이 아니다.** 이번 조사에서 확인한 것은 휠의 존재와 순수 계산 결과뿐이다.

### 환경 구성

3.11은 그대로 두고 별도 venv를 만든다. 이 단계 내내 3.11이 롤백 지점으로 살아 있어야 한다.

```
py -3.14 -m venv .venv314
.venv314/Scripts/python.exe -m pip install -r requirements-stage8.txt
```

**`py -3.14`는 venv 생성에만 쓴다.** 그 뒤로는 `py -3.14`나 `python`이 .venv314를 가리킨다고 가정하지 않는다. `py` 런처는 venv가 아닌 **시스템 3.14**를 실행한다.

실행 주체를 항상 명시한다.

| 용도 | 명시적 호출 |
| --- | --- |
| 시험·실행 | `.venv314\Scripts\python.exe` |
| 패키지 설치 | `.venv314\Scripts\python.exe -m pip` |
| 빌드 | `.venv314\Scripts\python.exe -m PyInstaller` |
| 복구킷 | `py -3.14` (의도적으로 시스템 파이썬) |

복구킷만은 venv 없는 환경에서도 돌아야 하므로 시스템 파이썬을 쓴다. 이 차이를 문서에 남긴다.

`build_exe.ps1`은 `Get-Command python`으로 파이썬을 찾는다. venv 활성화 여부에 따라 결과가 바뀌므로, **스크립트가 어느 파이썬을 잡았는지 로그로 남기도록 고친다.**

### 시험 목록

기존 1198개 시험 외에 아래를 실제로 돌린다.

- [ ] 프로그램 시작 / 종료
- [ ] 자동 저장
- [ ] crash recovery
- [ ] 한글 / 일본어 / 이모지 파일명
- [ ] storage-name v2 계산
- [ ] Supabase 업로드 / 다운로드 왕복
- [ ] handshake
- [ ] 동시편집 lock / heartbeat
- [ ] keyring 저장 / 복원
- [ ] OpenAI 호출 (2.44.0 그대로)
- [ ] 새 문서 생성 / 삭제 / 이름변경
- [ ] PyInstaller 빌드
- [ ] exe 재실행
- [ ] Windows 재부팅 후 실행

### 특히 볼 것

`main.py`의 `--qt-import-smoke-test`가 3.14 + PyQt6 6.11 조합에서 Qt 플랫폼 플러그인과 MSVC 런타임까지 통과하는지 확인한다. 릴리스 빌드의 관문이다.

그리고 **29개 적합성 벡터를 3.14 venv에서 다시 돌린다.** 이번에 29/29가 나왔지만, 전환 후 코드가 바뀐 상태에서 재확인한다.

### 완료 기준

시험 전항 통과 + 적합성 벡터 29/29 + 아래 네 가지 확인.

**F-① 실제 실행 버전**

시험이 정말 3.14에서 돌았는지 로그로 남긴다. 테스트 시작 시 `sys.version`, `sys.executable`, `sys.prefix`를 출력한다.

`sys.prefix`가 `.venv314`를 가리키지 않으면 시스템 파이썬으로 돌아간 것이다.

**F-② 간접 의존성**

직접 명시한 14개 외에, 그것들이 끌고 오는 패키지도 3.14 휠이 있어야 한다.

```
.venv314\Scripts\python.exe -m pip check
.venv314\Scripts\python.exe -m pip list --format=freeze
```

설치 결과 전체를 3.11 환경과 비교해 **예기치 않게 버전이 올라간 간접 의존성이 있는지** 확인한다. `pip check`가 통과해도 버전이 달라졌을 수 있다.

**F-③ 서버 호출 권한**

3.14에서도 인증과 권한이 똑같이 동작하는지 확인한다. keyring 백엔드(`WinVaultKeyring`)가 3.14에서 같은 자격증명을 읽고, 그걸로 Supabase 인증이 통과하고, RPC 호출이 `AUTH_REQUIRED` / `FORBIDDEN` 없이 동작하는지까지 확인한다.

**F-④ 작품 상태 불변**

이 단계의 시험이 작품의 `project_sync_mode` / `migration_epoch`를 바꾸지 않았는지 시험 전후로 대조한다. 이번 범위에서는 전부 `LEGACY/0`에 머물러 있어야 한다.

## 단계 G — exe 재빌드와 내부 확인

현재 exe(79MB, 2026-09-13)에는 `python311.dll`이 내장돼 있다. **보안 관점의 실제 목표는 이걸 `python314.dll`로 바꾸는 것이다.** 시스템 3.11 삭제는 이 목표와 무관하다.

### 빌드

3.14 venv에서 PyInstaller 6.21.0으로 spec을 돌린다. `build_exe.ps1`은 `Get-Command python`으로 파이썬을 찾으므로 venv가 활성화된 상태에서 실행해야 한다.

Tcl/Tk 경로도 `sys.base_prefix` 기준이라 3.14 쪽을 보게 된다. 3.14에도 `tcl` 폴더가 있고 tkinter 8.6이 동작함을 확인했다.

### 빌드 후 필수 확인

| 항목 | 기대 | 확인 방법 |
| --- | --- | --- |
| `python314.dll` | 있음 | 바이너리 문자열 |
| `python311.dll` | 없음 | 바이너리 문자열 |
| `unicodedata2` 확장모듈 | 없음 | 번들 목록 |
| 번들된 패키지 버전 | .venv314와 일치 | 번들 목록 ↔ `pip list` |
| `--qt-import-smoke-test` | 종료코드 0 | 실행 |
| 실행 중 `sys.version` | 3.14.x | 진단 출력 |

**문자열 검사만으로는 부족하다.** onefile 빌드는 압축돼 있어 문자열이 안 보일 수 있고, 반대로 주석이나 경로 잔향으로 오탐도 있다. 세 가지를 같이 본다.

1. **번들 목록 직접 열람** — 풀어낸 임시 디렉토리에서 `.pyd` / `.dll` 목록과 패키지 메타데이터를 확인한다.
2. **실행 중 자기보고** — exe가 실제로 실행된 상태에서 `sys.version`과 주요 패키지 `__version__`을 진단 출력으로 남긴다. 이게 가장 확실하다.
3. **이전 빌드와 비교** — 번들 목록을 구버전 exe와 대조해 사라져야 할 것과 새로 들어온 것을 확인한다.

### 사용 시나리오 시험

빌드된 exe로 실제 원고 작업을 한다. 새 작품 생성, 화 작성, 저장, 동기화, 재시작 후 복원까지 한 바퀴 돌린다.

기존 exe는 백업해 둔다. 이전 성공 빌드가 `_evidence\windows-release-install-20260907\previous-installed.exe`에 보존돼 있는 선례를 따른다.

### 완료 기준

위 4항목 전부 기대값과 일치하고, 실사용 시나리오가 통과한 상태.

## 단계 H — Python 3.11 제거

**이 지점에 오기 전에는 지우지 않는다.** 전제 조건이 모두 충족된 상태여야 한다.

```
소스              3.14 OK
시험              전항 통과
적합성 벡터       29/29
Supabase 동기화   OK
PyInstaller exe   OK
python314.dll     확인됨
Unicode v2        OK
기존 데이터        무결성 확인됨
v1 golden vectors 보존됨
```

### 제거 전 마지막 확인

`unicodedata2 15.0.0` cp311 휠 파일이 `_evidence`에 보관돼 있는지 다시 확인한다. 이걸 놓치면 v1을 재현할 수단이 없어진다.

### 제거 후 재확인

3.11을 지운 다음 **프로그램과 빌드를 한 번씩 더 돌린다.** 실수로 시스템 3.11에 숨어서 의존하던 부분을 찾기 위해서다.

특히 볼 곳:

- `writerpad-recovery.cmd` 실행 (저장소 본체 + 독립복구 사본)
- `build_exe.ps1` / `build_portable.ps1` 재실행
- `강좌신청.bat` 같은 `pythonw` 의존 스크립트 (타 프로젝트)

### PATH 정리

3.11을 지우면 PATH에 잔여 항목이 남을 수 있다. 현재 PATH는 `Python311\Scripts`와 `Python311`이 앞에 있고 3.14 경로가 뒤에 있다.

또한 `py` 런처 기본값이 3.14인지 확인한다. 3.11 제거 전에는 `PY_PYTHON` 설정으로 명시하는 게 안전하다.

### 주의

3.11을 지워도 기존 배포 exe 안의 `python311.dll`은 그대로 남는다. 구버전 exe 백업본을 계속 보관할 것인지 판단한다. 롤백용으로는 필요하지만, 보안 관점에서는 정리 대상이다.

## 중단 조건과 롤백

아래 신호가 나오면 **다음 단계로 넘어가지 않고 멈춘다.** 무리해서 진행하면 원인 구분이 불가능해진다.

| 단계 | 중단 신호 | 롤백 지점 |
| --- | --- | --- |
| B | `v2_newly_rejected > 0` 또는 `new_collisions > 0` | 작업 시작 전. 해당 이름들을 먼저 처리 |
| B | 라이브 수치가 문서 기록과 크게 다름 | 다시 조사. 문서 숫자로 진행하지 않음 |
| C | 서버 29개 벡터 중 1건이라도 불일치 | allowlist 켜지 않음. 서버는 0.2.0 유지 |
| C | 마이그레이션 적용 중 오류 | 단계 A 기준점 |
| D | 두 저장소 digest 불일치 | 계약 파일 재복사 |
| E | 3.11에서 기존 시험이 깨짐 | 단계 A 기준점 |
| F | 시험 목록 중 하나라도 실패 | 3.11 환경으로 즉시 복귀 |
| F | 적합성 벡터가 29/29가 아님 | 단계 E 재검토 |
| G | exe에 `python311.dll` 잔존 | 빌드 환경 재점검 |
| G | 실사용 시나리오 실패 | 구버전 exe 복원 |

### 가장 위험한 조합

**단계 A를 건너뛰고 단계 E를 시작하는 것.** 미커밋 284개와 Unicode 마이그레이션이 섞이면 무엇이 깨뜨렸는지 구분할 수 없다.

### 롤백은 두 종류다 — 섞으면 위험하다

Git 기준점과 Python 3.11 보존은 **로컬 환경만** 되돌린다. 서버가 바뀌거나 새 데이터가 기록된 뒤에는 이것만으로 복구되지 않는다.

**① 로컬 롤백 — 단계 A〜B, E〜G**

| 대상 | 복구 수단 |
| --- | --- |
| 소스 변경 | Git 기준점 |
| 파이썬 환경 | 3.11 유지 + `.venv314` 삭제 |
| 빌드 산출물 | 구버전 exe 백업본 |
| 로컬 SQLite | 단계 A의 전체 폴더 복사본 |

이 범위는 되돌리기 쉽고 외부 영향이 없다. **이번 구현 작업은 여기에만 머무른다.**

**② 서버·데이터 롤백 — 단계 C 이후**

아래는 Git으로 되돌릴 수 없다. 각각 별도 절차가 필요하다.

| 변경 | 되돌리는 방법 | 주의 |
| --- | --- | --- |
| 마이그레이션 적용 | 함수·테이블 삭제 스크립트 사전 작성 | 동결 참조 테이블은 immutable 트리거가 걸려 있다 |
| allowlist 활성화 | `enabled=false` 로 되돌림 | 즉시 반영됨 |
| 작품 mode/epoch 전환 | **되돌리는 공식 경로가 없다** | 전환 전 백업이 유일한 수단 |
| 저장 키 재계산 | 전환 전 키 값 복원 | manifest의 「현재 저장 키」 컬럼이 원본이다 |
| 새로 기록된 원고·배치 | **되돌리면 사용자 작업이 사라진다** | 아래 참조 |

**③ 새 데이터가 쌓인 뒤의 복원**

단계 C 이후에는 사용자가 실제 원고를 쓰기 시작한다. 이 시점부터 "서버를 이전 상태로 되돌린다"는 **그 사이의 집필을 버린다**는 뜻이 된다.

따라서 서버를 바꾸는 작업은 이 순서를 따른다.

1. 작업 전 전체 백업 — 폴더·문서·버전·저장 키·작품 설정을 포함
2. 작업 창 동안 양쪽 기기에서 쓰기 중단
3. 변경 적용
4. 검증 — 왕복 시험 포함
5. 검증 실패 시 즉시 백업 복원, 그 창 안에서 종결

**백업 이후에 새 원고가 들어오면 단순 복원이 불가능해진다.** 쓰기 중단 구간을 짧게 잡고, 검증까지 한 창 안에서 끝낸다.

### 항상 유지해야 할 것

단계 H 전까지 **파이썬 3.11과 `unicodedata2 15.0.0`은 지우지 않는다.** 단, 이것은 ① 로컬 롤백의 수단일 뿐 서버 변경을 되돌리지는 못한다.

## 변경 대상 파일 전체 목록

이번 조사로 위치가 확정된 것만 적었다. 행번호는 조사 시점 기준이다.

### 계약 핵심

| 파일 | 위치 | 변경 |
| --- | --- | --- |
| `sync_contract.py` | 22 | `CONTRACT_VERSION` 0.2.0 → 0.3.0 |
| `sync_contract.py` | 30 | `STORAGE_NAME_ALGORITHM` → storage-name-v2 |
| `sync_contract.py` | 31 | `STORAGE_NAME_UNICODE_VERSION` 제거 |
| `sync_contract.py` | 36〜53 | `CLIENT_CAPABILITIES` 의 v1 → v2 (2곳) |
| `sync_contract.py` | 104〜114 | `_unicode15_module()` 제거 |
| `handshake_lifecycle.py` | 299 | capability 집합 일치 검사 확인 |

### v1 호출부 16곳

```
sync_v2_store.py            1871, 1930, 2057, 2313, 2547
integrated_editor_store.py  70
isolated_read_collector.py  256, 257, 323
(그 외 7곳)
```

### 계약 산출물 — Writerpad 저장소에서 가져옴

```
sync-contract/protocol.json
sync-contract/contract-lock.json
sync-contract/conformance_vectors/storage-name-v2.json
sync-contract/unicode/  (자산 4개)
```

### 빌드·환경

| 파일 | 위치 | 변경 |
| --- | --- | --- |
| `requirements-stage8.txt` | — | `unicodedata2==15.0.0` 제거 |
| `작가님힘내세요.spec` | 47 | hiddenimports 에서 `unicodedata2` 제거 |
| `Antigravity_AI_Writer.spec` | 47 | 동일 |
| `.github/workflows/windows-contract.yml` | 70 | `"3.11.9"` → `"3.14"` |
| `writerpad-recovery.cmd` | 3〜4 | 하드코딩 경로 → `py -3.14` |

복구킷 사본도 같이 고친다.

```
집필프로그램/독립복구-20260910/writerpad-recovery.cmd
```

### 손대지 않는 것

- `unicode15_casefold.py` — 동결 데이터. v2가 `frozen_casefold()`를 그대로 쓴다
- `storage_name_tables.py` — 동결 테이블. 이미 0.3.0 자산과 해시 일치
- `normalize_storage_name_v2()` 본문 — 적합성 벡터 29/29 통과
- OpenAI SDK 및 나머지 패키지 버전

## 남은 경화 과제와 미해결 항목

### 경화 과제 — 클라이언트의 마지막 플랫폼 의존

`sync_contract.py`의 `_reject_supplementary_adjacency()`가 내장 `unicodedata.combining()`을 쓴다. 서버는 같은 일을 동결 테이블 `private.storage_name_v2_nonzero_ccc`로 한다.

서버·계약은 동결했는데 **클라이언트만 런타임에 의존하는 유일한 지점**이다.

실무 위험은 낮다. 배정된 문자의 결합 클래스는 유니코드 안정성 정책상 불변이고, 282건 시험에서 3.11/3.14 불일치가 0이었으며, iPad 스캔도 상위면 문자 0건이라 이 경로에 닿지 않는다.

이번 작업의 필수는 아니다. 다만 동결 테이블로 옮기면 **클라이언트가 완전히 런타임 독립**이 된다.

### 오류 코드 순서 차이

서버는 배정·제외·인접을 각각 전체 순회하고, 클라이언트는 문자별로 묶어서 돌린다. 두 결함이 한 이름에 섞여 있을 때 오류 코드가 갈릴 수 있다.

충돌 키는 영향받지 않고, 계약 벡터가 `valid` / `normalized` / `utf8_hex`만 검증하므로 위반도 아니다. 진단 메시지가 달라질 뿐이다.

맞추고 싶다면 클라이언트를 서버와 같은 3패스 구조로 바꾸면 된다.

### 재확인 필요 항목

- [ ] prod / Staging의 **현재** 행 수와 allowlist 상태 (문서는 2026-09-07 기록)
- [ ] prod에 마이그레이션이 몇 개 적용돼 있는지
- [ ] Windows 설치본이 Staging에 붙어 있는 것이 맞는지
- [ ] 서버 `private.storage_name_v2_result()` 실제 실행 결과 29건

### 조사하지 않은 영역

- **라이브 Supabase 전수 스캔.** 자격증명이 필요해 수행하지 않았다
- **iPad 앱의 내부 동작.** 소스에서 RPC 호출 목록만 확인했다
- **`begin/validate/complete_project_sync_migration` 상세.** 작품 단위 전환이 필요해지면 그때 조사한다

### 참고 — 이 지시서의 근거

모든 수치는 2026-09-19 읽기 전용 조사에서 나왔다. 파일·DB·서버를 수정하지 않았고, 라이브 DB에는 접속하지 않았다.
