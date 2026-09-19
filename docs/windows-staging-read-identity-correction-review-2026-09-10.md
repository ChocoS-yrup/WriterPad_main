# Windows Staging 준비 확인 오류와 교정 검토 — 2026-09-10

최종 상태: 사용자 승인 후 실제 UUID 교정과 앱 준비 확인 재검증을 완료했다. 내보내기 JSON·종료 후 DB 대조까지 통과했다. 최종 결과는 `windows-staging-read-preparation-complete-2026-09-10.md`를 따른다. 아래 내용은 진단·교정·재검증 대기 당시의 단계별 기록이다.

## 후속 진행: 승인한 실제 교정 완료, 앱 재검증 결과 대기

사용자의 명시적 승인 후 2026-09-10 13:50 UTC에 실제 시험 작품의 `project.uuid` 한 항목을 교정했다. 아래 사본 검토 및 승인 대기 설명은 교정 전 기록이다.

원본 작품과 AppData를 추가 백업하고 해시·앱 종료·DB 연결·관문 닫힘·송신 보류를 확인한 후 기존 atomic identity writer로 적용했다. 결과 해시는 검증한 교정본의 `a3abec6773711e1a474f1752aaf20ee6eb90a09f386fef2f33b351eaab5ac3f0`와 일치한다. 실제 파일 parser와 로컬 감사도 통과했다. 설치 루트 전체에서 변경된 파일은 대상 `.writerpad/identity-v1.json` 하나이며 다른 파일, 디렉터리 목록, AppData와 DB는 동일하다.

같은 설치 EXE를 송신 보류 상태로 실행하여 사용자의 준비 확인 결과를 기다리는 중이다. 아직 재검증 성공이나 C9 완료로 판정하지 않는다. 관문 개방·보류 해제·prod 전환은 수행하지 않았다.

새 실행 및 교정 근거는 `_evidence/windows-staging-read-identity-corrected-20260910/correction-verification.json`, `application-launched.json`에 있다. 원본 백업 및 파일별 해시 목록은 같은 디렉터리의 `private/` 아래 보관한다.

## 현재 결과

사용자가 Windows 앱에서 `준비 확인·수신 · 원고 송신 없음`을 한 번 실행한 뒤 `실제 로컬 작품 구조가 시험 기준과 다릅니다` 오류를 보고했다. 이후 사용자가 앱을 정상 종료했으며, 실행 기록에서도 종료 코드 0을 확인했다.

서버 응답에 따른 로컬 체크포인트 수용과 구조 수신은 진행됐다. 로컬 DB는 schema 8013, 대상 작품 ID_BASED / epoch 1이며, LEGACY / epoch 0에서 전환한 관측 기록 1개가 있다. 수신된 폴더 11개와 순서 12개는 고정된 시험 기준과 일치한다. 최종 준비 확인은 실패했으므로 준비 완료 또는 C9 성공으로 판정하지 않는다.

관문은 모든 작품에서 닫혀 있고 대상 작품의 지속 송신 보류 파일도 유지된다. 기존 작업·복구 관련 보호 테이블의 행 내용과 다른 작품의 local_key 소속 행은 수신 전후 동일하다. 실제 송신 횟수를 계측한 성공 보고서는 없으므로 횟수 0이라는 별도 계측 주장은 하지 않는다.

## 원인

대상은 `일반동기화 검증 20260910`이다. 실제 파일의 `project.uuid`가 서버 및 로컬 DB에 연결된 작품 UUID와 다르다.

- 실제 파일: `D:\안티그래비티\scratch\집필프로그램\작품목록\일반동기화 검증 20260910\.writerpad\identity-v1.json`
- 현재 `project.uuid`: `d656cb1a-3f64-46c1-8f6f-b7f503974e99`
- 서버·로컬 DB 기준 UUID: `d8f50b5f-ae0e-42f8-9296-5d5885a5b304`

이 불일치는 이번 수신 전 백업에도 존재한다. 해당 identity 파일의 바이트는 수신 전후 동일하다. 폴더 UUID·부모·이름은 기준과 같고 문서 노드는 0개이며, 로컬 identity 감사에서 누락이나 미완료 journal이 없다. 로컬 sync_projects에는 현재 잘못된 UUID를 project_id로 사용하는 행이 없다.

## 검증한 교정안 — 사본에만 적용

