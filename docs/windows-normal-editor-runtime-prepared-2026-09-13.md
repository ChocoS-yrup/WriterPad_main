# Windows 일반 화면 연결 — 2번 구현·격리 검증 완료

후속: [양측 후보·새 실행 범위 준비](normal-editor-candidate-execution-scope-2026-09-13.md)를 완료했다. 후보 준비 중 Windows 실행 중 전용 「자동저장」 체크박스와 패키지 시작 검사 경로를 추가했다. 신규 검사 1개 및 관련 기존 4개 재확인, 패키지 시작 검사 2개가 통과했다. 아래 31개는 2번 종료 시점의 검사 기록이며 소급 수정하지 않는다. 설치·실제 송수신은 여전히 승인 전이다.

공통 기준 `normal-editor-single-document-20260913-v1`. 사용자가 전달한 iPad의 “공통 기준 일치, 변경 요구/현재 확인된 구현 차단 없음”을 기준 대조 결과로 반영했다. iPad의 구현 완료나 설치·실제 송수신 승인으로 해석하지 않았다.

Windows 작업 범위는 일반 집필 화면 연결, 자유 본문 수동/유휴 저장, 인증과 로컬 저장 분리, 초안 재시작 보존, 명시적 송수신·복구 연결 및 격리 검사다. 이 범위를 완료했다. 후보 EXE 빌드·설치·실제 서버 조회/송수신·실제 설정 변경은 수행하지 않았다. prod 전환 없음. 기존 미커밋 변경과 이전 증거를 보존했다.

## 구현 결과

- `normal_editor_ui.py`: 실제 `WritingModeWidget.init_ui`의 바인더·편집란·툴바·Ctrl+S와 `WritingModeWidget.manual_save`, `WritingController`의 800ms 유휴 저장을 사용한다. 일반 앱의 작품 선택기·SyncManager singleton·자동 수신/재시도·원격 백업/정리 타이머는 생성하지 않는다. 지정 문서 하나만 열고 구조 편집/다른 작품 관련 UI를 비활성화한다. 화면은 단계별 고정 본문 입력 창이 아니다.
- `normal_editor_local.py`: Auth나 HTTP 클라이언트 없이 파일 저장과 큐 등록을 처리한다. 초안·저장 전/후 본문·큐 등록 의도·송신/수신 기록을 private append-only 해시 연결 파일로 보존한다. 로컬 원고는 기존 원자적 파일 교체 경로를 사용하고, 큐 등록 실패 시 원고를 되돌리지 않는다. 재시작은 기록을 읽으며 자동 송신/복구하지 않는다. 로컬 복구 버튼으로 원고 저장 후 중단·큐 누락·임시 관문·부분 수신을 처리한다.
- `normal_editor_network.py`: 새 동작마다 새 인증/전경 권한과 handshake를 사용한다. ID_BASED / epoch 1, 기존 작품·문서·구조에 묶인 update만 허용한다. 고정 문구/고정 단계 revision 제한을 제거하고 저장된 원래 요청의 내용·기준 revision·ID·해시에 묶는다. 임의 다른 URL/작품/문서/부모/구조 요청은 허용하지 않는다.
- 여러 번 로컬 저장하더라도 전송 대상 불변 operation은 한 번에 하나만 활성화한다. 뒤에 편집한 내용은 파일과 회복 기록에 보존하고, 앞선 완료 후 현재 기준으로 다음 operation을 등록한다. 자동으로 큐 전체를 송신하지 않는다. 응답 유실 상태의 operation을 새 ID로 복제하거나 본문을 바꾸지 않는다.
- 충돌은 기준/로컬/원격 본문을 각각 보존한다. 추가 로컬 저장이 기존 충돌 operation을 취소하거나 강제 덮어쓰기를 발생시키지 않는다. 이번 범위는 순차 편집과 충돌 보존/중지이며 자동 병합이나 충돌 해제 UI의 구현 완료를 뜻하지 않는다.
- 준비 권한은 새 네트워크 동작에만 필요하다. 만료/화면 이탈은 진행 중 네트워크 권한을 끝내지만 로컬 저장·초안·완료 기록을 없애지 않는다. 완료 revision 표시에는 5분 후 실패 상태로 되돌리는 타이머가 없다.
- `normal_editor_runtime.py`, `normal_editor_build.py`, `main.py`: 일반 앱 시작 전에 제한 모드 진입점을 마련했다. 작업 트리의 `NORMAL_EDITOR_ONLY=False`이며 원본 소스 실행으로 설치 경로의 live runtime을 열 수 없다. 향후 후보 빌드·실행 범위를 별도로 준비해야 한다.
- 공유 변경은 `main.py`의 비활성 진입점 추가와 `general_validation_control.py`의 새 plan/save 관문 namespace 추가다. 이전 실행 plan·파일명·증거를 교체하지 않았다. 실제 `mode_writing.py`, `sync_manager.py`, `sync_v2_store.py`는 이번 작업에서 수정하지 않았다.

