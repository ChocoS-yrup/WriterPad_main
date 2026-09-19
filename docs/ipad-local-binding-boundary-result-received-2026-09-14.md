# iPad 로컬 결합 경계 결과 접수

2026-09-14. 사용자 요청에 따라 별도로 진행한 로컬 경계 구현의 결과와 남은 차이만 기록한다. 입력은 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-local-binding-boundary-result-20260914.zip` 내부 결과 문서·result.json·preservation-result.json이다. 아래는 iPad 전달 보고이며 Windows에서 소스 감사·검사·보존 hash 재검증을 수행한 결과가 아니다.

**계약 회신은 이미 iPad에 전달됐다는 사용자 설명을 반영한다.** 첨부의 “계약 회신 대기” 표현과 구분하며, 이번 구현 결과를 계약 제안의 최종 수용으로 해석하지 않는다. 추가 구현·검사·실제 생성/수신 승인도 아니다.

## 결과

- 독립 패키지에 LocalBoundary.swift를 추가해 의존성·저장소·상태 경계를 합성 메모리 대역으로 구현했다고 보고했다. 기존 앱 source/target·실제 DB 구현은 변경하지 않았다.
- 신규 로컬 경계 18개 + 기존 Swift 영향 32개 + Python 영향 44개 = 최종 고유 94개 통과, 최종 실패 0. 기존 영향 합계는 76개이며 이전 보고의 76개를 다시 합산하지 않는다.
- 최초 시도는 시험 callback의 throwing/non-throwing 형식 오류로 컴파일 단계에서 종료돼 검사가 시작되지 않았다. 수정 후 신규 17개/기존 Swift 32개 통과, 물리 저장소·링크 차단 검사 1개를 추가해 최종 신규 18개를 통과했다고 보고했다. 최초 로그는 보존했다.
- 입력·bundle 문자열·합성 identity·임시 namespace·기존 물리 저장소 부재를 확인한 뒤에만 대역 팩토리를 호출한다. 관찰/제안 입력은 거부하고 실제 계약 미확정 후보는 realContractUnresolved로 차단한다.
- bodies/metadata/documentBaseline/folderBaseline/treeOrderBaseline의 다섯 합성 저장 영역과 전체 완료 digest를 구분하고 쓰기 후 읽기 대조를 요구한다. 무동작 저장·삼킨 오류·초안·알 수 없는 상태·완료 후 손상은 성공이나 자동 초기화로 처리하지 않는다.
- synthetic_boundary_ready=true. baseline_ready/applied·execution_allowed·app_binding_created·editing_allowed·sending_allowed·automatic_receive_allowed와 실제 local UUID 발급은 false다.
- 이전 5,294개 파일 대조와 작업 전후 5,310개 파일 보존 대조는 각각 차이 0으로 보고됐다. 서로 다른 집합이며 합산하지 않는다. 기존 충돌·미송신 18/19바이트 원본 2개·종료 사용량/만료·설치물·증거·기존 경계 보존 보고를 접수한다.
- 서버 요청·로그인 갱신·서명/프로파일 작업·설치·기기/시뮬레이터 실행·실제 baseline 적용·관문/hold/prod 변경·실제 Windows 관찰 재검토/적용은 0으로 보고됐다.

## 남은 차이·미완료

1. iOS 의존성·파일 보호·생명주기, 실제 전용 앱 컨테이너/local identity, 물리 저장소의 읽기·쓰기·잠금·영속화, 앱 target 결합은 미완료다. bundle 문자열 대조는 실제 서명/프로파일 검증이 아니다.
2. 새 session을 같은 메모리 대역에 연결한 재개 검사는 DB 영속화·프로세스 재시작 검증이 아니다. 기존 파일 adapter의 프로세스 중단 검사를 새 메모리 경계의 영속성 증거로 확대하지 않는다.
3. 실제 revision/structure revision/폴더·order 버전/epoch/삭제/null 의미, 생성 후 원문·대상 결합, 완전성·일관성의 최종 수신 계약은 미확정이다. 전달된 Windows 계약 회신의 최종 수용 여부는 별도이며 실제 후보 차단 상태를 유지한다.
4. raw 본문·handshake 의미, 체인 재인코딩, 후보 profile 결합, 서버 출처/최신성 및 과거 합성 reference 부재의 미검증 상태는 유지한다.

Windows에서는 위 기록을 읽고 이 접수 문서만 추가했다. 후속 구현·검사·실제 생성/수신·토큰 접근·기존 기록 변경은 하지 않았으며 새로운 작업 지시를 발급하지 않는다.
