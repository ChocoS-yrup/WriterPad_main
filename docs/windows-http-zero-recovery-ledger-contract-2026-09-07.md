# HTTP 0 복구: 서버 원장 읽기 증명 제안

**Windows 클라이언트에 검사기를 구현했으나 서버 API는 이번에 구현·배포·호출하지 않았다. 아래 명세는 iPad/서버 검토가 필요한 제안이다. 현재 상태에서 실제 복구 버튼을 사용하면 안 된다.** 기존에 제출된 서버 정의에서는 이 전체 응답을 제공하는 API를 확인하지 못했다. 현재 배포 상태를 이번에 서버에 접속하여 재확인한 것은 아니다.

## 필요한 이유

일반 테이블 SELECT의 빈 결과나 `count=exact`의 0은 해당 계정이 원장 전체를 볼 수 있다는 증명이 아니다. RLS는 행을 필터링하며 테이블 GRANT와 별개다. 기존 owner/editor 확인은 작품 권한을 확인하지만 각 내부 원장의 누락 없는 조회를 보증하지 않는다. [Supabase RLS 문서](https://supabase.com/docs/guides/database/postgres/row-level-security).

관리자 SQL 내보내기 파일을 앱에 넣거나 service-role 키를 앱에 추가하는 우회는 구현하지 않았다. 신뢰할 수 있는 서버 읽기 증명이 없으면 `HTTP_ZERO_LEDGER_PROOF_REQUIRED` 또는 해당 RPC 오류로 멈춘다. 현재 Windows 검사기는 아래 응답을 합성 fixture로만 검증했다.

## 입력 제안

RPC 이름: `get_contract_recovery_preflight` (미배포 제안).

```json
{
  "p_project_id": "1bd47431-0773-482c-8eb5-ac9e2952b6f4",
  "p_batch_id": "6fd8c11b-5b74-4219-aa0b-a5d408ca8505",
  "p_request_sha256": "bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045",
  "p_operation_ids": ["7998be14-c48d-49e0-84b0-08030070d312", "f55e8fab-ea38-468e-a4fe-fbe2cf9ad127"],
  "p_nonce": "<호출마다 생성한 UUID>"
}
```

## 응답 제안

```json
{
  "kind": "contract_recovery_ledger_v1",
  "project_id": "<검증한 입력과 같음>",
  "batch_id": "<검증한 입력과 같음>",
  "request_sha256": "<검증한 입력과 같음>",
  "operation_ids": ["<입력의 첫 operation ID>", "<입력의 둘째 operation ID>"],
  "nonce": "<이 호출의 nonce>",
  "account_marker": "<검증한 auth.uid() 문자열의 SHA256 앞 16자리>",
  "authorized": true,
  "complete": true,
  "counts": {"batches": 0, "operations": 0, "attempts": 0, "results": 0},
  "checked_at": "<서버의 시간대 포함 ISO 8601 시각>"
}
```

Windows는 필드 집합과 타입, 요청 식별값, account marker, nonce, 시간대 포함 시각을 검사한다. checked_at은 클라이언트 시각 기준 과거 60초 이내/미래 5초 이내여야 한다. 모든 count가 엄격한 정수 0이어야 한다. 필드 누락, 배열 결과, 일부 count 누락, boolean 0 대용, 권한 false, complete false, 오래된 응답, 다른 nonce, 다른 계정은 통과하지 못한다. 네 범주 중 기록이 하나라도 있으면 중단한다.

## 서버가 보장해야 하는 의미

1. 인증된 현재 사용자와 해당 작품의 owner/editor 권한 및 active 상태를 검사한다. 입력은 이 고정 canary의 작품·배치·해시·두 operation ID로 제한한다. nonce는 호출을 묶는 값이며 권한 증명이 아니다. 입력값을 그대로 복사해 `authorized=true`만 반환하면 안 된다.
2. 원장 읽기에 필요한 권한과 RLS의 실제 조회 범위를 확인한다. 누락 없는 읽기를 수행할 수 없으면 오류로 반환한다. 계정이나 테이블의 정책 때문에 숨겨진 행을 0개로 선언하면 안 된다. 권한을 가진 서버 내부 읽기가 필요할 경우 공개 범용 관리자 조회를 만들지 않고 좁은 내부 함수와 명시적 인증·작품 권한 검사를 검토한다. 앱에는 관리자 키를 넣지 않는다.
3. 한 DB 읽기 snapshot에서 네 범주의 전체 count를 얻는다. `batches`: 고정 batch ID의 행. `operations`: 고정 batch ID에 속하거나 두 고정 operation ID에 해당하는 행. `attempts`: 해당 operation들의 모든 시도. `results`: 고정 batch ID의 결과. request hash가 다르거나 예상 작품과 연결이 어긋난 충돌 행을 필터로 숨기지 않는다. 고정 ID와 관련된 충돌 역시 기록 존재로 취급한다.
4. 부분 조회·페이지 상한·타임아웃·테이블 누락·권한 실패를 빈 배열로 보정하지 않는다. `complete=true`는 범주 전체를 읽었다는 서버 보증이어야 한다.
5. 이 함수는 영수증/시도/승인/큐/준비/작품/관문을 쓰거나 수정하지 않는다. 기록이 없다는 읽기 결과는 실행 승인이나 원자적인 송신 예약이 아니다. 읽기 이후 경쟁은 기존 atomic_structure_commit의 요청 식별·중복 처리와 검증으로 처리한다.

## Windows 연결과 검증 범위

초기 원격 검사와 두 번째 원격 검사에서 매번 새 nonce로 호출한다. 각각 기존 인증·핸드셰이크·작품 상태·전체 로컬 및 원격 구조 기준 확인과 함께 수행하며, 이후 기존 C9 검사와 HTTP 직전 영속 시도 기록을 통과해야 실제 쓰기 RPC에 도달한다. 기존 전송기의 retry 없는 호출을 그대로 사용한다.

서버 API가 없거나 오류를 내면 회차는 stopped/HTTP 0으로 남고 관문을 닫는다. 별도 복구 회차는 소모되며 자동으로 새 회차를 만들지 않는다. 따라서 **API 지원·권한·응답 의미를 별도 검토하고 검증하기 전 실제 회차를 만들지 않는다.**

서버 측 합의가 필요한 항목은 API 제공 방식·이름·최소 권한·네 count의 누락 없는 의미·오류 동작이다. 명세가 변경되면 Windows 연결부와 관련 fixture만 수정·재검증한다. 이번 전달은 서버 API의 보안·실행 검증 완료를 주장하지 않는다.
