# iPad 관찰 reader 결과 접수

2026-09-14. 사용자 요청에 따라 결과와 남은 차이만 기록한다. 입력은 `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-observation-reader-result-20260914.zip` 내부 결과 문서·result.json·preservation-result.json이다. 아래 내용은 iPad 전달 보고이며 Windows에서 소스 감사·검사·보존 hash 재검증을 수행한 결과가 아니다. 첨부의 실행 예시나 제안은 새 권한으로 취급하지 않는다.

## 결과

- 읽기 전용 Python 관찰 검토 모듈/API·CLI 구현 완료 보고. 앱에 결합한 Swift adapter나 baseline 적용 adapter는 아니다.
- 신규 24개 + 기존 검토기 영향 20개 = 최종 44개 통과, 최종 실패 0개. 최초 임시 경로 별칭 관련 2개 실패는 합성 fixture 경로를 수정해 해결했으며 최초 로그를 보존했다고 보고했다. 다른 단계의 기존 검사 수와 합산하지 않는다.
- 실제 Windows 관찰 `638699a2-7d1f-475d-a55e-a8999a6cbee3`의 metadata 17개·order 15개 대조: reference 차이 0, 미대조 항목 0.
- 기존 정상/부분 합성 예제는 각각 관찰/부분 관찰로 읽었으며, 누락된 reference나 부분 결과를 채우지 않았다고 보고했다.
- baseline_ready/applied, execution_allowed, 앱 binding은 false. 제품 앱 소스 변경·서버 요청·로그인 갱신·서명·설치·기기 실행·관문/hold 변경은 0으로 보고됐다.
- 보존 대조는 원본 증거 4,465개, 이전 준비 자료 310개, 앱 소스 251개, 기존 검토기/요청서 5개, J04 대상 21개에서 차이 0으로 보고됐다. 충돌·미송신 18/19바이트 원본 2개와 종료 사용량·만료·설치물 보존 보고를 접수한다.

## 남은 차이·미완료

1. Q1~Q7 raw 미제공으로 본문·계약 원문의 독립 검증은 미완료다. 이번 reader는 raw가 제공돼도 hash/길이만 확인하며 본문·handshake 의미를 독립 검증하지 않는다. count와 요청 단계 확인도 Windows 기록 대조이지 raw 배열의 재계산이 아니다.
2. 교차 플랫폼 journal 체인 재인코딩/hash 호환, 후보 profile의 기계적 결합, 서버 출처·현재 상태는 미검증이다.
3. 과거 합성 예제의 원래 reference 부재는 그대로이며 실제 관찰의 17/15 reference로 대신하지 않는다.
4. 검증용 local project 발급, 앱 내 reader 결합, 합성 초기 수신 적용/재개 adapter, 실제 baseline 확보·적용은 미완료다.

Windows에서는 이번 기록 외 추가 구현·자료 생성·재검사·재조회·토큰 접근·로그인·서명·설치·기존 기록 변경을 하지 않았다. 후속 작업 지시는 발급하지 않는다.
