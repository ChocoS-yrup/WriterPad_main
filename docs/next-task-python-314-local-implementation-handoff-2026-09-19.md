# 다음 작업 인계: Python 3.14 전환 — 로컬 구현 착수

> **사용자 최종 판단 (2026-09-19):** 전환 추진, **로컬 구현 착수 가능, 서버 변경은 조건부.**
> 서버 변경 없이 가능한 작업부터 진행한다. 각 단계를 끝낼 때마다 검증 결과와 다음 변경 범위를 제시하고 승인을 받은 뒤 넘어간다.

## 먼저 읽을 것

**[docs/python-314-transition-development-order-2026-09-19.md](python-314-transition-development-order-2026-09-19.md)** — 이번 작업의 개발지시서. 단계 A〜H, 중단 조건, 변경 대상 파일 목록이 전부 여기 있다. 이 인계 문서는 그 요약과 현재 상태만 담는다.

지시서는 2026-09-19 읽기 전용 조사 결과로 작성됐고 사용자 보완 지시 5건이 반영된 확정본이다.

## 기준선

```yaml
stage_id: python-314-local-implementation-20260919
repository: https://github.com/ChocoS-yrup/WriterPad_main
branch: feat/contract-handshake-closed-gate
head: 0244d82
uncommitted: 285 (modified 13 / untracked 272)
target_server: WriterPad Staging (mhpnszcorfzrvhyondxr)
contract_now: 0.2.0 / storage-name-v1 / digest 416c1b99...
contract_target: 0.3.0 / storage-name-v2 / digest abbd234c...
python_now: 3.11.9 (+ unicodedata2 15.0.0)
python_target: 3.14.6
```

## 다음 작업 순서

### 1. 단계 A — 현재 상태 동결 (여기부터 시작)

미커밋 285개가 쌓여 있다. **이것이 파이썬 버전보다 급한 리스크다.**

1. 전체 폴더를 그대로 복사해 보존 — 되돌아올 지점
2. untracked 272개를 분류 — 소스 / 빌드 산출물 / 캐시 / 임시 파일
3. 현재 정상 작동 상태를 WIP 커밋 또는 별도 브랜치로 묶기
4. 분류 결과에 따라 `.gitignore` 정리

**자동 정리·일괄 삭제 금지.** `git clean` 계열을 쓰지 않는다. `_evidence` 아래 진단 스냅샷은 과거 검증 근거이므로 임의로 지우지 않는다.

이번 세션에서 만든 `docs/python-314-transition-development-order-2026-09-19.md`와 이 인계 문서도 untracked에 포함돼 있다. 분류 시 소스로 취급한다.

### 2. 단계 B-0 — 라이브 상태 읽기 전용 확인

manifest보다 먼저 한다. 문서 숫자(2026-09-07 기록)를 그대로 쓰지 않는다.

- 작품·폴더·문서의 현재 행 수
- 각 작품의 `project_sync_mode` / `migration_epoch` / `active_contract_sha256`
- `sync_contract_allowlist` 행별 `enabled` / `revoked_at`
- 적용된 마이그레이션 목록
- 현재 저장된 `storage_name_key` 값

**자격증명이 필요하다.** Windows 자격증명 저장소나 service_role 키를 쓰게 되므로, 실행 전에 사용자에게 방법을 확인한다.

### 3. 단계 B-1 — v1 golden vectors / manifest 생성

3.11 + `unicodedata2 15.0.0`이 살아 있을 때만 만들 수 있다. 필드·봉인·민감도 처리는 지시서 참조.

완료 기준이 두 갈래다. 실데이터는 거부 0이어야 하고, 적합성 벡터는 거부가 나와야 정상이다. 섞지 않는다.

### 4. 단계 D → E → F → G

지시서 순서대로. E에서 코드를 고치고, F에서 3.14 venv 검증, G에서 exe 재빌드.

## 이번 작업에 포함하지 않는 것

아래는 **별도 승인 후 진행**한다. 로컬 구현 범위 밖이다.

- prod 변경 일체
- 기존 데이터 삭제
- allowlist 활성화 (`enabled=true`)
- 작품 mode/epoch 전환
- legacy RPC 다이제스트 제약 실제 변경
- Python 3.11 제거 (단계 H)

## 반드시 알고 시작할 것

### iPad는 조건부로만 무영향이다

`20260811020000_sync_contract_0_1_0_rpcs.sql`의 실행 경로 4곳(`368`, `418`, `1619`, `1798`)에 **0.2.0 다이제스트가 하드코딩**돼 있다.

