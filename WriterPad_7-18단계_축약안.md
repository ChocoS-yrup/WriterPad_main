# WriterPad 7~18단계 축약안

작성일: 2026-08-15
대상: 7단계 이후 남은 전 단계
전제: **혼자 쓰는 Windows ↔ iPad 동기화 앱. 배포·공개·타인 사용 없음.**

동기화 흐름의 정의:
`iPad 자동저장 → 서버 업로드 → Windows가 변경 인지 → 서버에서 다운로드 → 반영` (양방향)

---

## 0. 이 문서의 목적

7~18단계 절차는 "내가 통제할 수 없는 다수의 클라이언트가 붙는 공개 서비스"를 전제로 짜여 있습니다.
실제 목적은 **기기 두 대를 오가며 혼자 글 쓰는 것**입니다. 이 차이 때문에 생긴 초과분을 걷어내는 것이 이 문서입니다.

기능을 줄이자는 게 아니라 **절차를 줄이자**는 것입니다.

---

## 1. 먼저 확인한 것: 동기화 흐름은 이미 동작 중

목표한 흐름이 실제로 구현되어 있는지부터 확인했습니다.

| 구간 | 상태 | 근거 |
|---|---|---|
| iPad 자동저장 → 서버 업로드 | 있음 | iPad Sync 계열 |
| Windows가 원격 변경 인지 | **있음 — 5초 폴링** | `mode_writing.py:334` `setInterval(5000)` |
| Windows가 서버에서 다운로드 | 있음 | `pull_remote_changes_async()` → `V2PullWorker` |
| Windows 변경 → 서버 업로드 | 있음 | `retry_pending_syncs` + 큐 |
| iPad가 원격 변경 인지 | 있음 | `SyncV2RealtimeTrigger.swift` (330줄) |

```
mode_writing.py:333-337
  self.remote_pull_timer = QTimer(self)
  self.remote_pull_timer.setInterval(5000)
  self.remote_pull_timer.timeout.connect(self.request_remote_sync)
  self.remote_pull_timer.start()
```

→ `request_remote_sync()` → `pull_remote_changes_async()` → `V2PullWorker`가
`documents` / `folders` / `folder_versions` 조회

**결론: 파이프라인은 이미 처음부터 끝까지 연결되어 있습니다.**

그래서 남은 7~18단계의 성격이 달라집니다. 지금 남은 건 **흐름을 만드는 일이 아니라 어긋나는 경우를 고치는 일**입니다.

| 남은 진짜 문제 | 해당 단계 |
|---|---|
| 이름 빠르게 6번 바꾸면 3건이 대기에 남음 | 12 · 15 |
| 한글 이름이 NFC/NFD로 갈라져 폴더가 둘이 됨 | 8 · 9 |
| 응답 유실 시 중복 적용 / 유실 | 13 · 14 |

**이 세 가지가 남은 작업의 전부**이고, 나머지는 그걸 하기 위한 절차입니다.

---

## 2. 판단 기준

1. **원고가 깨지거나 사라지는가?** → 남긴다
2. **두 기기가 서로 다른 상태로 갈라지는가?** → 남긴다
3. **내가 통제할 수 없는 제3자·미래 클라이언트를 방어하는가?** → 버린다

3번에 해당하는 게 지금 절차의 대부분입니다. 클라이언트는 두 개고 둘 다 직접 만들고 직접 업데이트합니다.

---

## 3. 한눈에 보는 축약표

| 단계 | 원래 내용 | 판정 | 축약 후 |
|---|---|---|---|
| 7 | staging 배포 + 계약 검증 + allowlist + 증거 커밋 | **대폭 축소** | migration 적용 확인 + 앱으로 직접 써보기 |
| 8 | Windows storage-name-v2 | **유지** | 절차만 간소화 |
| 9 | iPad storage-name-v2 | **유지** | 동일 |
| 10 | Windows가 iPad 검토 | **삭제** | 공유 테스트 벡터 파일로 대체 |
| 11 | iPad가 Windows 검토 + 병합 | **삭제** | 벡터 결과 같으면 양쪽 머지 |
| 12 | incident 포렌식 | **성격 변경** | 보존증거 재구성 → **재현 시도** |
| 13 | protocol 3 전송 기반 | **축소** | RFC 8785 정식 구현 → 정렬 JSON |
| 14 | document_commit 배선 | **유지** | 검증만 실사용으로 |
| 15 | atomic_structure_commit 배선 | **유지** | 선행 게이트 제거 |
| 16 | staging 종단간 검증 | **축소** | 30분 수동 체크리스트 |
| 17 | production 준비 게이트 | **삭제** | 백업/복구 확인만 흡수 |
| 18 | production 제한 배포 | **삭제** | DB 하나로 통합 |
| — | (없음) | **신규 추가** | **일별 원고 백업** |
| — | (없음) | 선택 | 폴링 → 구독 전환 (지연 단축용) |

