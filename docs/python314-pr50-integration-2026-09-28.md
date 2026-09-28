# Python 3.14 보존 및 PR #50 Windows 후속 수정 통합

## 통합 기준과 범위

- 기준: 로컬 `dfb4a92266a65e1f76777d049bb1fd1fab35dfdc`.
- 이 기준은 기존 PR #9의 누적 변경과 원격에 없던 17개 커밋을 포함한다.
- 별도 브랜치: `codex/python314-pr50-integration`.
- 기존 PR #9와 main 기반 PR #10은 수정하지 않는다. 새 통합 draft PR로 검토한다.
- PR #10의 호환성 수정 `fa8ff9b6287316887cacd37cc27532a3310fabc8`을
  현재 기준에 적용했다. 문서 인덱스 조회 최적화, 폴더 identity 적용과 롤백,
  구조 변경 잠금, 진단 이벤트와 닫힌 송신 게이트를 보존했다.
- 원래 작업 폴더의 브랜치와 미추적 파일을 보존한다. 병합·배포·설치·실제
  프로젝트 DB 변경·운영/스테이징 RPC 실행은 이번 검증 범위에 포함되지 않는다.

## 현재 실행 기준

Python은 기존 `.venv314`의 실제 실행 결과인 **3.14.7**을 사용한다.
계약은 기존 전환의 **0.3.0**, storage-name-v2와 고정 Unicode 자산을 유지한다.
구버전 Python이나 `unicodedata2`를 검증·빌드에 사용하지 않는다.

| 항목 | 통합 기준 |
| --- | --- |
| 계약 content commit | `3843b05aa91461e1541f5ebaa14557dc3dc2b39c` |
| Canonical bytes | 24,777 |
| Canonical SHA-256 | `abbd234c7b65d422c2e43d468f4f724e069ede26a3d24be22eb8b35cce8ebf2c` |
| Python / stdlib Unicode | 3.14.7 / 16.0.0 |
| PyQt6 / PyInstaller | 6.11.0 / 6.21.0 |

CI가 참조하던 계약 0.2 checkout과 Python 3.12 검증기를 바로잡았다.
이제 계약 검증, 전체 테스트, EXE 빌드를 같은 Python 3.14.7에서 수행한다.
GitHub Windows runner의 checkout은 자동 CRLF 변환을 끄고 released 계약의
LF 바이트를 보존한다. 0.3 pin 자체와 canonical digest/CR 바이트 검사는 유지한다.
`verify_python314_runtime.py`는 요청한 Python 버전, pip 의존성 검사,
storage-name-v2 벡터 또는 제공된 golden manifest 비교가 실패하면 실패 종료한다.
복구 CMD는 `py -3.14`와 알려진 3.14 설치 경로만 사용하며 임의의 Python 3으로
후퇴하지 않는다. 과거 일회성 v1 golden 생성 스크립트와 검증 문서는 이력이며,
현재 운영·검증 절차는 기존 golden을 재생하는 3.14 검증 스크립트를 사용한다.

## 호환성 수정

동일한 본문 revision에서도 서버가 초기화한 구조 메타데이터 네 필드를
받는다. 같은 프로젝트·문서·본문·경로·삭제 상태를 확인하고, 서버 폴더 증명과
단조 증가하는 구조 revision을 요구한다. 작업 큐·편집 중 내용·로컬 변경은 보호한다.
인덱스 조회에도 project ID와 storage key를 포함하여 본문 재전송 없이 메타데이터
변경을 감지하고 적용한다.

Binder `<root>`는 유일한 살아 있는 서버 `메인` 폴더 ID로 대응한다. 물리적인
NULL parent order와 구분하고, 자식의 부모 ID·경로·프로젝트를 검증한다.
기존 tree_order ID와 revision을 재사용하며 오래된 순서와 잘못된 관계를 거부한다.

## 재검증과 테스트 준비 데이터

아래 전체 테스트·EXE 결과는 오류 우선순위 수정 전 `95252c0`의 이력이다.
후속 변경의 검증 범위는 마지막 절에 따로 기록한다.

테스트는 임시 SQLite, 임시 파일, 모의 HTTP로 실행한다. 테스트가 새 프로젝트의
실제 UUID 대신 임의 UUID를 넘기던 부분, 명시적 송신 게이트·handshake 준비 누락,
구형 Qt 테스트 대역, 이전 schema 번호 기대값을 현재 동작에 맞췄다.
제품 보호 조건을 완화하거나 실패 테스트를 제거하지 않았다.

누락된 `_evidence` 파일에 의존하던 본문 왕복 테스트는
`tests/body_round_trip_fixture.py`가 생성하는 새 합성 폴더·문서 구조를 사용한다.
해시 기대값과 부모 ID만 테스트 범위에서 고정하고 실제 검증 함수를 실행한다.
로컬 evidence의 원고·폴더 자료는 저장소에 복사하지 않는다.
쿠키 회귀는 과거 소스 사본 대신 쿠키 제거가 없는 동작을 모의하여 재현한다.