```sql
if v_actual_mode in ('MIGRATING', 'ID_BASED') then
  if v_settings.contract_enforcement_started_at is null
     or v_settings.active_contract_sha256 <>
       '416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670' then
    raise exception using errcode = 'P0001', message = 'CONTRACT_NOT_ALLOWED';
```

0.3.0 마이그레이션은 이 함수들을 재정의하지 않는다. 작품을 `MIGRATING`/`ID_BASED`로 올리면서 계약을 0.3.0으로 두면 **iPad가 하드 차단된다.**

작품이 `LEGACY/0`에 머무는 동안에는 영향이 없다. 이번 로컬 범위는 전부 `LEGACY/0` 유지다.

### 3.14 블로커는 v2 전환에 흡수된다

`sync_contract.py`의 `_unicode15_module()`가 `unidata_version == "15.0.0"`을 강제한다. 3.14 내장은 16.0.0이고 `unicodedata2==15.0.0`은 cp314 휠이 없다 (이 PC에 MSVC 빌드 도구도 없음).

그런데 `normalize_storage_name_v2()`는 `unicodedata2`를 쓰지 않는다. v2로 전환하면 의존이 사라지므로 **별도 과제가 아니다.**

### 롤백은 두 종류다

Git 기준점과 3.11 보존은 **로컬만** 되돌린다. 단계 C 이후 서버 변경·작품 전환·새 데이터 기록은 별도 복원 절차가 필요하고, 작품 mode/epoch 전환은 되돌리는 공식 경로가 없다.

이번 로컬 범위는 ① 로컬 롤백 안에만 머무른다.

## 반복하지 않을 완료 작업

2026-09-19 조사에서 이미 실측했다. **다시 조사하지 않는다.**

| 항목 | 결과 |
| --- | --- |
| 클라이언트 v2 ↔ 계약 0.3.0 벡터 29건 | 3.11 29/29, 3.14 29/29 |
| v2 결과의 3.11 ↔ 3.14 동일성 | 282건 검사, 불일치 0 |
| v1 ↔ v2 키 차이 (실데이터 262건) | 동일 258, 결과 다름 0, 둘 다 거부 4 |
| v2 신규 충돌 | 0건 |
| 기존 고정 버전의 3.14 휠 | 14개 중 13개 존재, `unicodedata2==15.0.0`만 없음 |
| `openai==2.44.0` 3.14 지원 | 있음. **올리지 않는다** |
| 서버 v2 SQL | 4041줄 대조 완료 |
| iPad 소스 | Swift에 `contract` 언급 0개, RPC 7종만 호출 |
| Windows 동결 테이블 ↔ 0.3.0 계약 자산 | SHA256 일치 확인 |

Unicode 일반 조사도 반복하지 않는다. 필요한 결론은 지시서에 있다.

## 미해결·재확인 필요

- prod / Staging의 **현재** 행 수와 allowlist 상태 (문서는 2026-09-07 기록)
- prod에 마이그레이션이 몇 개 적용돼 있는지
- Windows 설치본이 Staging에 붙어 있는 것이 맞는지
- 서버 `private.storage_name_v2_result()` 실제 실행 결과 29건
- 클라이언트 `_reject_supplementary_adjacency()`의 `unicodedata.combining()` 의존 (경화 과제, 이번 필수 아님)

## 작업 방식

- 각 단계 완료 시 **검증 결과 + 다음 변경 범위**를 제시하고 승인을 받는다. 중간에 범위를 넓히지 않는다.
- 계약 산출물(`sync-contract/`)은 RFC 8785 정규화 바이트에 SHA-256이 봉인돼 있다. 손으로 편집하지 않는다.
- 서버·DB를 수정하는 작업은 이번 범위에 없다. 읽기만 한다.
- 단계 G까지의 도착점: **3.14로 빌드된 exe가 Staging에서 LEGACY/0 작품을 정상 동기화하는 상태.** v2 코드는 들어가 있지만 서버가 0.3.0을 허용하지 않는 한 실제 경로는 여전히 v1이다.

## 참고 자료

| 문서 | 내용 |
| --- | --- |
| `docs/python-314-transition-development-order-2026-09-19.md` | 이번 작업의 개발지시서 (필독) |
| `docs/server-contract-preflight-2026-09-06.md` | Staging 서버 상태 실측 (2026-09-06) |
| `docs/next-task-seoul-prod-handoff-2026-09-07.md` | prod 전환 보류 결정과 서버 비교 |
| `ChocoS-yrup/Writerpad` | iPad 앱 + 서버 마이그레이션 + 0.3.0 계약 |
| `sync-contract/contract-lock.json` | 현재 Windows 계약 (0.2.0) |
