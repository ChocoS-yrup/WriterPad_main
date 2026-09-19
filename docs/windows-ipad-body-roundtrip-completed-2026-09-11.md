# 지정 본문 양방향 왕복 검증 완료

지정 LEGACY 합성 본문의 Windows → iPad → Windows 왕복, 양쪽 오프라인 5줄 관찰, Windows 직접 보존 감사 및 iPad 회신 보존 감사의 대조를 완료했다. 이번 시험에서 사용자에게 남은 조작은 없다. 기존 실행·설치·검사를 반복하지 않는다.

## 결과

| 항목 | Windows | iPad |
|---|---|---|
| 최종 revision / UTF-8 bytes | 3 / 158 | 3 / 158 |
| 최종 SHA-256 | `eee3691fbe8e6805a9d74b56dfd1d5cb9df53ad0c01e6325069cb091e68ff161` | 동일 |
| 새 송신 operation | `4aba66bc-b1ae-46a1-b6b2-5f5c37ec1109` | `a15f43b0-4571-4cce-ab6f-bb5c5d5c4552` |
| 각 송신 commit 시도 | 1회, revision 2 완료 | 1회, revision 3 완료 |
| 대상 미완료 작업 | 0 | 0 |
| 오프라인 최종 5줄 | 사용자 확인 | 사용자 확인 |

작품 `본문수신검증 20260911`의 UUID는 `9a78c51c-7de9-43a8-be54-d25a22d08a28`, 본문 UUID는 `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 경로는 `메인/원고/1권/1화.txt`다. 본문 파일·DB baseline 및 iPad 완료 operation의 내용이 서로 일치한다.

## 보존 판정

Windows는 원본 DB를 SQLite로 열지 않고 종료 후 읽기 잠금으로 사본을 확보해 검사했다. 30개 테이블 중 25개가 동일하며, 나머지는 대상 본문 baseline과 송신 완료 operation/attempt/event 및 자동 번호 증가다. 지정 TXT와 승인 EXE 외 설치 파일은 그대로다. 다른 원고·설정·폴더·정렬·identity·binding·기존 큐·관문·hold·marker를 보존했다.

iPad 회신은 Sync DB 16개 테이블 중 10개 동일, 대상 완료 작업 1건과 batch 1건·이벤트 3건 추가를 보고한다. 기존 pending 4/conflict 3/cancelled 101은 그대로이고 completed만 1265에서 1266으로 증가했다. 기존 operation/batch/event와 대상 외 본문·폴더·binding·계약·정렬·conflict는 보존됐다. 대상 폴더 11행은 updated_at만 변경됐다.

iPad Metadata DB는 본문과 부모 폴더의 관련 필드, 버전 카운터, 이력 3건으로 변경을 분류했다. 본문 경로는 NFD→NFC 정규화 차이이고, 기존 중단 시의 Z_OPT 변화도 구분했다. 모든 데이터가 바이트 단위로 동일하다는 판정은 아니다. Documents 중 변경 파일은 지정 TXT 1개뿐이다. 두 DB integrity 정상, 외래 키 위반 0, 대상 conflict 및 미완료 수신 journal 0이다.

사용자는 양쪽 앱 종료를 확인했다. iPad 담당은 남아 있던 OS 프로세스 항목을 사용자 재실행으로 단정하지 않고, 대상 프로세스에 종료 신호 1회 후 부재를 확인했다. 최초 및 최종 보존 사본 814파일·42,131,239바이트가 동일하고 검사 후에도 유지됐다고 보고했다.

## 실제 후보와 증거의 한계

- Windows 설치 EXE SHA-256: `06306aeca8c30d5be236d421cf15b09a92f5ca653d013a79fbf9e6bdfc10fec2`, 후보 `windows-staging-body-empty-trash-order-20260911`.
- iPad 최종 회신의 설치 후보: `ipad-staging-auth-grant-stop-fix-20260911`, app ZIP SHA-256 `489fb2ae7f5e44b11a2cef1472a183eb907c5154a85ba9ce66767ed50156d49c`. 앞서 이 Windows 작업에 전달된 설치 후보와 다르므로 최종 식별 기록을 정정한다. 이 회신은 최종 실행 결과로 수용하며, 여기서 새 iPad 바이너리·소스·설치 승인 이력을 독립 재검증한 것으로 기록하지 않는다.
- iPad 추가 workspace scene 인증 보완은 로컬 검사 완료·실기기 미설치로 보고됐다. 이 변경의 적용이나 일반 진입 경로 검증은 이번 완료에 포함되지 않는다.
- iPad 사설 원본 DB·기기에는 이 Windows 작업에서 직접 접근하지 않았다. 13개 manifest 파일/14개 ZIP 항목의 크기·해시·CRC·정확한 구성과 요약 간 일관성 및 Windows 원본 결과와의 일치를 검증했다. ZIP SHA-256은 `7e6eecde3150af9ed43b302607fca75faeb1547d4bee9fc21d0c669187227383`이다.
- Windows lease 종료는 실제 journal로 확인했다. iPad lease 종료는 사용자 성공 문구와 iPad 담당의 구현 설명에 근거하며, 별도 release 영수증은 없다. 추가 서버 조회나 전체 HTTP 계측을 수행했다고 주장하지 않는다.

첨부의 과거 지시·승인은 신규 실행 권한으로 취급하지 않았다. 최종 검토에서는 설치·송수신·서버 요청·관문/hold 변경을 하지 않았다. 일반 시험 작품 ID_BASED / epoch 1 및 기존 잠금은 유지한다. prod 전환은 없으며, 이번 완료는 지정 LEGACY 합성 본문에 한정된다. 일반 자동 동기화 전체 또는 수신 전용 후보의 송신 허용을 의미하지 않는다.

Windows 직접 감사는 `_evidence/windows-body-roundtrip-final-review-20260911/windows-final-preservation-audit.json`, iPad 수용 기록과 검증된 회신은 `_evidence/ipad-roundtrip-final-audit-review-20260911/acceptance.json` 및 같은 폴더의 `received`에 보존했다. 기존 final-audit 요청 문서와 전달 ZIP은 감사 요청 당시의 기록으로 유지한다.