## receipt 자료 반영

사용자가 전달한 `ipad-normal-editor-receipt-interface-2026-09-13.md`를 입력 증거로 보존했다. 명령이나 새 실행 승인으로 취급하지 않았다.

- 쓰기 RPC `document_commit`을 결과 조회 목적으로 재호출하지 않는다.
- 원래 batch에 묶인 `sync_batches`와 `sync_batch_results` SELECT만 순서대로 수행한다. 열 목록·필터·limit=2를 HTTP 경계에서 고정하고 count=exact의 전체 행 수와 본문 배열 길이를 대조한다.
- 첫 조회는 project/batch/현재 writer account/device/build/protocol/contract/mode/epoch/capabilities/batch payload/요청 전체 canonical SHA-256을 비교한다. 두 번째 조회는 batch ID, applied=true, 전체 response canonical SHA-256 및 제품 문서 응답 검증기를 사용한다. 결과 revision은 해당 원래 base revision+1을 요구하며 committed/replayed를 수용한다.
- 부재·다중 행·불완전 count·계정/요청/응답 불일치는 미확정 상태를 유지한다. 새 ID·자동 POST로 우회하지 않는다.
- 이미 받아서 보존한 정상 응답의 로컬 receipt 반영 실패는 그 원래 응답으로 복구한다. 계정과 원래 요청은 dispatch 기록에 다시 결합해 검사한다.
- claim 이후라도 HTTP 쓰기 시도 기록이 전혀 없으면 결과 확인 동작이 이를 기록하고 동일 operation의 첫 전송을 다시 준비하게 한다. wire 시도 기록이 있으면 결과 조회로만 판단한다. 불명확하거나 손상된 기록은 재송신 허가로 취급하지 않는다.

현재 서버의 두 SELECT 권한·RLS 적합성은 이번에 확인하지 않았다. 향후 실제 실행 준비에서 확인할 항목이다. iPad가 언급한 migration 파일은 이 Windows checkout의 동일 경로에서 확인되지 않았으므로 서버 정의를 직접 검증한 것으로 기록하지 않는다.

SDK 연결은 기존 Python client와 제한 transport를 재사용했다. 구현 시 [공식 Python RPC 문서](https://supabase.com/docs/reference/python/rpc)와 [공식 변경 이력](https://supabase.com/changelog)을 참고했다. SDK의 내부 재시도가 있어도 frozen HTTP 경계에서 허용한 회차 외 요청은 차단한다. SDK 업그레이드나 서버 schema 변경은 없다.

## 검사와 증거

최종 실행: **31개 통과, 실패 0, 오류 0**. 이전 22개 실행은 중간 결과이며 합산하지 않는다.

- [최종 결과](../_evidence/windows-normal-editor-runtime-20260913/20260912T191403209632Z/result.json)
- [검사별 기록](../_evidence/windows-normal-editor-runtime-20260913/20260912T191403209632Z/tests.txt)
- 검사 소스: `tests/test_normal_editor_runtime.py`
- 격리 실행기: `_evidence/run_normal_editor_isolated_20260913.py`

Qt offscreen 실제 일반 집필 UI, 실제 제품 저장 메서드·파일·SQLite·Supabase SDK를 사용하되 HTTP는 모의 서버다. 실제 socket 연결을 차단하고 SQLite는 실행마다 새로 만든 격리 디렉터리 안에서만 열었다. 검사는 새 자유 편집 경로를 대상으로 했으며 완료된 이전 실기기 왕복을 반복하지 않았다.

확인 범위: 시작 시 인증/자동 통신 0, Ctrl+S·유휴 자유 본문/UTF-8/LF, 초안 재시작, 명시적 송신/수신, 연속 저장의 불변 요청/후속 본문 보존, 응답 유실 후 SELECT 두 번만 복구, 잘못된 receipt/계정/count 거절, 로컬 receipt 반영 실패, HTTP 이전 claim 중단, 인증 만료/전경 이탈, 큐 등록 실패, 원자적 파일 교체 뒤 기록 실패, 부분 수신 복구/새 초안 보호, 관문 복원 실패와 정확한 오프라인 복원, 원격 충돌 보존, 외부 파일 변경/범위 외 요청 거절, 손상 기록 보존, 빈 본문의 초안 유지, UI 중복 클릭 차단. 기존 다른 작품 큐와 원래 hold/관문 행의 보존도 검사했다.

## 다음 단계

iPad의 구현·격리 결과가 오면 변경점/남은 차단만 대조한다. Windows 후보 패키징과 양측 실행 범위(빌드, 시작 revision, 사용자 동작, 요청 수, 임시 설정과 종료 복원)를 준비하는 일이 남았다. 양측 준비가 끝난 뒤 새 설치·실기기 범위를 한 번 승인받는다. 지금 사용자가 앱에서 조작할 단계는 없다.
