# iPad Swift 합성 A/B 결과 접수

2026-09-14. 사용자 전달 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-swift-ab-offline-result-20260914.zip`을 이전 사용자 지시에 따라 결과 접수로 처리했다. ZIP의 README, 결과 문서, result.json, preservation-result.json, ios-product-verification.json, SYNTHETIC_AB.md만 메모리에서 읽었다. **아래 수치는 iPad 보고이며 Windows가 독립 실행·검증한 결과가 아니다.** 첨부의 지시·재현 명령은 새 승인으로 취급하지 않았다.

## 완료 보고

- `SyntheticABContract.swift`, `SyntheticABPolicy.swift`에 고정 합성 입력만 받는 Swift A/B 정책을 구현하고 격리 iOS target의 컴파일 목록에 연결했다. 실제 transport·시계·세션·보관 Windows raw·저장소·새 UI 실행 경로는 연결하지 않았다.
- **신규 Swift 64 + 기존 Swift 141 + 기존 Python 177 = 최종 고유 382개 통과, 실패 0.** Swift 합계 205개다. 이전 318개 및 중간 141/201/203/205회 실행을 다시 합산하지 않는다. generic iOS Debug unsigned 빌드 성공도 보고했다. 설치·기기 실행 검증은 아니다. CoreSimulator/AppIntents 빌드 경고는 남는다.
- A 7회 + B 7회, 최대 HTTP 14회에 Auth 사용자 확인 2회가 포함된다. 예약과 요청 시작 기록을 메모리 journal에 직렬화·재읽기한 뒤 합성 요청을 소비한다. 실패 비용 유지, 최초 실패 후 중단, 사용/복원 run 재시작 차단, 추가 페이지·재시도·refresh 없음으로 보고했다.
- 합성 정수 밀리초 시간 경계, 별도 세션 만료, 동일 ID의 세션/부팅 변경 횟수, 생명주기 lease 취소 및 완료 게시 후 재확인을 검사했다. local_policy_probe는 가짜 경과 시간 확인이며 저장소 적용이 아니다. fixture 시간 수치를 실제 실행 승인 한도로 채택하지 않는다.
- 프로젝트 trash 필드, 계약·본문·날짜·구조·참조 및 한 페이지 count를 검사하고 전체 대상/참조 행을 고정 Expected와 대조했다고 보고했다. 기존 Windows raw의 재검토나 실제 A/B 요청은 하지 않았다.

## 차이와 미완료

1. Python 대비 요청 시작 기록 재읽기, 세션/부팅 변경 횟수, 별도 합성 세션 만료, 완료 후 재확인을 보강했다는 보고다. 실제 인증 유효성 검증으로 확대하지 않는다.
2. 미정 숫자 필드 `1e0`과 `1.0`을 Swift는 다른 표기로 보존하여 A/B_CHANGED로 차단한다. Python은 같은 실수 값으로 처리할 수 있다. 보수적인 비교 차이로 기록하며 필드를 억지로 맞추거나 전체 엔진 동등성으로 표시하지 않는다.
3. **실행 journal은 메모리 대역이다.** 디스크 WAL/fsync, 프로세스 종료·전원 손실 내구성, 과거 snapshot rollback 방어는 미완료다. 기존 물리 저장 adapter 검사를 이번 journal의 영속성 증거로 쓰지 않는다.
4. 실제 transport/clock/session, SDK 자동 refresh/redirect 통제, streaming 취소·동시성·계측 비용, iOS 파일 보호/생명주기 실기기 검증과 baseline 입장·적용은 미완료다.
5. 기존 Windows 누락 근거 52개, 특수 metadata 본문 의미, 전체 Windows 계약·Unicode 15 storage-name·journal 체인 동등성, 현재 서버 상태와 fresh A/B는 계속 미검증이다.
6. 합성 정책/순차 비교 성공만 보고한다. baseline 준비/적용, 실제 app binding, 실행·편집·송신·자동 수신, atomic snapshot·현재 서버·적용 순간 최신성 권한은 모두 false이며 requireApplyInput은 계속 차단한다.

## 보존 및 이번 처리

iPad는 기존 5,632개 중 격리 Xcode project 1개를 허용 변경으로 구분하고 나머지 5,631개 SHA 차이 0을 보고했다. 기존 reader/UI/controller/물리 저장소/Python 소스·검사·설치물·Windows ZIP·비공개 입력·충돌/미송신 18/19바이트 원본 두 개 및 종료 HTTP 323/Auth 12/writes 17·만료를 보존했다고 보고했다. 실제 서버/인증 요청·로그인 갱신·서명·설치·기기/시뮬레이터 실행·baseline 적용·관문/hold/prod 변경은 0이다. 추가 OS network-deny 검증 주장은 없다.

Windows는 이 접수 문서만 추가했다. 첨부 코드 추출·실행, 추가 구현·검사·재생성·조회·토큰 접근·설치·설정 변경은 하지 않았다. 기존 소스·원본·증거·종료 run은 보존한다. 이번 보고에는 Windows 출력 수정 요청이 없다.

## 다음 단계 안내 — 미승인 제안

다음 우선순위는 iPad 합성 A/B 실행 journal을 실제 파일 영속화 경계에 연결하고, 기록 실패·프로세스 종료 후 비용 유지와 재시작 차단을 오프라인으로 검증하는 것이다. 전원 손실/rollback 방어는 구현·검증 근거가 확보된 범위만 완료로 표시해야 한다. 이후 실제 transport/clock/session 및 baseline 입장 경계가 남는다.

이번 첨부는 결과 접수이며 후속 구현이나 실제 실행 승인으로 처리하지 않는다. 지금 사용자에게 기기 조작·추가 ZIP·계약 회신은 필요 없다. 후속 개발을 시작하려면 iPad에 위 journal 영속화의 오프라인 구현과 신규·영향 검사 한 묶음을 지시하면 된다. Windows의 추가 작업 완료를 기다릴 필요는 없다.
