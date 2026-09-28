# iPad ab89384 소스·검증·설치 기록 대조

2026-09-06 KST. 첨부 제품·테스트 패치, 검증 JSON, 설치 JSON과 GitHub 소스를 읽기 전용으로 대조했다. Windows 제품 코드, 실제 계약 관문, 서버 설정은 변경하지 않았다.

## 판정

**소스 식별과 주요 C9 보완은 확인했다. 다만 대기열의 미관측 차단·해소 전이에 대한 통합 검증이 남아, 양쪽 정책의 최종 통과 및 실제 계약 전송 준비 완료 판정은 보류한다.** 아래 차이는 실서버 오전송을 재현했다는 뜻이 아니다. Swift 실행 환경이 없는 Windows에서 실제 소스 경로와 회귀 시험의 검증 범위를 확인한 결과다.

## 1. 소스와 증거의 연결

- iPad 기준: `bb40d22164f34371f9ad7f70b9cddf208a692f83`.
- iPad 후속 커밋: `ab893843562e06e14f400d99e18870f1a97c3703`. GitHub에서 해당 커밋을 받아 부모가 기준 커밋임을 확인했다.
- 제품 diff SHA-256: `c2d62e6936380a0dc14e9283b9194e435e69025b697a37f08917531e219c5e9b`.
- 제품+테스트 diff SHA-256: `e955f146482d53632f074736326948973981f0ea66ab86bbaa037e0f55321a28`.
- 두 diff 해시는 Git 커밋 간 차이로 재계산하여 검증 JSON과 일치했다. 첨부 패치 파일 자체의 SHA도 제품+테스트 해시와 일치했다.
- 격리된 검토 폴더에서 기준 소스에 패치 적용 검사를 통과했다. 변경된 14개 파일은 Windows 작업 사본의 CRLF/LF 차이를 제외하면 해당 커밋과 일치했다.
- Windows HEAD: `a3eaa8b97dc9769ad313ac3fe579d0b1443849a9`. 기존 집필 UI 및 C4·C5·C9 미커밋 변경은 유지했다.

## 2. 구현에서 확인한 내용

| 항목 | 소스 대조 결과 |
|---|---|
| 새 작품 상태 조회 | 실제 sender가 각 전송 준비에서 `get_project_status`를 읽는다. 기본 active나 일반 동기화 fallback이 없다. |
| 작품 식별과 상태 | `SyncV2ContractProjectStatus.decode`가 UUID 필수·요청 ID 일치·알려진 상태를 검사하고 sender가 active만 허용한다. |
| 구조 기준 | 실제 AppEnvironment의 recorder/sender/pull이 coordinator의 동일 authority를 공유한다. 시작 시 메모리 기준은 미확인이다. |
| pull 결과 승인 | 폴더 조회·projection 평가·기준 반영 및 구조 거절/merge/보류 여부를 검사한다. 정상 enqueue의 동기화 표시 변화만으로 기준을 무효화하지 않는다. |
| 작품 삭제·복원 | LocalProjectManager의 실제 삭제 요청·취소·삭제 목록 이동·복원·영구 삭제 함수가 수명 세대를 갱신한다. |
| 송신 직전 검사 | 실제 HTTP 요청 준비 뒤 authorize를 호출한다. gate/auth/binding/handshake/activity/global-sync/local lifecycle/structure proof를 다시 검사한다. |
| 시작 전후 구분 | 시작 전 실패는 transmissionNotStarted로 배치를 보존한다. 시작 후 검증된 응답은 저장하고, 문맥이 바뀌면 새 화면의 완료 표시를 억제한다. |
| 중복 준비 | sender의 작품별 sendingProjects가 상태 조회를 포함한 sendNext 전체를 감싼다. |
| 복구 진단 | Debug에서 제한된 타입의 사건·시각·식별자만 직렬 백그라운드 기록한다. 두 파일 회전 및 기록 실패 흡수 경로가 있다. |

Windows는 이미 비활성/차단으로 알려진 작품의 경우 기존 복원·기준 재조회가 먼저 필요하다. iPad는 로컬 활성 조건 아래 수동 송신의 새 상태 읽기로 서버 복원을 확인할 수 있다. 두 동작은 안전 차단 원칙에 부합하지만 복원 후 재개 순서는 같지 않으므로 공통 문서에 구분해야 한다.

## 3. 남은 확인: 관측 사이의 차단·해소

`WriterPad/Sync/SyncV2ContractStructure.swift:582`의 `observeQueue`는 전달된 snapshot의 blocked/conflict 유무가 이전 값과 달라질 때만 revision을 갱신한다. 최종 `validates`도 이 메모리 revision을 검사한다. 저장소 사건 이력을 직접 읽지 않는다.

반면 Windows의 `sync_v2_store.py:3459`와 `handshake_lifecycle.py:281`은 실제 저장소에 남은 차단/충돌 진입·이탈 이력 지문을 전송 준비 값과 비교한다. 현재 snapshot이 다시 허용 상태여도 과거 준비는 무효다.

iPad에서 확인한 연결 범위는 다음과 같다.