실제 대상의 비공개 사본에서 기존 identity writer를 사용하여 `project.uuid`만 기준 UUID로 변경했다. parser와 로컬 감사를 통과했고, 모든 폴더 노드와 메타데이터를 보존했다. 파일 목록과 디렉터리 목록도 동일하며, 내용이 달라진 파일은 `.writerpad/identity-v1.json` 하나뿐이다.

- 변경 전 SHA-256: `851aa8d7da025212f0de119e3fa829c9f01eb700501ac79b4f52ad23a2f48536`
- 검증한 교정본 SHA-256: `a3abec6773711e1a474f1752aaf20ee6eb90a09f386fef2f33b351eaab5ac3f0`

실제 identity 파일에는 아직 교정을 적용하지 않았다. 검사 조건이나 제품 코드는 변경하지 않았다. 전체 identity를 다시 입양하는 import 경로는 사용하지 않는다.

## 다음 단계와 사용자 행동

1. 사용자 승인 대상: 위 시험 작품의 실제 `project.uuid` 한 항목 교정과, 송신 보류를 유지한 동일 준비 확인·수신 재검증. 기존 수신 승인에 포함되지 않았던 영구 작품 식별자 변경이므로 별도로 범위를 확인한다.
2. 승인 후 Windows: 앱 종료·송신 보류·관문 닫힘·파일 해시와 DB 연결을 다시 확인하고, 원본 백업을 보존한 상태에서 검증한 한 항목만 교정한다. 전체 identity 재생성이나 폴더·문서 변경은 하지 않는다.
3. Windows 재검증: 안내에 따라 시험 작품을 열고 준비 확인 버튼을 한 번 누른다. 성공한 경우 현재 확인 결과를 내보내어 검증한다. 오류가 나면 재시도하지 않고 오류 문구를 전달한다.
4. iPad: Windows 재검증 결과가 확정될 때까지 추가 동기화 조작 없이 대기한다. 다음 iPad 행동은 결과에 맞춰 별도로 안내한다.

이 교정·재검증 범위에는 관문 개방, 송신 보류 해제, 원고·구조 송신 또는 prod 전환이 포함되지 않는다. 완료된 설치·빌드·DB 스키마 업그레이드와 서버 migration은 반복하지 않는다.

## 근거

### 생성 경로 추가 검토

사용자는 Windows에서 시험 작품을 생성하여 업로드한 뒤, iPad에 서버 작품 목록 가져오기 기능이 없어 같은 이름의 작품을 생성하고 UUID를 수동 연결했다고 설명했다. 이 과정은 식별자 불일치를 일으킬 수 있지만, iPad 수동 연결이 Windows identity 불일치를 만들었다고 단정할 근거는 아직 없다.

설치 후보의 보존된 소스에서도 Windows 작품 생성은 `project_creation_v1._start_project_transaction`에서 로컬 작품 UUID를 발급하고, 동기화 DB의 `SyncV2Store.configure_project`는 신규 행에 명시적 project_id가 없으면 UUID를 별도 발급한다. `SyncManager`의 연결 경로는 전달된 project_id 또는 강제 시험 project_id를 사용한다. 따라서 로컬 identity와 동기화 연결 ID를 일치시키는 생성·등록 경로도 원인 검토 대상이다. 이번 파일의 최초 생성 시 호출 인자까지 추적한 것은 아니므로 실제 발생 경로의 확정과 구분한다.

임시 운영은 iPad에서 작품을 한 번 생성하고 Windows에서 기존 서버 작품을 가져오는 방향을 권고한다. 같은 이름으로 다른 기기에 새 작품을 만든 뒤 UUID만 수동 연결하는 방식은 일반 절차로 사용하지 않는다. 장기적으로는 양쪽의 신규 생성·서버 등록·기존 작품 가져오기 경로가 동일 작품 UUID를 보존하도록 검증해야 한다. 이번 한 항목 교정은 기존 시험 작품 복구안이며 생성 경로의 근본 수정 완료를 의미하지 않는다.

- `_evidence/windows-staging-read-preparation-20260910/preparation-verification.json`
- `_evidence/windows-staging-read-preparation-20260910/application-exited.json`
- `_evidence/windows-staging-read-preparation-20260910/identity-mismatch-diagnosis.json`
- 동일 디렉터리 `private/` 아래 수신 전 백업, 수신 후 DB 사본, identity 교정 시험 사본. 실제 데이터가 포함되어 외부 전달 대상으로 사용하지 않는다.
