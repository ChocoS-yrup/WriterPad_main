# Windows 추가 1회 경로 설치·비송신 준비 완료

2026-09-07 KST. 사용자 ‘진행’ 승인 범위인 빌드·설치·새 회차 없는 최신 준비 점검을 완료했다. **실제 추가 회차 생성·쓰기 송신·관문 개방은 아직 하지 않았다.** 설치 보고서만으로 iPad 재승인을 기다리지 않는다. 실제 실행은 아래 범위의 새 사용자 승인을 받은 뒤 진행한다.

## 설치와 보존

- iPad 회신 ZIP SHA256: `638051faa8f1e02288db258bfbdaba767a771cea596e91128eaea10026ff5292`. 회신 manifest 4개, 검토 소스 8개와 증분 패치 및 기존 공통 경계 소스가 일치한다. 제품 소스 추가 변경과 완료한 140개 검사 반복은 없다.
- 새 EXE: `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`, **79,315,384 bytes**, SHA256 **`7b9c0eacc75da121afe02ddbe8784b9dc09cc37a6c1175ee4024b2d4d5b0fc73`**.
- 16:45:22 KST 설치 기록. 사용자 종료 확인 후 기존 EXE `3d7b0daf780a3aa63d9cf06bc135f60f54c2830a7b48ace96c2bf0777dc365dd`를 해당 설치 evidence의 `previous-installed.exe`로 보관했다.
- 패키지의 핵심 모듈 10개(신규 `contract_post_coordination_resume` 포함)를 검토 소스 컴파일 결과와 재귀 비교했다. 파일명만 정규화한 코드가 일치했다. 빌드본과 설치본의 프로젝트·인증 초기화 전 Qt 시작 검사 모두 종료 코드 0.
- 설치 후 정상 실행 프로세스의 경로와 시작 시각을 설치 기록 및 파일 해시에 연결했다. 실행 메모리 전체를 검증한 것은 아니다.
- 설치 직후 DB는 8011 그대로였다. 이후 **사용자의 정상 앱 실행으로 8012**가 됐다. 운영 DB 수동 이행은 하지 않았다. 신규 회차·사건·영수증 테이블은 **각각 0개**.
- 원본 준비·최초 stopped/HTTP 0·부모 stopped/HTTP 0 및 부모 사건 4개·영수증 0개를 보존했다. 전체 선별 상태의 변경은 schema_version뿐이다. 관문 열림 **0/15**, 기존 큐 ID·사건과 진단 3개도 보존했다.

보존 해시: 원본 준비 `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4`, 최초 실행 `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4`, 부모 전체 행 `176d27df155a477621a97b91aa7d1eea212ef79fdb72327c0ef403e255b3bd4e`, 부모 사건 `72e27e685ec476dd5eea79ed3b456161615e85202460332f94f03d42d42fefdb`.

## 이번 실제 조회

- 첫 준비 조회(16:46:08)는 자동 수신 중이어서 authority_allowed와 pull_idle만 false였다. 이 관찰은 `readiness-during-pull.json`으로 보존했다. 회차를 소모하지 않았고 송신을 시도하지 않았다.
- 수신 완료 후 16:48:32 재조회는 **11개 조건 모두 true**, stale=false, gate_open=false, authority=contract/accepted, pull/worker/pending 없음이다. 새 정책 local_candidate=true, 보존 이력 일치, round=null, approval_recorded=false다. 실제 파일명은 앱 기본값 `windows-contract-readiness.json`이며 별도 사본으로 보존했다.
- 16:46:27의 설치 앱 현재 로그인 원장 RPC는 account_marker `bbca16c03e5b9711`, authorized=true, complete=true, 새 nonce `530707db-64bf-42c7-9b20-481c87ca70ec`, 네 count 모두 0이다. 오류와 stale 없음, 새 회차 없음. 검증 당시 증명 시각 허용 범위도 충족했다.
- **저장된 조회를 실행 직전의 새 증명으로 재사용하지 않는다.** 실제 sender는 현재 인증·수신 상태·로컬/서버 기준과 새 nonce 원장을 다시 확인한다.
- 16:48:46 별도 최신 관리자 SELECT는 서버 active, 계정 표식과 소유자 일치, 활성 계약 0.2.0/protocol 3/digest 일치, LEGACY/epoch 0를 확인했다. 설정 행 부재의 LEGACY/0 의미는 이번에 읽은 배포 handshake 함수 정의로 확인했다. 서버 원장 네 범주도 0개다. 이 관리자 조회는 앱의 현재 로그인 증명을 대신하지 않는다.
- 16:50:01 읽기 전용 로컬 지문 재구성 결과 **`a26eed18ecb701308b900d26e66d12d3715fbe550f637bd9ea3d3cdc0271e98d`**가 원본과 일치했다. 폴더 메타데이터 12개·문서 메타데이터 26개·원고 파일 25개, 빈 2권, `[1권,2권]`, 순서 revision 1, 새 UUID/3권 이름 부재 확인. 서버 세 구조 테이블의 모든 선택 메타데이터가 로컬과 정확히 일치했다.
- 실제 SQLite는 immutable 읽기 전용, WAL 부재 및 조회 중 크기/수정 시각 불변을 확인했다. 원고 본문·토큰·전체 DB를 내보내지 않았다. 모든 관찰은 기록된 시각의 결과다.

## 사용자에게 제시할 실제 실행 범위

양쪽 최종검증03에서 편집·구조 변경을 중지한 상태를 확인하고 다음 범위에 새 승인을 받는다.

- Staging `mhpnszcorfzrvhyondxr`, 작품 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`, 위 새 설치본만 대상.
- 정책 `post_coordination_once_v1`, 부모 `http0-074b0dc9-007c-4f0e-8516-a84804850e77`를 보존하는 **추가 로컬 회차 최대 1개·쓰기 HTTP 최대 1회**.
- 기존 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`와 원본 payload를 그대로 사용한다.
- operation `7998be14-c48d-49e0-84b0-08030070d312`: 원고 `14df4a55-b7cd-4790-bc53-f84148418c0f` 아래 빈 3권 UUID `77627d68-8388-4d6c-9363-ba6537aac1f0`, create/base 0 → 성공 revision 1.
- operation `f55e8fab-ea38-468e-a4fe-fbe2cf9ad127`: order `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`, reorder/base 1 → 성공 revision 2, children `[c0b43fc4-89a0-4a5a-b140-768a62cfde5a,4711b2ce-5de9-44ba-80fe-570515839549,77627d68-8388-4d6c-9363-ba6537aac1f0]`.
- 직전 검사 통과 때만 Windows 해당 작품 관문을 일시 사용하고 종료 후 닫는다. iPad 관문은 계속 닫는다.
- 결과 원장·영수증·두 구조 행 읽기 및 양쪽 일반 수신 확인을 포함한다. 응답 유실이나 처리 중 상태에는 결과 확정을 위한 추가 읽기만 한다. iPad 측 실제 수신 확인을 위해 실행 결과 ZIP을 사용자에게 전달한다.
- 재전송·ID 재발급·추가 회차의 추가 생성·수동 완료·삭제 정리·25화 생성·다른 구조 변경·서버 설정 또는 운영 환경 변경은 제외한다. claim 뒤 중단은 HTTP 0이어도 이번 기회를 소모하므로 버튼을 반복해서 누르지 않는다.

현재 실행 승인은 없다. 사용자 승인 후에만 전용 버튼을 안내한다. 설치 보고서만을 위한 iPad 왕복은 하지 않는다.
