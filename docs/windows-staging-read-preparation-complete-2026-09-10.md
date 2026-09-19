# Windows UUID 교정 후 준비 확인 완료 — 2026-09-10

승인받은 시험 작품의 `project.uuid` 한 항목 교정과 송신 보류 상태의 준비 확인 재검증을 완료했다. 사용자 화면 보고, 설치 앱이 내보낸 JSON, 앱 정상 종료 후 로컬 DB 사본을 대조했다.

## 검증 결과

- 작품: `일반동기화 검증 20260910` / `d8f50b5f-ae0e-42f8-9296-5d5885a5b304`
- 로컬 schema 8013, ID_BASED / epoch 1, contract 0.2.0 / protocol 3.
- 폴더 11개·정렬표 12개가 고정 시험 기준과 일치한다. 로컬 identity UUID와 서버·DB 작품 UUID도 일치한다.
- 내보내기 시각 2026-09-10 22:52:18 KST에 기준 구조·새 handshake·C9 전체 조건을 통과했으며 미완료 작업 0건이다. 이는 해당 관측 시점의 결과이며 다음 실행의 준비 상태를 미리 보장하는 판정은 아니다.
- 실제 설치 앱 PID 5364 / frozen 실행과 설치 EXE SHA-256을 별도로 대조했다. 앱 보고서의 installed_build_verified null 필드를 임의로 바꾸지 않았다.
- 앱은 정상 종료 코드 0. 종료 후 모든 작품의 관문이 닫혀 있고 대상의 지속 송신 보류도 유지된다.
- 재검증 중 작품 파일 내용은 동일하다. 다른 작품의 DB 행과 기존 작업·복구 기록도 동일하다. 체크포인트 관측 기록은 1개를 유지하며 전환을 반복하지 않았다.

내보내기는 안내 경로 대신 설치 루트의 기본 이름 `windows-general-test-gate-observation.json`으로 저장됐다. 원본을 보존하고 검증 폴더에 사본을 만들었으며 두 파일의 SHA-256 일치를 확인했다.

## 완료 범위와 다음 행동

이번 단계는 교정 및 준비 확인·수신 검증까지다. 관문 개방, 송신 보류 해제, 실제 원고·구조 송신 시험, prod 전환은 수행하지 않았다. HTTP 송신 횟수를 측정한 검증은 아니므로 0회 계측 결과로 표현하지 않는다.

Windows는 종료 상태를 유지하고 iPad도 추가 조작 없이 대기하면 된다. 후속 교차 검증에는 이 완료 결과를 사용한다. 실제 송신 시험 단계는 시험 범위와 양쪽 상태를 확인한 뒤 별도 승인 범위로 다룬다. 기존 설치·빌드·DB 업그레이드 및 이번 준비 확인을 불필요하게 반복하지 않는다.

별도 남은 개발 검토는 Windows 신규 작품 생성·동기화 등록 시 동일 UUID 보장과 iPad 서버 작품 목록·가져오기 흐름이다. 이번 단일 작품 교정으로 그 생성 경로까지 수정 완료된 것은 아니다. 그 전까지 신규 작품은 iPad에서 한 번 생성하고 Windows에서 기존 서버 작품을 가져오는 운영 방향을 권고한다.

## 근거 파일

- `_evidence/windows-staging-read-identity-corrected-20260910/correction-verification.json`
- `_evidence/windows-staging-read-identity-corrected-20260910/windows-general-test-staging-read-observation.json`
- `_evidence/windows-staging-read-identity-corrected-20260910/application-exited.json`
- `_evidence/windows-staging-read-identity-corrected-20260910/final-verification.json`
- 위 디렉터리의 비공개 `private/`에는 원본 백업과 전후 DB·파일 해시를 보관한다. 외부 전달에 포함하지 않는다.

앱 내보내기 SHA-256: `4f7fe8f1eeeb6821cc081ac5ba441c1bf30e49e3bcac03b3f1c29230884e5e93`

설치 EXE SHA-256: `def63f0086e0a0e3ce5cc9530d580d339a1128835afcf961964a616cea1df070`