**12단계 → 5단계 + 신규 1건.**

---

## 4. 단계별 상세

### 7단계 — staging 배포와 서버 계약 검증

**지금 상태**: corrective migration은 Staging에 적용 완료. 하네스 실행 승인 대기 중.

#### 버릴 것

| 항목 | 이유 |
|---|---|
| 1,912줄 exact harness를 "무수정 정확히 1회" 실행 | RPC 2개 검증에 과함. 앱으로 써보면 같은 걸 확인함 |
| 임시 CLI login role 발급 → 사용 → 폐기 확인 | 하네스를 원격 psql로 돌리려고 생긴 절차. 하네스를 안 돌리면 통째로 사라짐 |
| macOS에 libpq/psql 설치 | Supabase Dashboard의 SQL Editor로 충분 |
| allowlist 활성화 → 검증 → 재비활성화 왕복 | **켜고 그냥 두면 됨.** 껐다 켰다 하는 것 자체가 사고 원천 |
| evidence commit + fingerprint digest 기록 | 감사 대상이 없음 |
| test_run_id / server_project_id / client_build_id 추적 | 실행이 한 번뿐임 |
| 합성 fixture 프로젝트 생성·승격 | 실제 프로젝트로 하면 됨 (백업이 있다면) |

#### 남길 것

- migration 5개가 적용됐는지 확인 (Dashboard에서 1분)
- 서버 함수를 건드렸으니 **기존 동기화가 안 깨졌는지 회귀 확인**
- **폴더 이름 6번 빠르게 변경** — 12단계 사건 재현 시도

#### ⚠ 중요: 새 RPC는 아직 아무도 안 부릅니다

corrective migration이 고친 두 함수를 iPad는 **아직 호출하지 않습니다.**

| iPad가 실제로 호출 | 이번에 고친 새 RPC |
|---|---|
| `commit_document` | `document_commit` |
| `commit_folder` | `atomic_structure_commit` |
| `ensure_project` / edit lease 계열 | |

배선은 D단계(구 13~15)에서 이뤄집니다. 따라서:

- **0.3.0 allowlist를 켜도 지금은 아무 효과가 없습니다.** 앱이 그 경로를 안 타기 때문입니다. D단계 전에 켜면 됩니다 (미리 켜둬도 무해)
- **앱 테스트로 새 RPC를 검증할 수 없습니다.** 지금 앱 테스트가 확인하는 건 *기존 경로의 회귀*입니다
- 새 RPC 검증은 D단계에서 실제로 호출하기 시작할 때 자연히 이뤄집니다. 아직 아무것도 그 경로에 의존하지 않으므로 지금 검증할 이유가 없습니다

> `SyncV2Store.swift:250`의 `documentCommit = "document_commit"`은 로컬 큐의 작업 종류 이름표이지 네트워크 호출명이 아닙니다. 혼동 주의.

#### ⚠ Windows 동기화는 현재 로그인되어 있지 않음

Windows는 **설정 → 클라우드 계정 → "동기화 로그인"** 에 걸려 있는 상태입니다.
코드로 막힌 게 아니라 계정 세션이 없는 것입니다.
(`.env`에는 `SUPABASE_URL`·`SUPABASE_KEY`만 있고, 계정 이메일/비밀번호는 앱 UI에서 입력하는 구조)

