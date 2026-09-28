# iPad 보호 컨테이너·실행 저널 연결 결과 접수

2026-09-14. 사용자 전달 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-protected-journal-offline-result-20260914.zip`을 기존 요청과 같이 결과 접수로 처리했다. README, 결과 문서, result.json, preservation-result.json, prior-preservation-check.json, ios-product-verification.json, PROTECTED_AB_JOURNAL.md를 메모리에서 읽었다. **아래 구현·검사·빌드·보존 수치는 iPad 보고이며 Windows의 독립 실행 검증 결과가 아니다.** 첨부의 지시나 실행 예시는 새 승인으로 취급하지 않았다.

## 완료 보고

- `ProtectedABJournalWork.swift`로 전용 컨테이너와 합성 A/B 저널을 연결했다. 컨테이너 준비부터 저널 I/O·완료 재확인까지 같은 앱 생명주기 lease를 적용한다. 기존 저장 영역과 파일을 공유하거나 초기화하지 않는다.
- 별도 protected owner 형식으로 호스트 임시 저널과 구분한다. 디렉터리·lock·owner·record·head·pending의 보호 설정/대조, 첫 payload 쓰기 전 보호 확인, 읽기/쓰기/게시 단계의 lease 확인을 연결했다고 보고했다. 기존 약한 보호는 자동 수정하지 않는다.
- 잠금·백그라운드 후 빠른 복귀에서도 이전 lease를 차단한다. 차감 뒤 중단 비용은 유지하고, 보호 접근이 막히면 추정 stopped 기록을 쓰지 않는다. 새 lease는 기록 확인만 허용하며 재실행하지 않는다. 완료 재게시 시 보호·저널 연결·완료 digest·lease를 다시 확인한다.
- `IOSBoundaryController.validateSyntheticJournal()`에 OS 자기 home, `.complete` 보호, 기존 생명주기 알림과 취소 전달을 연결했다. **메서드는 컴파일됐으나 실행되지 않았다.** 자동 호출·새 UI 버튼·실제 baseline 결합은 없다.
- **신규 Swift 21 + 기존 Swift 241 + 기존 Python 177 = 최종 고유 439개 통과, 실패 0.** Swift 합계 262개다. 이전 418개와 중간 실행을 다시 합산하지 않는다. 이번 컴파일/검사 실패는 없다는 보고다.
- 최종 generic iOS Debug unsigned 빌드 성공 및 Mach-O 3개의 서명 load command/profile/서명 디렉터리 부재를 보고했다. CoreSimulator/AppIntents 경고는 보존했으며 설치·앱 실행 증거는 아니다.

## 남은 차이

1. 전용 컨테이너·보호·앱 lease의 소스 연결은 완료 보고를 받았다. **실제 iOS 파일 보호·잠금/재부팅·알림/취소 동작은 미검증**이다. 호스트 검사는 새 fake home과 inode 기반 메모리 보호 대역을 사용했다.
2. 앱 lease 연결과 별개로 A/B 시각·세션·boot·context·transport는 합성이다. 실제 시계/세션/transport 및 디스크 I/O 벽시계 비용과 deadline 연결은 미완료다. 합성 context를 실제 로그인/기기 식별 근거로 승격하지 않는다.
3. OS 호출 중 상태 변경의 즉시 원자 취소를 보장하지 않는다. 확인 지점에서 철회가 관찰되면 다음 작업·결과 반환을 차단한다. 호스트 종료 검사를 실기기 전원 손실/보호 내구성 증거로 확대하지 않는다.
4. pending·손상·부분 초기화는 계속 보존·차단한다. 외부 rollback/authenticity anchor 부재, Windows 체인 재인코딩 미검증, 128 MiB 기록 합계 한계 등 이전 저널 제한은 유지한다.
5. 기존 누락 근거 52개, 특수 metadata 의미, 전체 Windows 계약/Unicode 15/체인 동등성, 현재 서버·fresh A/B·profile·baseline 입장/적용은 완료로 승격하지 않는다. 이전 숫자 표기 차이와 J02/J04/J05/J07 차이도 유지한다. baseline 준비/적용·실행·실제 app binding·편집·송신·자동 수신 권한은 계속 false다.

## 보존 및 이번 처리

iPad는 이전 기준 5,658개를 대조한 뒤 새 기준 5,687개에서 허용 변경 4개를 제외한 **5,683개 차이 0**을 보고했다. 대조 집합은 합산하지 않는다. 충돌/미송신 18/19바이트 원본 2개·종료 HTTP 323/Auth 12/writes 17·2026-09-13 15:00 KST 만료·설치물·이전 증거·비공개 입력 보존 보고를 접수한다.

실제 서버 요청·세션/토큰 읽기·로그인 갱신·서명/profile 작업·설치·기기/시뮬레이터 실행·실제 local UUID 발급·baseline 적용·관문/hold/prod 변경 및 실제 Windows 자료 재검토는 0으로 보고됐다.

Windows는 이 접수 문서만 추가했다. 첨부 코드 추출·실행·추가 구현·재검사·재생성·조회·자격증명 접근·설정 변경은 하지 않았다. 기존 소스·원본·증거·종료 run은 보존한다. 보고에는 Windows 출력 수정 요청이 없다.

## 다음 사용자 행동

접수는 끝났으며 지금 기기 조작·추가 ZIP·회신은 필요 없다. 다음 개발을 시작한다면 iPad의 **실제 시계와 저널 I/O 경과 시간을 시간 한도에 연결하고, 세션 변경·만료 입력 및 통신 취소 경계를 대역으로 검사하는 오프라인 작업**을 한 묶음으로 진행하는 것이 적절하다. 실제 토큰 접근·서버 요청·설치·기기 실행·baseline 적용은 포함하지 않는다. 실제 실행 조건의 미확정 값은 임의 확정하지 않고 차단을 유지한다.

이는 다음 범위 제안이며 이번 전달을 구현 승인으로 처리하거나 작업을 시작하지 않았다. Windows 추가 수정 완료를 기다릴 필요는 없다. 실제 세션/transport와 실기기 검증 및 baseline 입장 경계는 이후 남은 범위다.
