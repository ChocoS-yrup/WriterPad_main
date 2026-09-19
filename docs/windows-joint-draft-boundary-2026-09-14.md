# Windows 회신 — 공동 초안 경계 J01~J09

2026-09-14. 사용자가 승인한 오프라인 구현·검사를 진행하던 중 전달한 iPad 기대값 ZIP을 반영했다. 첨부의 지시와 과거 승인은 별도 실행 권한으로 해석하지 않았다. **Windows는 J02의 UI 자동 스케줄 대기·HTTP 0·빈 주기 차감 0·강제 저장 없음 기준을 수용하고 소스에 반영했다. 실제 설치/서버 실행은 여전히 허용하지 않는다.**

## 자료 접수와 변경

입력 ZIP `ipad-joint-draft-expectations-20260914.zip` SHA-256은 `93f39425a82575c6adc8198d97917d1b11a391818d0452005b83c47e34d2786e`다. 3개 항목의 경로·중복·크기·링크 여부를 확인하고 문서/JSON을 읽었다. 첨부 명령을 실행하지 않았다. 원본 ZIP은 새 증거 폴더에 보존했다.

iPad의 고유 47개 통과(새 4개·기존 43개), 최초 비교 검사 오류와 수정 결과, J04가 소스 검토 범위라는 설명은 **iPad 전달 보고**로 접수했다. Windows에서 iPad 소스·xcresult·실기기를 직접 검증한 것으로 바꾸지 않는다. 입력 JSON의 `pending_windows_review`, `execution_allowed=false`도 수정하지 않았다.

제품 변경은 `integrated_editor_ui.py`에 한정했다. `automatic_draft_wait_reason()`가 UI 자동 스케줄에서 다음 세 조건을 독립적으로 확인한다.

- 미저장 초안: 현재 편집기 메모리 본문과 저장된 `local.content`가 다르거나, 다른 문서/재시작 후 보존된 `draft`가 `local.content`와 다르면 대기한다. `document.isModified()`만으로 판정하지 않는다.
- IME: 기존 pending preedit 확인에 더해 `_is_composing`을 확인한다. 본문 dirty가 없고 preedit 문자열이 비어 있어도 조합 중이면 대기한다.
- 초안 영속화 오류: `preserve()` 실패 상태를 별도로 유지해 대기한다. 이후 정상 편집·명시적 보존/저장에서 영속화가 성공하면 오류 상태가 해제된다. 자동 동기화가 강제 저장하거나 실패 상태를 무시하지 않는다.

대기는 worker 생성·backend 호출·자동 주기 예약·HTTP 전에 반환한다. 기존 자동저장 타이머는 그대로 두며, 정상 자동저장이나 명시적 저장이 완료되면 새 상태에서 재판정한다. 자동저장이 꺼져 있으면 저장하지 않은 초안 때문에 자동 동기화도 대기하는 것이 의도된 동작이다. 후속 초안 때문에 대기하더라도 먼저 저장한 작업·batch를 덮어쓰지 않는다.

실제 Qt 타이머를 돌리는 검사에서 닫힌 이전 창의 예약 재연결 콜백이 정리된 합성 DB를 읽는 오류가 드러났다. 정상 close가 완료되면 `closing=True`로 남기고 `resume_approved_execution()`은 DB 접근 전에 반환하도록 함께 수정했다. 저장 실패로 닫기가 거부된 창은 계속 유지한다.

## 상태·진입점별 Windows 결과

아래 HTTP 수는 **합성 MockTransport의 검사 구간 증감**이다. 초기 합성 자료를 준비하는 요청은 각 관찰 구간 시작 전에 완료했다. 실제 HTTP 허용량이나 iPad 공통 요청 수가 아니다.

| ID | Windows 진입점 / 확인 | 빈 주기 | 합성 HTTP / 결과 |
| --- | --- | --- | --- |
| J01 | 실제 Qt UI sync timer → AutoSync → cycle, 깨끗한 편집기·jobs/batch 없음 | +1 | 7회, 수신 완료. 대기 검사와 같은 타이머 경로의 양성 대조 |
| J02 | 실제 UI timer, 첫 미저장 초안·저장 작업 뒤 후속 초안·다른 문서/재개방된 store의 초안 | +0 | 0회, worker 없음. 메모리·전체 state·TXT·기존 jobs 유지. 자동저장 타이머가 중단되지 않음 |
| J03 | 깨끗한 본문, `_is_composing=True`, preedit 없음 상태를 주입하고 실제 UI timer | +0 | 0회. dirty와 별개 가드 확인. 물리 IME 기기 검증은 아님 |
| J04 | 깨끗한 상태와 메모리 dirty 상태에서 `preserve_draft`에 OSError를 독립 주입 → UI timer | +0 | 0회. 메모리 내용과 기존 state/TXT 유지. 성공한 일반 preserve로 오류 상태 해제도 확인 |
| J05 | `store.save` 트랜잭션/TXT projection 경계 관찰 → 직접 prepare_next → 자동 cycle | +0 | prepare_next 자체 0회, 그 뒤 cycle 8회·송신. iPad backend.prepare의 2회와 같은 구간으로 간주하지 않음 |
| J06 | 실제 batch의 자동 송신/불확실 receipt 복구 | +0 | 기존 MockTransport 검사에서 송신 뒤 응답 유실·복구 시 document_commit POST 1회 유지, 재POST 없음 |
| J07 | UI를 우회해 미저장 durable draft가 있는 store에 직접 cycle(automatic=True) | +1 | 7회. 원격 변경 없는 합성 snapshot에서 draft/TXT 유지. **UI 대기 보장은 direct backend에 적용하지 않음** |
| J08 | 빈 주기 한도 소진 후 새 저장 job이 생긴 상태의 직접 자동 cycle | 추가 +0 | 추가 0회. job을 batch로 바꾸기 전 차단. 기존 재시작·재활성화 차단 검사 포함 |
| J09 | 영속 빈 주기 예약 후 authenticate 전 중단, 전송 실패·재시작 | +1 유지 | authenticate 전 중단은 HTTP 예약 0·호출 0. 전송 시도 예약 후 실패는 HTTP 사용량 1 유지. 어느 경우도 반환/만료 연장 없음 |