**그래도 진행에 지장 없습니다.** 12단계 사건은 `iPad → 서버` 구간에서 끝나는
로컬 큐 발송 문제라서 iPad 단독으로 재현·검증됩니다.
Windows 수신 확인은 있으면 좋을 뿐 필수가 아닙니다.

B단계(storage-name-v2)도 공유 벡터에 대한 단위 테스트라 라이브 동기화가 필요 없습니다.

#### 대체 절차 (약 15분, iPad 단독)

1. Supabase Dashboard → SQL Editor에서 migration ledger 5건 / pending 0 확인
2. iPad에서 문서 저장 → 서버 반영 확인 (회귀 확인)
3. iPad에서 한글 폴더 이름 변경 → 서버 반영 확인 (회귀 확인)
4. **iPad에서 폴더 이름을 빠르게 6번 변경** ← 유일하게 새 정보를 주는 항목
   - iPad 대기 큐가 0으로 떨어지는지
   - 서버에 6건이 반영됐는지
   - 남으면 몇 번째부터 남는지 + 로그 + 큐 상태
5. allowlist는 켜도 되고 안 켜도 됨 (D단계 전까지 무효과)

> **자격증명 관련 판단**: 아이패드가 제안한 "임시 role 발급 후 즉시 폐기"는 설계 자체로는 위생적입니다(장기 자격증명을 안 남김). 문제는 **그런 절차가 필요할 만큼 하네스가 커진 것**입니다. 하네스를 안 돌리면 토큰·비밀번호를 다룰 일 자체가 없어집니다. 이번에 "다른 프로젝트 목록 노출" 범위 위반이 한 번 발생했는데, 절차가 복잡할수록 이런 사고가 늘어난다는 근거이기도 합니다.

---

### 8·9단계 — Windows / iPad storage-name-v2 클라이언트

**판정: 유지.** 여기가 실제 버그 수정입니다.

한글 파일명이 iPad는 NFD, Windows는 NFC로 다뤄져서 같은 폴더가 둘로 갈라지는 문제. 혼자 쓴다고 안 터집니다. 오히려 혼자 두 기기를 오가니까 **더** 터집니다.

#### 남길 것 (거의 전부)

- baseline / excluded / casefold 동결 자산을 양쪽 동일 digest로 사용
- NFKC 전후 검사 순서
- SN-001 ~ SN-029 전량 통과
- `storage_name_v1` 제거하지 않음 (기존 원고 보호)
- Xcode compile source 등록 확인 (실제로 자주 빠뜨림)
- `unicodedata2` 참조 전수조사 후 제거 판단 (패키징 크기에 영향)

#### 버릴 것

- draft PR 생성 → 상대 검토 대기 → 인계표 작성 → 중단
- base_main_sha / contract_git_commit / canonical_contract_sha256 명시적 pin 전달

→ **그냥 브랜치에서 작업하고, 테스트 통과하면 머지.**

---

### 10·11단계 — 양쪽 교차 검토

**판정: 삭제.**

이 두 단계의 실질 내용은 "12개 입력에 대해 Windows와 iPad가 같은 결과를 내는가"입니다.
그건 사람(또는 에이전트)이 SHA 대조하며 볼 일이 아니라 **파일 하나로 자동화할 일**입니다.

#### 대체: 공유 벡터 파일

```
sync-contract/vectors/storage_name_v2_cases.json
```

```json
[
  {"id": "SN-016", "input": "U+AB70",         "valid": true,  "utf8_hex": "..."},
  {"id": "SN-017", "input": "U+1C80",         "valid": true,  "utf8_hex": "..."},
  {"id": "SN-0xx", "input": "U+13046 U+FF9E", "valid": false, "error": "..."}
]
```

- Windows: pytest 한 개가 이 파일 읽고 전량 확인
- iPad: XCTest 한 개가 같은 파일 읽고 전량 확인
- 둘 다 초록불이면 끝. **검토 문서도, 인계표도, 승인도 필요 없음**

이게 이 문서에서 가장 큰 치환입니다. 교차검토 의식 2단계 → 파일 1개.

---

### 12단계 — 이름변경 3건 대기 사건

**판정: 목적은 유지, 방법은 전면 변경.**

