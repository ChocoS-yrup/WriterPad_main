# 단계 B 완료 증거 — v1 golden manifest

> 개발지시서: [python-314-transition-development-order-2026-09-19.md](python-314-transition-development-order-2026-09-19.md)
> 인계 문서: [next-task-python-314-local-implementation-handoff-2026-09-19.md](next-task-python-314-local-implementation-handoff-2026-09-19.md)

3.11 + `unicodedata2 15.0.0`이 살아 있는 동안 **우리 실제 데이터에 대한 v1의 답**을 고정했다. 서버·로컬·파일시스템 어느 쪽도 쓰지 않았다.

원고 제목은 사용자 데이터다. 지시서의 민감도 규칙대로 **원본 이름은 `_evidence`에만 두고, 이 문서에는 집계와 해시만 남긴다.**

## 생성물

전부 `_evidence/python-314-v1-golden-20260920/` 아래에 있다. `.gitignore` 대상이라 저장소에 올라가지 않는다.

| 파일 | 내용 | bytes |
| --- | --- | --- |
| `manifest.jsonl` | 992행 전체 레코드 (원본 이름 포함) | 548,212 |
| `summary.json` | 봉인 집계 | 1,619 |
| `v1-golden-vectors.json` | 계약 벡터 스키마 형식의 v1 결과 374건 | 94,378 |
| `vectors-0.2.0.json` | 0.2.0 적합성 벡터 15건 재현 결과 | 2,129 |
| `unicodedata2-preservation/` | cp311 휠 + 보존 메타데이터 | 427,494 |

생성기는 [scripts/build_v1_golden_manifest.py](../scripts/build_v1_golden_manifest.py)이고 저장소에 커밋돼 있다.

## 봉인

```yaml
stage: python-314-local-implementation-20260919 / B-1
live_read_at: 2026-09-19T13:00:00Z
record_count: 992
distinct_names: 374
v1_v2_identical: 992
v2_newly_rejected: 0
new_collisions: 0
manifest_sha256: 155c3730cefd1eb719008db1b845242723b64ed1e4fee8d6b2779c12b83ec2f2
sorted_name_digest_algorithm: >-
  이름 UTF-8 bytes 를 bytewise 오름차순 정렬, 각 항목 앞 4-byte big-endian 길이,
  이어 쓴 뒤 SHA-256
sorted_name_sha256: 6799320431184ab48586edde770d35d8f69471fe6efbaffcb291efc968b85092
v1_golden_vectors_sha256: 90357186864572ec5850f3ff91fc952b6ab97e2f9a5d2e03b3c69cf3921fc512
generator_python: 3.11.9
generator_unicodedata2: 15.0.0
generator_stdlib_unicodedata: 14.0.0
```

## 대상 집합

| 출처 | 레코드 |
| --- | --- |
| Staging `folders` | 232 |
| Staging `documents` | 5 |
| 로컬 `sync_folders` | 225 |
| 로컬 `sync_documents` | 1 |
| 파일시스템 (`작품목록` `메인` `백업` `공유` `saves`) | 529 |
| **합계** | **992** (고유 이름 374, 삭제 플래그 59) |

Staging `documents`가 660행 중 5건만 잡히는 것은 나머지 655행의 `name`이 NULL이기 때문이다. `LEGACY` 작품은 이름을 `relative_path`로만 들고 있고 `name`·`storage_name_key`는 v2 경로에서만 채워진다.

### 262건과의 관계

지시서와 인계 문서의 `실데이터 262건`은 **재현하지 못했다.** 2026-09-19 조사의 스크립트와 입력 목록이 `_evidence`에 남지 않았고, Staging(고유 38) · 로컬 원고(고유 346) · 두 집합의 조합 어느 것도 262와 맞지 않는다. 근접치는 `백업+메인` 257, `백업+작품목록` 284였다.

따라서 262를 되살리는 대신 **재현 가능한 집합을 새로 정의하고 그 정의를 함께 봉인했다.** 위 표가 그 정의이며, 같은 입력에 대해 `manifest_sha256`이 재현된다.

## 완료 기준 판정

### ① 실데이터 검사 — 거부 0

| 지표 | 기준 | 실측 | 판정 |
| --- | --- | --- | --- |
| `v2_newly_rejected` | 0 | **0** | 통과 |
| `new_collisions` | 0 | **0** | 통과 |
| `stored_key_mismatch_v1` | 0 | **0** | 통과 |
| `stored_key_mismatch_v2` | 기록만 | **0** | — |

`stored_key_mismatch_v2 = 0`은 합격/불합격이 아니라 전환 규모 실측치다. **저장된 키 248건 중 v2 전환 시 갱신이 필요한 행은 0개다.**