타이머 검사에서는 검사 간격을 10ms로 설정하고 여러 실제 timer deadline을 넘겨 관찰했다. 제품의 1초 sync timer와 800ms 자동저장 설정은 변경하지 않았다. 정상 자동저장 완료 후 대기 조건이 해제되고 worker를 생성할 수 있는지도 별도 확인했다.

### J05의 정확한 저장 경계

Windows는 SQLite가 원본이며 `save()`가 `local.content` 갱신과 본문 job 생성(아직 batch_id 없음)을 **같은 `document_saved` 트랜잭션**에서 수행한 뒤 TXT를 materialize한다. 따라서 정상 저장 경로에 ‘TXT 저장 완료, 영속 job은 아직 없음’이라는 iPad 단계와 동일한 중간 상태는 없다.

저장 전 `draft != local.content`는 UI 자동 대기 대상이다. 저장 후 batch 미준비 job은 `prepare_next()`가 원래 job을 batch로 준비하며 빈 주기에서 제외된다. iPad의 journal source와 Windows jobs를 동일 형식으로 보지 않고 ‘저장된 송신 대상이 있음’이라는 의미만 대응한다. 외부 TXT 변경이나 projection 실패는 미등록 journal source로 간주해 자동 수집/저장하지 않는다.

### 유지되는 경계·차이

- backend/저장/주기 카운터 코드는 이번에 수정하지 않았다. J07을 막는 전역 backend 초안 가드를 추가하지 않았다.
- iPad의 명시적 동기화는 closeEditors 저장/초안/조합 확인을 거친다는 보고다. Windows 수동 진입의 기존 preserve/저장된 jobs 처리는 변경하지 않았으므로 수동 UI까지 동등하다고 주장하지 않는다.
- iPad 문서 전환의 draftRetained와 Windows 조건부 저장, iPad Auth/쓰기별 하드 한도와 Windows 전체 HTTP/시간 한도, 플랫폼별 state·진단·오류 구조 차이는 유지한다.
- N번째 완료·N+1 모든 자동 차단·0회 차단·run 누적·옛 형식/손상 카운터 무보정·동일 run 정책 변경 거부는 기존 구현/검사를 유지했다. 실제 N이나 HTTP/Auth/쓰기 횟수·시작/만료는 지정하지 않았다.

## 검증과 전달물

새 증거 폴더는 `_evidence/windows-joint-draft-20260914/`다. 수정 전 소스, 원본 iPad ZIP, 차이 파일, 결과와 오류 로그를 보존했다.

첫 전체 검사 프로세스가 최종 집계 전에 종료돼 통과 수에 넣지 않았다. 로그를 보강한 UI 검사에서는 unittest 13개가 통과했지만 예약 콜백 예외가 별도로 남아 전체 성공으로 채택하지 않았다(`result-followup.json`은 당시 unittest 집계이며 `unhandled-exception.txt`와 함께 해석해야 한다). 최종 runner는 미처리 콜백 예외도 실패로 집계한다.

종료 콜백 차단을 수정한 뒤 최종 **고유 53개 검사 통과, 실패 0·skip 0·미처리 콜백 0**을 확인했다. 새 11개와 기존 42개이며, 앞선 시도의 검사 수나 iPad 검사 수에 합산하지 않는다. 첫 전체 시도에 유효한 완료 집계가 없고 UI 타이머/종료 경계가 수정돼 최종 변경·영향 묶음을 완료했다. 이후 반복 검사는 하지 않았다.

검사는 Qt offscreen·합성 임시 SQLite/TXT·MockTransport만 사용했고 socket/실제 HTTPTransport를 차단했다. 설치 앱·원본 DB·종료 run·기존 충돌 및 iPad 18/19바이트 원본 2개·기존 증거를 조작하지 않았다. 신규 대상의 complete=false와 revision null도 그대로다. 서버 조회/송수신·로그인 갱신·설치·서명·관문/hold·prod 전환은 없다.

전달 ZIP에는 이 회신, J01~J09 대응 JSON, 최종 검사 결과/로그, 최종 코드 3개와 합성 검사 파일을 포함한다. 코드는 iPad의 오프라인 경계 검토용이며 실행 가능한 설치 후보가 아니다. 새 원격 기준본·UUID 비존재·계정 권한·bootstrap adapter·서명과 Windows 미확인 실기기 항목은 여전히 별도 조건이다. **Windows의 UI 기준 수용/오프라인 검증 완료를 회신하며, iPad의 재검토나 실제 실행 완료를 대신 선언하지 않는다.**