원래 절차는 보존 DB/snapshot에서 6건의 intent·operation_id·revision·event·attempt를 시간순 재구성하고, 증거가 불완전하면 `not-verified`로 중단하며, **그러면 15단계를 시작할 수 없게** 되어 있습니다.

이건 사후 감사 보고서를 쓰는 방식입니다. 목적은 감사가 아니라 **고치는 것**입니다.

#### 대체: 재현

1. 지금 코드로 폴더 이름을 빠르게 6번 바꿔본다
2. 재현되면 → 로그 보고 고친다 (원인 규명 완료)
3. 재현 안 되면 → 그동안 커밋이 많았으니 이미 고쳐졌을 수 있음. 15단계에서 방어 로직만 넣고 넘어간다

#### 삭제할 것

- `test_run_id` / `server_project_id` / build ID / digest 사전 확보 요구
- 보존 DB·snapshot 읽기전용 취급 및 digest 확인 절차
- `codex/incident-<test_run_id>` 식별정보 제거 증거 브랜치
- **`not-verified`이면 15단계 진입 금지 게이트** ← 후반부 최대 병목이었음

이 게이트를 없애면 12단계가 더 이상 15단계를 막지 않습니다.

---

### 13단계 — protocol 3 전송 기반

**판정: 절반 유지, 절반 축소.**

#### 남길 것 (동기화 정확성의 실제 심장)

| 항목 | 이유 |
|---|---|
| batch_id / operation_id | 재시도 시 중복 적용 방지 |
| payload digest | "이미 처리한 요청인가" 판별 |
| 응답 유실 후 replay | 네트워크 끊김은 실제로 일어남 |
| append-only event로 상태 파생 | 앱 재시작 후 큐 복구에 필요 |
| durable queue | 오프라인에서 쓴 글이 안 날아가게 |

#### 축소할 것

| 원래 | 대체 |
|---|---|
| RFC 8785 제한 부분집합 canonical JSON 정식 구현 | 양쪽이 **같은 방식으로 정렬**하면 충분<br>Swift: `JSONEncoder.OutputFormatting.sortedKeys`<br>Python: `json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False)` |
| fail-closed server compatibility 검사 | 삭제. 클라이언트가 둘 다 내 것이고 같이 업데이트함 |
| 계약 오류 코드 전체 체계 | 3가지로 축약: **재시도 가능 / 재시도 불가 / 사용자 개입 필요** |
| 엄격한 성공·실패 응답 스키마 검증 | 파싱 실패 시 재시도 불가로 처리 |
| immutable batch 메타데이터 8필드 | 필요한 것만 (batch_id, device_id, digest 정도) |

digest는 남깁니다 — 재시도 판별에 실제로 쓰입니다.
버리는 건 **digest를 만드는 방식을 RFC로 규격화하는 것**이지 digest 자체가 아닙니다.

---

### 14단계 — document_commit 배선

**판정: 유지.** 본문 쓰기 경로라 원고 손실에 직결됩니다.

#### 남길 것

- 빈 본문 처리
- content digest / byte count
- revision 충돌 처리
- replay (응답 유실)
- 문서 update가 name / parent_folder_id / structure_revision을 못 바꾸게 하는 제약

#### 축소할 것

- staging 합성 프로젝트 검증 → **실제 프로젝트로 써보기**
- draft PR + 인계표 → 머지

---

### 15단계 — atomic_structure_commit 배선

**판정: 유지.** 12단계 사건의 실제 수정이고, **남은 작업 중 가장 중요합니다.**

#### 우선순위 높음 (반드시)

- sequence 1부터 연속
- 부분 적용 없음 (전부 성공 또는 전부 실패)
- exact replay
- **빠른 이름변경 6건** ← 사건 재현 케이스
- 앱 재시작 후 queue 복구

#### 우선순위 낮음 (여유 되면)

- batch digest 일치
- changed-payload ID reuse 거부
- revision / structure revision 정합
- operation event와 terminal state

#### 삭제

- "incident 직접 원인이 verified가 아니면 시작 금지" 선행 게이트

---

### 16단계 — 종단간 검증

**판정: 유지하되 성격 변경.** 감사 절차 → 평범한 체크리스트.

#### 삭제