- coordinator의 enqueue 완료, restore, drain/pull 시작·종료는 snapshot을 authority에 전달한다.
- `SyncV2Store.swift:11664`의 실제 `markConflict`, `11734`의 `markBlocked`는 저장소에 위임하며 authority를 직접 갱신하지 않는다.
- `SyncV2Store.swift:11724`의 `resolveConflict`는 저장소 처리 뒤 dispatcher를 깨우며 authority를 직접 갱신하지 않는다.
- dispatcher는 실제 작업 처리 후 drain 종료 시 snapshot을 전달한다(`SyncV2Dispatcher.swift:1316`, `1334`). 개별 작업마다 차단 사건을 전달하는 경로는 아니다.
- `SyncV2HandshakeTests.swift`의 `testC9LateActiveServerReadingCannotClearNewStructureBlock`은 coordinator.restore에 blocked와 idle을 직접 차례로 전달한다. 따라서 **두 상태가 모두 관측된 경우**를 검증한다.

이 때문에 실제 저장소가 허용→차단→해소로 바뀌고 authority에는 해소 뒤 snapshot만 전달되는 경우까지 보호된다고 현재 자료만으로 확정할 수 없다. 특히 상태 조회는 upload permit 획득보다 먼저 시작하므로, 일반 dispatcher가 동작하는 동안 전송 준비가 상태 응답을 기다리는 경계를 포함해 확인해야 한다. UI의 충돌 해결에는 enqueue 등 추가 경로도 있으므로 실제 경로 전체를 통과하는 통합 시험으로 판정해야 한다. 단순한 snapshot 대역 조작만으로 실제 앱의 재현 결과를 주장하지 않는다.

### iPad에 요청하는 보완 또는 증명

1. 실제 저장소와 실제 recorder/dispatcher/coordinator를 연결하고 계약 전송의 상태 응답 또는 최종 transport 준비를 대기시킨다.
2. 저장소의 실제 차단/충돌 진입 및 해소 경로를 통과시킨다. authority.observeQueue/restore를 테스트가 직접 호출해 전이를 대신 전달하지 않는다.
3. 관측 사이에 두 전이가 끝나도 이전 준비의 계약 쓰기 호출은 0회이고 batch/operation ID와 payload는 유지돼야 한다.
4. 새 준비에서는 모든 조건을 재검사하여 같은 배치로 재개해야 한다. 정상 enqueue만으로는 이 조건을 실패시키면 안 된다.
5. 누락이 있으면 저장소 전이 시점에 빠짐없이 세대를 갱신하거나, Windows처럼 내구성 있는 사건 이력을 비교한다. 메모리 사용 자체가 문제는 아니며, 모든 관련 전이를 놓치지 않는 것이 합의 조건이다.
6. 실제 경로에서 누락이 불가능하다면 그것을 보장하는 코드 근거와 위 통합 회귀 시험을 회신한다.

이 항목은 실제 계약 관문을 열지 않고 격리된 저장소와 모의 전송으로 검증할 수 있다.

## 4. 시험과 설치 기록의 의미

- Git 커밋에 포함된 `Docs/evidence/ipad-c9-final-full-test-2026-09-06.json`까지 읽었다. **957개 / 통과 956 / 실패 0 / 건너뜀 1**이며 첨부 검증 JSON과 일치한다. 2026-09-06 10:03:43–10:06:27 KST, iPad 시뮬레이터 결과다.
- Windows에서는 Swift/Xcode 시험을 다시 실행하지 않았다. 패치 적용·소스/diff 해시 검증과 정적 코드 대조를 수행했다. 과거 Windows 전체 회귀 결과는 764개 / 통과 763 / 실패 0 / 건너뜀 1이며 이번 읽기 전용 대조에서 재실행하거나 합산하지 않았다.
- 검증 JSON의 `installed=false`는 검증 종료 시점이다. 후속 설치 JSON은 **10:39:55 KST 설치·실행 성공**을 기록하므로 서로 모순되지 않는다.
- 두 기록의 Debug 바이너리 SHA는 `426fc13fa198fd1a5e71c3ae2969b8953aea6ad80f4341dd1c34936243ff0e88`로 같다. 설치 전후 열린 관문은 0개라고 기록돼 있다.
- 기기의 실제 바이너리나 설치 명령 원본을 Windows에서 다시 확인한 것은 아니다. 설치 후 오프라인 저장→자동 복구→재실행 시험과 recovery JSONL은 이번 첨부에 없다. 과거 30초~2분 지연의 원인은 여전히 미확정이다.
- 새 커밋은 설명 문서 `sync-contract/handshake-envelope.md`도 수정했다. 정규 protocol/lock/digest 변경은 없다. 다만 그 설명에 남은 Windows의 project_id/중복 목록 허용 내용은 최신 C4 보완 전 상태이므로 최신 Windows 결과로 갱신해야 한다.

## 5. 다음 단계

**사용자가 지금 할 일:** 이 문서를 iPad 측에 보내 3절의 실제 저장소 전이 통합 시험 및 필요 시 수정 결과를 요청한다. 함께 이미 설치한 Debug 앱에서 닫힌 관문을 유지한 일반 복구 시험과 recovery JSONL 수집 절차를 안내받는다. 일반 복구 시험은 계약 전송 시험과 분리해 기록한다.

현재 Windows 앱에서 조작하거나 통신을 차단할 필요는 없다. Windows 최신 수정은 아직 커밋·재빌드·설치 전이며 이번 검토에서도 수행하지 않았다.

실제 계약 시험은 남은 전이 검증, 양쪽 검증 버전 고정, 닫힌 관문 복구 확인, 서버의 쓰기 시점 활성 상태/revision/권한·멱등성 보장 확인, 시험용 작품과 복구 계획 확정 후 별도 진행한다. 첫 범위는 기존 합의인 iPad Debug 수동 새 폴더 1개+부모 tree_order 1개 배치를 유지한다. 현재 실제 계약 쓰기 성공 증거는 없다.
