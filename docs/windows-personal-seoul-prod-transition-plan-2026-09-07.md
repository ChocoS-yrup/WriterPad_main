# 개인 사용·서울 Production 전환 준비안

> **진행 상태: 보류.** 최신 사용자 결정에 따라 Staging에서 Windows·iPad 개발과 필요한 검증을 완료한 뒤 이 전환안을 재개한다. Staging 비활성화도 보류하며, 아래 조사 근거는 보존한다.

## 최신 사용자 정정

양쪽 서버의 파일은 모두 테스트용이다. 사용자는 Staging 파일을 삭제하지 않고 추후 서버를 비활성화할 계획이다. 따라서 아래의 전체 작품/계정 UUID 이전 설명은 ‘기존 데이터를 가져오기로 선택할 경우’의 보존 방법이며 기본 필수 작업이 아니다. prod의 계정 구성과 새로 시작할 작품 범위를 먼저 정하고, 기존 테스트 기록은 제자리에 보존한다. 새 계약 구조 쓰기까지 이번에 사용할지는 일반 동기화의 prod 연결과 별도로 결정한다. 이 정정으로 Staging 비활성화·파일 삭제·새 계약 관문 개방을 실행하지 않았다.

## 결정

2026-09-07 사용자 선택: 외부 배포 대신 개인 사용을 유지하고, 해외 Staging에서 한국 리전의 기존 prod로 전환을 준비한다. 이번 3단계는 배포 방향 결정과 사전 조사이며 실제 데이터 이전·접속 전환은 아직 실행하지 않았다.

## 현재 확인된 사실

| 항목 | 현재 Staging | 전환 대상 prod |
|---|---|---|
| 프로젝트 | WriterPad Staging | writerpad-prod |
| ref | mhpnszcorfzrvhyondxr | lrnbklzvxpwnaschrakf |
| 리전 | ap-southeast-1 (싱가포르) | ap-northeast-2 (서울) |
| 관리 API 상태 | ACTIVE_HEALTHY | 사용자 재개 후 ACTIVE_HEALTHY |
| 보고된 PostgreSQL | 17.6.1.155 | 17.6.1.155 |

사용자가 prod를 직접 재활성화했다. 관리 API의 COMING_UP → ACTIVE_HEALTHY 전환 후 양쪽 DB를 읽기 전용으로 조회했다. 과거 기록과 구분해 현재 함수 정의·권한·테이블 컬럼·RLS·정책·트리거와 데이터 집계를 대조했다. 원고 본문·Auth 사용자 행·비밀번호·세션·API 키는 내보내지 않았다.

| 현재 집계 | Staging | prod |
|---|---:|---:|
| Auth 계정 | 1 | 0 |
| 작품(현재 모두 활성) | 11 | 0 |
| 원고(삭제 포함 / 활성) | 651 / 648 | 0 / 0 |
| 폴더(삭제 포함 / 활성) | 195 / 166 | 0 / 0 |
| 원고 버전 / 폴더 버전 | 1086 / 272 | 0 / 0 |
| tree_orders | 1 | 0 |
| Storage 객체 / 버킷 | 0 / 0 | 0 / 0 |
| Edge Functions | 0 | 0 |
| public/private 함수 | 51 | 44 |

이 집계는 조사 시점의 값이며 이후 실제 이전 직전 다시 일관된 시점으로 고정해야 한다. prod에는 현재 계정·작품이 없으므로 기존 계정 UUID를 보존하는 계정 이전과 작품 데이터 이전을 함께 설계한다. 단순 신규 가입은 기존 소유자 UUID 보존 방법으로 간주하지 않는다.

정의 대조 결과:

- 공통 함수의 비교한 실행 권한·security definer·search_path 설정과 공통 테이블 컬럼·ACL·RLS, 정책에는 차이가 없었다.
- prod 누락 함수 8개: storage-name-v2/legacy 관련 5개, ledger mutation 보호 1개, recovery preflight 2개. prod에만 있는 `public.rls_auto_enable()`은 별도 관리 함수이며 삭제 대상으로 잡지 않는다.
- 정의 MD5가 다른 공통 함수 11개 중 5개는 줄바꿈·바깥 공백 정규화 후 같았다. 실제 정의 텍스트가 다른 6개는 `storage_name_v1`, `validate_contract_request`, `begin_project_sync_migration`, `complete_project_sync_migration`, `purge_project`, `validate_project_sync_migration`이다.
- prod에 storage-name-v2 참조 테이블 4개와 해당 트리거가 없고, 원장 테이블 5개의 append-only 트리거 정의도 다르다.
- 이 목록은 적용 SQL이 아니다. 0.3.0 관련 차이와 기존 purge 보완·읽기 진단 차이를 분리해 검토해야 한다. 계약 0.2.0 pin, mode/epoch, allowlist, 관문을 이번 조사에서 변경하지 않았다.
- 제약조건·인덱스·Auth 설정은 아직 전체 대조하지 않았다. API 프로젝트 메타데이터·마이그레이션 목록·카탈로그·집계 근거는 `_evidence/windows-pr-ci-release-20260907/`에 보존했다.