- 네 저장소 SHA의 main 도달 가능성 사전 확인
- 전용 합성 사용자·프로젝트 신규 생성
- 동일 `test_run_id` / `server_project_id` 추적
- 로그·DB 증거 식별정보 제거 후 보존 + digest 기록
- 별도 evidence 브랜치와 커밋 SHA 인계

#### 남길 것 — 실사용 체크리스트 (약 30분)

- [ ] Windows에서 만든 문서가 iPad에 나타남
- [ ] iPad에서 고친 내용이 5초 안에 Windows에 반영됨
- [ ] 한글 이름 NFC/NFD 수렴 (같은 폴더가 둘로 안 갈라짐)
- [ ] 거부돼야 할 이름이 거부됨
- [ ] 문서 생성 / 수정 / 삭제 / 복원
- [ ] **폴더 이름 6번 빠르게 변경 → 전부 반영** ← 핵심
- [ ] 비행기모드 → 글 쓰기 → 해제 → 반영
- [ ] 앱 강제 종료 → 재실행 → 대기 작업 복구
- [ ] 양쪽 대기 건수 0으로 수렴
- [ ] 양쪽에서 같은 문서를 거의 동시에 고쳤을 때 한쪽이 사라지지 않음

---

### 17·18단계 — production 준비 게이트 / 제한 배포

**판정: 삭제. DB를 하나로 통합.**

staging과 production을 따로 두는 건 "실사용자가 있어서 실험을 분리해야 할 때" 필요합니다.
사용자가 본인 한 명이면 프로젝트를 둘 유지하는 비용이 이득보다 큽니다.

#### 흡수해서 남길 것 두 가지

1. **백업 및 복구 가능성 확인** (17단계에서 유일하게 가치 있는 항목) → 아래 별도 트랙으로 승격
2. **기존 프로젝트를 LEGACY/0으로 유지하고 자동 승격하지 않는다** → 원칙으로 유지. 기존 원고가 새 경로로 강제 이동하지 않게

#### 삭제

- production migration ledger / allowlist 사전 판정
- 신규 allowlist 행 enabled 전후 예상 상태 보고
- 구형 클라이언트 허용 조건 (구형 클라이언트가 없음)
- 배포할 최소 client build 강제
- 단계별 읽기전용 검증 + 즉시 중단 규약
- 실패 시 신규 쓰기 차단 절차

---

## 5. 신규 추가

### 신규 — 일별 원고 백업 ★ 최우선

이 모든 절차가 방어하려는 최악은 결국 **원고 손실**입니다.
그건 계약 검증이 아니라 백업으로 막는 게 훨씬 싸고 확실합니다.

- 하루 한 번 프로젝트 전체를 평문 텍스트/마크다운으로 내보내기
- 로컬 폴더 + 클라우드 드라이브 두 곳
- 날짜별 보관, 최근 30일 유지
- **복구를 한 번은 실제로 해볼 것** (안 해본 백업은 백업이 아님)

이게 있으면 동기화 버그가 **재앙에서 성가심으로 강등**됩니다.
그리고 그 순간 남은 게이트 대부분이 존재 이유를 잃습니다.

> 참고: `BackupWorker`와 `RetentionWorker` 클래스가 이미 `sync_manager.py`에 있습니다. 새로 만들기 전에 이게 무엇을 백업하고 있는지부터 확인하는 편이 빠릅니다.

### 선택 — 폴링을 구독으로 전환

지금은 5초 폴링이고, 이걸로 목표 흐름은 충족됩니다. 굳이 안 바꿔도 됩니다.

바꾸면 얻는 것:
- 반영 지연 5초 → 1초 미만
- 앱 켜놓은 동안 계속 나가는 주기적 조회(문서/폴더/폴더버전 3종)가 사라짐

바꾸면 드는 비용:
- 서버는 이미 준비됨 (`documents`, `folders`가 `supabase_realtime` publication에 있음)
- `supabase-py`의 realtime 클라이언트도 이미 패키징 환경에 포함되어 있음
- 필요한 건 구독 → 기존 `pull_remote_changes_async()` 호출. **새 엔진이 아니라 트리거만**
- 연결 실패 시 기존 5초 타이머로 자동 강등되게 두면 안전