| 검증 | 결과 |
| --- | --- |
| 전체 unittest discovery | 1,691개, 실패·오류 0, skip 2, 382.218초, exit 0 |
| skip 사유 | Windows 심볼릭 링크 생성 권한 없음(백업 검사·원격 순서 경로 검사 각 1개) |
| 이번 전환 호환성 테스트 | 전체 실행에 34개 포함, 모두 통과 |
| 계약 검증 | schema 7·전환 벡터 12·storage-name-v2 29·atomic wire 4·document wire 7 통과 |
| 고정 Unicode 자산 | canonical digest 4개 및 NFKC 방어 검사 통과 |
| runtime verifier / pip check | 3.14.7 확인, 의존성 오류 0, exit 0 |
| 잘못된 요구 버전 검사 | 실제 3.14.7 실행에 요구 버전 0.0.0을 주면 exit 1 |
| 복구 CLI | 3.14.7에서 `writerpad_recovery.py --help` exit 0; 실제 복구/DB 작업 없음 |
| PyInstaller / Qt smoke | 빌드 성공, `--qt-import-smoke-test` exit 0 |
| EXE runtime report | Python 3.14.7, frozen=true, verdict=ok; AI 공급자 3종 포함 |
| EXE archive | python314.dll 존재, python311.dll 없음; unicodedata2 임포트 없음 |
| CI 스크립트 구문 | PowerShell 다중 행 블록 5개 파싱 및 git diff --check 통과 |

로컬 EXE는 59,018,397 bytes,
SHA-256 `decf387f7e266338947bf5f8ef550cdabf0bbce1c30bbfde594396789eda88b4`다.
실제 클라우드 설정을 복사하지 않은 별도 worktree의 검증용 빌드이며 설치하지 않았다.
개인 golden manifest 992건은 이번에 재생하지 않았으며, 과거 결과를 새 실행 결과로
계산하지 않는다. 기존 3.11 검증 환경은 이번 명령·빌드에서 참조하지 않았다.
GitHub의 게시 커밋 기준 CI 결과는 통합 PR 본문에서 별도로 기록한다.

로컬 검증 명령은 모두 기존 3.14 interpreter의 절대 경로로 실행했다.
CI에서 재현할 명령:

```powershell
python -m pip install -r requirements-stage8.txt -r sync-contract/requirements-validation.txt
python scripts/verify_python314_runtime.py --require-python 3.14.7
python sync-contract/scripts/verify_contract.py
$env:QT_QPA_PLATFORM = 'offscreen'
python -X faulthandler -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean '작가님힘내세요.spec'
```

## 최초 검토에서 발견한 iPad/서버 계약 불일치 (해소 이력)

검토한 서버 PR #50 head는 `3c2c119703e4d711e821d01c6d4a12181804474f`다.
`supabase/migrations/20260927181453_product_sync_transition_initialization.sql`은
당시 45·60·173행에서 `private.storage_name_v1`을 호출하고,
128–129·165·199–200행에서 계약 0.2 해시
`416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`를 요구한다.

따라서 Windows 단위 테스트·EXE 검증 통과만으로 실제 전환/양방향 송신이
호환된다고 판단할 수 없다. 서버/iPad가 0.3 전환 경로와 storage-name-v2,
allowlist·handshake 응답을 일치시킨 뒤 양쪽 동일 계약으로 재검증해야 한다.
Windows를 0.2로 내리거나 송신 게이트를 자동으로 열어 해결하지 않는다.
라이브 allowlist 및 배포 상태는 이번 작업에서 조회·변경하지 않았다.

## 일반 0.3 경로 교차검토와 Windows 오류 우선순위 수정

서버/iPad PR #50 `e44ab0a40a9194163d34e64a8e6bdf3a7225df61`과
Windows PR #11 `95252c08df654563179d88a0afb1d3cea4793eb4`를 다시 대조했다.
서버는 0.3 전환 확장과 target-bound handshake를 지원하며, 기존 v1 dispatcher도
검증된 0.3 문맥에서는 storage-name-v2를 호출한다. iPad의 일반 송수신·대기열·복구도
명시적 0.3 선택을 유지한다. 이전의 0.3 지원 공백은 이 서버 head에서 해소되었다.
계약/서버 CI 36395558767 및 36395559362의 7개 성공과 exact-head 로그를 확인했다.
iPad 시뮬레이터 633개 성공은 상대 측 보고이며 Windows에서 재실행하지 않았다.

교차검토에서 Windows의 오류 우선순위 불일치가 발견되었다. 기존 구현은 입력의
각 문자마다 assigned/exclusion 검사를 함께 수행했다. `U+E000 U+1CCD6` 입력에서
`STORAGE_NAME_UNSUPPORTED_SCALAR`를 반환했지만, 계약과 서버/iPad의 결과는
`STORAGE_NAME_UNASSIGNED`다. 전체 입력의 assigned 검사를 끝낸 뒤 exclusion을
검사하도록 분리했다. 정규화 결과도 전체 separator/control 검사 다음에 방어적
baseline 재검사를 수행하도록 분리하여 계약의 단계 순서를 보존한다.

회귀시험 2개(입력 순서 2경로, 주입한 정규화 결과의 separator/control 8경로)를 추가했다.
수정 전 5개 subtest 실패를 확인했고 수정 후 아래 검증은 모두 Python 3.14.7에서 통과했다.

- storage-name-v2, frozen casefold, 계약 Stage 8, PR #50 호환성: 203개, 실패/오류/skip 0,
  22.654초, exit 0. 공개 이름 벡터 29개와 신규 회귀시험을 포함한다.
- released 계약 검증: schema 7, transition 12, 이름 벡터 29, Unicode digest 4,
  atomic wire 4, document wire 7 및 NFKC 방어 검사 통과.
- runtime verifier: Python 3.14.7, pip check 성공, 이름 벡터 29/29 성공.
- 계약 pin, Unicode 자산, 의존성 버전 및 송신 관문 정책은 변경하지 않았다.

이번 소규모 후속에서는 전체 discovery와 EXE를 로컬에서 다시 실행/빌드하지 않았다.
기존 EXE digest는 수정 전 산출물이며 새 코드의 빌드 증거로 사용하지 않는다.
게시 커밋의 전체 테스트·EXE CI 결과는 PR에서 별도로 확인한다.
병합·배포·실제 작품 전환·실앱/실기기/다기기 E2E는 수행하지 않았다.