현재 Windows 설치본은 Staging 설정을 포함한다. GitHub CI는 기기별 release 설정 없이 cloud-disabled 패키지를 검증한다. CI 산출물을 서울 prod용 설치본으로 취급하지 않는다.

## Windows에서 분리해야 하는 상태

- `security_manager.py`의 자격 증명 서비스 이름은 프로필로 분리되며, Supabase URL 자체로 분리되지 않는다.
- `runtime_profile.py`는 `ANTIGRAVITY_PROFILE`, `ANTIGRAVITY_APP_DATA_DIR`, `ANTIGRAVITY_ROOT_DIR`, `ANTIGRAVITY_INSTANCE_KEY` 등의 분리 수단을 제공한다. 기본 데이터 경로는 기존 공용 앱 데이터 디렉터리다.
- 따라서 같은 기본 프로필에서 URL만 교체하는 방식은 이번 전환안으로 사용하지 않는다. prod용 로그인 저장소·동기화 DB·작품 경로를 함께 분리하는 실행 구성을 먼저 확인한다. 이는 현재 코드에서 가능한 분리 수단을 확인한 것이며 prod 실행 검증이 끝났다는 뜻은 아니다.
- 기존 Staging의 원본 준비 기록, 두 중단 기록, 성공 회차·이벤트·영수증, 설치본과 원고를 보존한다. 소모된 시험 요청을 prod에 다시 실행하지 않는다.

## 다음 실행 순서

1. **완료:** 사용자가 기존 서울 prod를 재개했고 정상 상태를 확인했다. 앱 접속 대상과 원고는 변경하지 않았다.
2. 양쪽 DB의 테이블·함수 정의·권한·RLS·마이그레이션, Edge Functions, Auth 설정, Storage 사용 여부를 읽기 전용으로 대조한다. 현재 사용자·작품의 UUID와 데이터 규모는 본문을 내보내지 않는 집계로 먼저 확인한다.
3. 대조 결과를 바탕으로 필요한 서버 차이와 이전 대상을 확정한다. 계정 UUID·작품/폴더/원고 UUID·revision·삭제 상태·순서의 관계를 보존할 방법을 정하고 기존 prod 데이터의 덮어쓰기를 전제로 삼지 않는다. DB와 별도로 Storage 객체와 Edge/Auth/Realtime 설정의 이전 필요성을 확인한다.
4. iPad 측과 같은 대상 ref 및 계정·UUID 보존 방식, 두 기기의 접속 전환 시점을 맞춘다. 기존 성공한 계약 시험을 반복 요청하지 않는다.
5. 실제 이전 직전에 양쪽 앱의 저장·동기화 상태를 확인하고 작업을 멈추는 시점을 안내한다. 검증 가능한 백업과 복구 경로를 갖춘 뒤 검토된 이전을 실행한다.
6. prod용으로 분리된 Windows/iPad 구성을 연결하고 이전 데이터 일치와 로그인·일반 동기화만 확인한다. 완료된 고정 계약을 재전송하거나 일반 계약 관문을 상시 개방하는 작업은 포함하지 않는다.

1~3의 준비 결과로 실제 이전 내용을 구체화한 뒤 실행 여부를 결정한다. 서버 재개는 사용자가 수행했으며 에이전트는 읽기 전용으로 확인했다. DB 변경·데이터 복사·새 빌드 설치·기기 전환은 실행하지 않았다. 외부 사용자 배포와 코드 서명 발급은 현재 개인 사용 목표의 다음 작업으로 잡지 않는다.

## 참고한 공식 문서

- [리전 변경](https://supabase.com/docs/guides/troubleshooting/change-project-region-eWJo5Z): 다른 리전 프로젝트로의 이전 방식.
- [프로젝트 백업 복원](https://supabase.com/docs/guides/platform/migrating-within-supabase/dashboard-restore): DB 외 Edge Functions·Auth/API 설정·Realtime 등과 Storage 객체의 별도 이전 필요성.
- [Data API 기본 권한 변경](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically): 새 테이블의 명시적 권한과 RLS를 함께 대조할 것.
- [Realtime 관리 스키마 제한](https://supabase.com/changelog/realtime-schema-locked-down-against-modification): 관리 스키마를 임의로 복원·변경하는 SQL을 전제로 삼지 않을 것.

`changelog.md`는 웹 도구의 content-type 지원 오류가 있어 공식 HTML changelog와 관련 원문으로 확인했다. 현재 작성 범위에서 스키마·Auth·Storage 변경은 없으며, 권한 변경이 필요한 실제 이전안은 별도 검토 대상이다.