**우선순위는 낮습니다.** 정확성 문제(12·15단계)를 먼저 끝내고 여유 있을 때 하면 됩니다.

---

## 5.5. 0단계 — DB 통합과 Windows 클라우드 로그인 복구

**B단계보다 먼저 와야 합니다.** 원래 계획에 없던 항목입니다.

### 배경: 프로젝트가 둘입니다

| 프로젝트 | 정체 | 상태 |
|---|---|---|
| `isotfvmlklrxspusjpcn` | **ChocoS-yrup's Web** — Windows 앱이 써온 실사용 DB | 스키마가 마이그레이션 체인으로 만들어지지 않음. baseline 버전 ID 3개가 ledger에 없음 |
| `mhpnszcorfzrvhyondxr` | **WriterPad Staging** — 7단계 검증용 | 마이그레이션 5개가 깨끗하게 적용된 **유일한** 프로젝트 |

원래 18단계 계획은 `Staging에서 검증 → 17·18단계에서 실사용 DB에 배포` 구조였습니다.
그 배포 단계가 비쌌던 이유가 바로 실사용 DB의 ledger 불일치입니다.

### 결정: Staging을 실사용 DB로 승격

기존 데이터가 전부 폐기 가능한 더미이므로, **실사용 DB를 버리고 Staging을 쓰는 쪽이 압도적으로 쌉니다.**
구 17·18단계의 ledger 정합 작업이 통째로 사라지고, 스키마가 마이그레이션 체인과 일치하게 됩니다.

### 작업 목록

**0-1. iPad가 실제로 붙어 있는 프로젝트 확인** ← 가정하지 말 것

iPad 저장소에는 실제 URL이 없습니다(테스트용 가짜만). 런타임 입력 방식입니다.
7단계 마이그레이션 작업은 Staging에 했지만, **iPad 앱 자체는 실사용 DB에 붙어 있을 수 있습니다.**
이름변경 6건 사건도 실사용 DB에서 났을 가능성이 높습니다.
iPad가 `isotf...`에 붙어 있다면 iPad도 Staging으로 옮겨야 합니다.

**0-2. Staging에 로그인 계정 생성** ← 빠뜨리기 쉬움

앱에는 **회원가입 기능이 없습니다.** `sync_manager.py`에 `sign_in_with_password`만 있고 `sign_up`이 없습니다.
Supabase 인증 계정은 프로젝트마다 별개이므로, 실사용 DB에서 쓰던 계정은 Staging에 존재하지 않습니다.

→ Supabase Dashboard → Authentication → Users → Add user 로 **직접 계정을 만들어야** 합니다.

**0-3. `release_cloud_config.json` 채우기**

```
_stage9_cloud_stabilization/release_cloud_config.json
```
```json
{
  "supabase_url": "https://mhpnszcorfzrvhyondxr.supabase.co",
  "supabase_publishable_key": "sb_publishable_..."
}
```

- publishable key는 **Staging 프로젝트 것**을 Dashboard에서 가져와야 합니다.
  루트 `.env`의 키는 실사용 DB(`isotf...`) 것이라 쓸 수 없습니다
- 형식 검사가 있습니다: `^sb_publishable_[A-Za-z0-9_-]{16,}$`
- `service_role` / `sb_secret_` 키는 거부됩니다 (넣지 말 것)

**0-4. 재빌드**

이 파일은 `sys._MEIPASS`에서 읽습니다 — **exe 안에 번들된 사본**을 봅니다.
exe 옆에 파일을 놔둬도 소용없습니다. 값을 채운 뒤 해당 디렉터리에서 다시 빌드해야 합니다.
spec이 빌드 시점에 `assert_release_config_buildable`로 값을 검증합니다.

**0-5. 로그인 및 동작 확인**

- 설정 → 클라우드 계정 → "동기화 로그인" 버튼이 활성화되는지
- 0-2에서 만든 계정으로 로그인되는지
- Staging은 **비어 있으므로** 프로젝트와 폴더를 새로 만들어야 합니다
  (C단계의 이름변경 6번 테스트를 하려면 폴더 구조가 먼저 있어야 함)

### 함께 정리해야 할 것: 최신 코드가 저장소에 없습니다