v1과 v2는 992건 전부에서 같은 키를 냈다 (`v1_v2_identical: 992`, `v1_v2_differing: 0`). 양쪽 모두 거부 0건이다.

#### 형제 충돌

검사 단위는 `(scope, parent_folder_id, key)`이고 폴더와 문서가 하나의 형제 네임스페이스를 이룬다.

| 기준 | v1 | v2 | 신규 |
| --- | --- | --- | --- |
| 활성 행만 | 0 | 0 | **0** |
| 삭제 행 포함 | 7 | 7 | 0 |

삭제 행을 포함하면 7쌍이 나오지만 전부 **원본 이름이 문자 그대로 동일한 폴더 쌍**이고, 각 쌍은 `is_deleted=true` 1건 + `is_deleted=false` 1건이다. 이름을 그대로 유지하는 소프트 삭제 묘비가 후속 폴더 옆에 남아 있는 것이라 충돌이 아니다. 정규화 때문에 생긴 것이 아니므로 v1·v2 차이와도 무관하다.

### ② 적합성 벡터 — 거부가 나와야 정상

| 집합 | 기준 | 실측 | 판정 |
| --- | --- | --- | --- |
| 계약 0.2.0 벡터 15건 v1 결과 일치 | 15/15 | **15/15** | 통과 |
| 계약 0.3.0 벡터 29건 | 29/29 | **미수행** | 자산 부재 |

0.3.0 벡터 자산은 이 저장소에 없다. `sync-contract/conformance_vectors/`에는 `storage-name-v1.json`(0.2.0, 15건)만 있고, 29건은 `ChocoS-yrup/Writerpad` 쪽에 있다. **단계 D(계약 0.3.0 등재)에서 자산을 가져온 뒤 수행한다.**

이 두 집합은 ①의 `v2_newly_rejected` 집계에 섞지 않았다.

## 추가 보존

### `unicodedata2 15.0.0` 휠

cp314 휠이 없고 이 PC에 MSVC 빌드 도구도 없으므로 설치본이 사실상 유일본이다. **네트워크를 쓰지 않고** site-packages의 `.pyd`와 `dist-info`로 설치 가능한 휠을 복원했다.

```yaml
wheel_filename: unicodedata2-15.0.0-cp311-cp311-win_amd64.whl
wheel_sha256: 302b56485f4e77a508a4a583e2d09d0b2ff13921cb28c2da7677cc52077cfdea
pyd_sha256: 7b4c6eca04ba6c9d2334bb55fd820de89e35b03052911d8a2df3160942bbfd43
python_tag: cp311-cp311-win_amd64
```

`pip install --no-index --no-deps --target <tmp>`로 격리 설치해 `unidata_version == 15.0.0`과 NFKC 표본 동작을 확인했다.

### 계약 벡터 형식 보존

`v1-golden-vectors.json`은 `sync-contract/storage-name-vectors.schema.json`을 충족한다. `vector_id`는 `GOLDEN-0001`〜`GOLDEN-0374`로 고유하고, 374건 전부 `valid: true`이며 `normalized`와 `utf8_hex`를 갖는다. **파이썬 환경이 사라져도 순수 데이터로 v1 동작이 남는다.**

## 이번 단계에서 드러난 사항

### 로컬과 서버의 `storage_name_key` 표현이 다르다

로컬 SQLite는 **정규화된 텍스트**(`typeof` = `text`)로, 서버는 **`bytea`**로 저장한다. 같은 값을 가리키지만 표현이 다르므로 두 쪽을 직접 비교하면 전부 불일치로 나온다. manifest는 양쪽을 UTF-8 hex로 환산해 비교한다.

처음 생성했을 때 이 차이 때문에 `stored_key_mismatch_v1`이 226(= 로컬 저장 키 전체)으로 나왔다. **데이터 무결성 문제가 아니라 비교 기준의 문제였다.**

### Windows Python의 기본 stdio 인코딩

한글 이름을 기본 인코딩으로 읽으면 대리 문자(`\udcec`)로 깨진다. v1/v2 정규화 결과를 오염시킬 수 있으므로 이 계열 스크립트는 입출력 전 구간에서 UTF-8을 명시해야 한다.

### `_reject_supplementary_adjacency()`의 stdlib 의존

`sync_contract.py:137`의 이 함수는 `unicodedata.combining()`을 쓴다. 내장 `unicodedata`는 3.11에서 14.0.0, 3.14에서 16.0.0이다. 이번 992건에서는 영향이 없었지만(거부 0건) 인계 문서가 지목한 경화 과제 그대로 남아 있다.
