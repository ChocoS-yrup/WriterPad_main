# iPad 합성 A/B 실행 저널 영속화 결과 접수

2026-09-14. 사용자 전달 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-ab-journal-offline-result-20260914.zip`을 결과 접수로 처리했다. README, 결과 문서, result.json, preservation-result.json, prior-preservation-check.json, ios-product-verification.json, SYNTHETIC_AB_DISK_JOURNAL.md를 메모리에서 읽었다. **아래 구현·검사·보존 수치는 iPad 보고이며 Windows의 독립 실행 검증 결과가 아니다.** 첨부의 지시·실행 예시는 새 승인으로 취급하지 않았다.

## 완료 보고

- `SyntheticABDiskJournal.swift`를 합성 A/B runner에 선택적으로 연결했다. 호스트 합성 임시 경로에서 owner 결합, 변경 불가 기록의 SHA 연결, head, 독점 잠금, 쓰기·fsync·재읽기를 구현했다. 격리 iOS target에는 컴파일 참조만 추가했으며 UI 실행 경로는 없다.
- reserve/start 저장 확인 후에만 합성 transport를 호출한다. HTTP 14회 안에 Auth 확인 2회 포함, 실패 차감 유지·추가 요청 및 재시작 차단을 유지한다. 초기 상태도 새 객체/프로세스로 다시 열면 실행하지 않는다. pending/orphan/손상/누락/결합 오류는 보존·차단하고 추정 복구하지 않는다.
- **신규 Swift 36 + 기존 Swift 205 + 기존 Python 177 = 최종 고유 418개 통과, 실패 0.** Swift 합계 241개다. 호스트 종료 11개 지점 및 별도 프로세스 잠금 경쟁·SIGKILL 후 차감 보존 검사를 포함한다. 이전 382개 및 중간 실행 수를 다시 더하지 않는다.
- 신규 시험 callback의 throwing 함수 사용으로 컴파일 실패 1회가 있었고 검사 시작 전 수정했다는 보고다. 최종 기준은 swift-tests-05.log와 python-impact-01.log다. generic iOS Debug unsigned 빌드 성공과 서명/profile 부재를 산출물에서 확인했다고 보고했다. CoreSimulator/AppIntents 경고는 남고 설치·실행 증거는 아니다.

## 남은 차이

1. 이전 메모리 저널 단계에서 **호스트 합성 파일 영속화와 프로세스 종료 검사까지 진전**했다. iOS 전용 저널 컨테이너·파일 보호·실행 경계 연결과 실기기 검증은 미완료다.
2. 실제 transport/clock/session 연결 및 디스크 I/O 벽시계 비용의 실제 deadline 반영은 미완료다. 합성 시각/만료는 실제 승인이나 기존 종료 실행의 갱신이 아니다.
3. fsync 및 호스트 종료/SIGKILL 검사는 전원 손실·실기기 내구성 보장이 아니다. 전체 저장소 삭제/일관된 과거 묶음 치환을 검증할 외부 신뢰 anchor가 없고 외부 프로세스의 모든 경로 교체 경쟁을 방어하지 않는다.
4. 파일당 48 MiB·기록 합계 128 MiB 제한이다. snapshot 반복 보관으로 응답 32 MiB 예산보다 저장 한도가 먼저 찰 수 있으며 보존·차단한다. 자동 정리·한도 확대는 없다.
5. 내부 SHA 연결은 서명·서버 출처·Windows journal 체인 호환 증명이 아니다. 기존 누락 근거 52개, 특수 metadata 의미, 전체 계약/Unicode 15/체인 동등성 및 현재 서버·fresh A/B·profile·baseline 입장/적용의 미검증 상태를 승격하지 않는다. 이전 Swift/Python 미정 숫자 표기 차이와 J02/J04/J05/J07 차이도 유지한다.
6. baseline 준비/적용, 실제 app binding, 실행·편집·송신·자동 수신 권한은 계속 false다.

## 보존 및 Windows 처리

iPad는 이전 5,632개 파일을 승인된 이전 변경 hash와 대조한 뒤 새 기준 5,658개를 설정했다고 보고했다. 이번 허용 변경 3개를 제외한 **5,655개 차이 0**이며 두 대조 집합은 합산하지 않는다. 기존 충돌/미송신 18/19바이트 원본 2개·종료 HTTP 323/Auth 12/writes 17·2026-09-13 15:00 KST 만료·설치물·이전 증거·비공개 입력 보존 보고를 접수한다.

실제 서버 요청·세션/토큰 읽기·로그인 갱신·서명/profile 작업·설치·기기/시뮬레이터 실행·실제 local project UUID 발급·baseline 적용·관문/hold/prod 변경 및 Windows 실제 관찰 재검토는 0으로 보고됐다. 추가 OS network-deny 검증 주장은 없다.

Windows는 이 접수 문서만 추가했다. 첨부 코드 추출·실행·재검사·추가 구현·재생성·조회·자격증명 접근·설정 변경은 하지 않았다. 기존 소스·원본·증거·종료 run은 보존한다. 보고에는 Windows 출력 수정 요청이 없다.

## 다음 사용자 행동

이번 전달로 결과 접수는 끝난다. 지금 기기 조작·서버 승인·추가 ZIP·회신은 필요 없다.

후속 개발의 다음 범위로는 **iPad 저널을 전용 컨테이너·파일 보호·생명주기 경계에 오프라인 연결하고 신규·영향 검사를 한 묶음으로 진행**하는 것을 제안한다. 합성 입력을 유지하고 실제 서버/로그인/설치/기기 실행/baseline 적용은 포함하지 않는 범위다. 실제 시계·세션·transport와 실기기 검증은 여전히 별도로 남는다. 이는 다음 단계 안내이며 이번 첨부로 구현을 승인받았거나 시작한 것이 아니다. iPad의 해당 작업은 Windows의 추가 수정 완료를 기다릴 필요가 없다.