| | |
|---|---|
| 루트에 `cloud_config.py` | **없음** |
| 루트 `dist/` / `dist_next/` | 8월 2일 / 8월 3일 — cloud_config 이전 버전 |
| 실제 최신 exe | `_stage9_cloud_stabilization/dist/` — 8월 12일 |
| `_stage9_*` git 상태 | **untracked** |

**Stage 9 클라우드 안정화 작업이 main에 병합되지 않았습니다.** 루트에서 빌드하면 `.env` 방식의 옛 버전이 나옵니다.
빌드된 exe가 31개, 작업 디렉터리가 30개 넘게 흩어져 있습니다.

0-3·0-4를 하기 전에 **어느 트리를 정본으로 삼을지 확정**해야 합니다.
`_stage9_cloud_stabilization`이 정본이라면 루트로 병합하고 나머지는 정리하는 것이 순서입니다.

---

## 6. 축약 후 로드맵

```
[먼저]  0  DB 통합 + 로그인 복구
           정본 트리 확정 → Staging 계정 생성 → config 채우고 재빌드
             ↓
     A  서버 마무리            (구 7)      ~30분
        migration 확인 / iPad 단독 재현 테스트
             ↓
     B  양쪽 한글 이름 v2      (구 8+9+10+11)
        구현 2건 + 공유 벡터 파일 1개 + 양쪽 테스트
             ↓
     C  이름변경 결함 재현      (구 12 축소)
        재현되면 원인 확인, 아니면 D에서 방어
             ↓
     D  전송 기반 + 커밋 배선  (구 13+14+15)  ← 남은 작업의 핵심
        정렬 JSON / durable queue / replay / 원자 커밋
             ↓
     E  실사용 점검            (구 16)      ~30분
        체크리스트 10항목

     (구 17·18 삭제 — 0단계에서 DB를 하나로 합쳤으므로 배포 단계 자체가 없음)
     (선택: 폴링 → 구독)
     (백업: 실제 원고를 넣기 시작할 때)
```

**남은 12단계 → 5단계.** 단계 사이의 승인 게이트·인계표·교차검토는 없습니다.

---

## 7. 지금 아이패드 승인 건에 대한 판단

아이패드가 대기 중인 승인 문구는 **승인하지 않기를 권합니다.**

버리는 것:
- exact harness 원격 1회 실행
- Management API 임시 CLI login role 발급 및 폐기
- macOS libpq / psql 설치
- allowlist 활성화 → 검증 → 재비활성화 왕복
- 자격증명 비노출 규약 일체 (다룰 자격증명이 없어짐)
- evidence commit / fingerprint / test_run_id 추적

대신 아이패드에 보낼 지시문:

```
Stage 7을 축약 종료한다.

## 전제

이 프로젝트는 혼자 쓰는 Windows ↔ iPad 동기화 앱이다. 배포도 타인 사용도 없다.
따라서 다수 클라이언트를 방어하는 절차는 적용하지 않는다.

## 하지 않을 것

1. exact harness를 실행하지 마라.
2. Management API 임시 CLI login role을 발급하지 마라.
3. psql / libpq를 설치하지 마라.
4. evidence commit, fingerprint 기록, test_run_id 추적을 하지 마라.
5. 합성 fixture 프로젝트를 새로 만들지 마라.
6. 별도 production 프로젝트를 만들지 마라.
   WriterPad Staging(mhpnszcorfzrvhyondxr)을 유일한 실사용 DB로 승격한다.
   기존 실사용 DB ChocoS-yrup's Web(isotfvmlklrxspusjpcn)은 더 이상 쓰지 않는다.
   그 안의 데이터는 폐기 가능한 더미로 확인됐다.
   구 17·18단계의 production 배포와 ledger 정합 작업은 하지 않는다.

## 할 것

7. Supabase Dashboard SQL Editor에서 다음만 읽어서 보고해라.
   - migration ledger 5건
   - pending migration 0
   - 두 wrapper와 두 legacy 함수의 존재 및 실행 권한

7-1. iPad 앱이 실제로 어느 Supabase 프로젝트에 연결되어 있는지 보고해라.
   추정하지 말고 앱의 실제 런타임 설정값을 확인해라.
   mhpnszcorfzrvhyondxr(Staging)가 아니라
   isotfvmlklrxspusjpcn(ChocoS-yrup's Web)에 붙어 있다면,
   Staging으로 옮기는 데 필요한 작업을 보고만 하고 실행하지는 마라.
   Staging에는 인증 계정이 따로 있어야 하므로 계정 생성이 선행된다.

8. 0.3.0 allowlist는 켜도 되고 안 켜도 된다.
   iPad는 아직 `document_commit` / `atomic_structure_commit`을 호출하지 않고
   `commit_document` / `commit_folder`를 호출하므로, 지금 allowlist를 켜도
   동작에 아무 영향이 없다. 켠 경우 "0.3.0 경로가 검증됐다"고 보고하지 마라.
   실제 활성화가 필요한 시점은 새 RPC 배선 단계다.

9. iPad 단독으로 다음을 확인해 보고해라. 이것이 이번 단계의 실제 검증이다.
   Windows 쪽 동기화는 현재 로그인되어 있지 않으므로 Windows 수신 확인은 하지 않는다.
   이 사건은 iPad 로컬 큐가 발송하지 못한 문제이므로 iPad 단독으로 재현·검증할 수 있다.

   - 문서 저장 → 서버에 반영되는지 (기존 경로 회귀 확인)
   - 한글 폴더 이름 변경 → 서버에 반영되는지 (기존 경로 회귀 확인)
   - **폴더 이름을 빠르게 6번 변경**
     · iPad 자체 대기 큐가 0으로 떨어지는지
     · 서버에 실제로 6건이 반영됐는지 (Dashboard에서 확인)
     · 대기에 남는 건이 있으면 **몇 번째부터** 남는지, 그때 로그와 큐 상태를 함께 보고해라

## 그다음

10. 위가 끝나면 Stage 7을 종료한다. 하네스 원격 실행은 Stage 7의 완료 조건이 아니다.
11. 다음은 storage-name-v2 클라이언트 구현(구 8·9단계)이다.
    양쪽 교차 검토(구 10·11단계)는 하지 말고,
    공유 벡터 JSON 파일 하나와 양쪽 테스트로 대체해라.

## 앞으로의 작업 방식

12. 단계마다 draft PR·인계표·상대 검토·승인 게이트를 만들지 마라.
    원격 DB를 바꾸거나 되돌릴 수 없는 작업일 때만 승인을 요청해라.
```

---

## 8. 버리면 안 되는 것 세 가지

축약하다 같이 쓸려나가기 쉬운 항목입니다.

1. **한글 이름 정규화 (storage-name-v2)**
   NFC/NFD 불일치는 두 기기를 오갈 때 실제로 터집니다. 혼자 쓴다고 면제되지 않습니다.

2. **재시도 안전성 (batch_id + digest + replay)**
   5초마다 폴링하고 자동저장이 계속 도는 구조에서는 같은 요청이 두 번 나가는 상황이 잦습니다. 이게 없으면 편집이 중복 적용되거나 사라집니다.

3. **`storage_name_v1` 유지 / 기존 프로젝트 자동 승격 금지**
   지금 쓰고 있는 원고를 보호하는 장치입니다. v1 제거는 양쪽이 v2로 안정화된 뒤 한참 나중에.

---

## 9. 요약

- 과한 것은 **기능이 아니라 절차**였습니다
- 남은 12단계 → **5단계 + 백업 1건**
- 목표한 동기화 흐름은 **이미 동작 중**입니다 (Windows 5초 폴링). 남은 건 흐름을 만드는 게 아니라 **어긋나는 경우를 고치는 것**
- 남은 작업의 핵심은 **D단계(원자 커밋 + 재시도 안전성)** 하나입니다
- 지워야 할 1순위는 **단계 사이의 승인 게이트와 교차검토 인계 절차**입니다
- 실시간 구독 전환은 해도 되고 안 해도 되는 선택 항목입니다

> 단, 이 프로젝트가 "글 쓰려고 만드는 도구"가 아니라 "만드는 것 자체가 재미"라면 원래 절차도 딱히 틀린 건 아닙니다. 이 문서는 전자를 가정합니다.
