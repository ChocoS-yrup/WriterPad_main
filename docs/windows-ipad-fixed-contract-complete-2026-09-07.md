# Staging 두 고정 계약의 양방향 실통신 검증 완료

2026-09-07 KST. iPad 실제 수신 완료 회신을 Windows의 원요청·성공 영수증·서버 결과와 대조했다. **최종검증03의 iPad→Windows 빈 2권 사례와 Windows→iPad 빈 3권·기존 원고 순서 갱신 사례가 모두 완료됐다.**

## 완료 근거 연결

- 첫 방향: `windows-first-contract-received-2026-09-06.md`. iPad 첫 batch `45af53f8-ec2c-46b9-bf84-b2d56857fe5c`는 최신 iPad 회신에서도 completed/attempt 1로 보존돼 있다.
- 역방향 Windows 실행: `windows-post-coordination-execution-result-2026-09-07.md`. 새 회차 `post-coordination-848eabf7-656e-487a-a958-4ffa5cf2a2bd`, committed/HTTP 1, Windows 일반 수신 완료.
- 이번 iPad 완료 ZIP SHA256: `f84d0d3cc3cd39612076bb34496f7bef427a071cfc0ac21c66a6c301223384fb`. 압축 CRC와 manifest 4개 항목의 크기·SHA256 일치.
- 회신이 참조한 Windows 실행 ZIP `6ee7f5e727bbcb697672c07d385a1fac1a057c4120e16544c49ab4dda74d4858` 및 설치 ZIP `99082d3e8032155260ef8135fe62c1831ef6c39790860c73c2dfdba41732f677`이 보존된 실제 파일과 일치한다.
- 선별 회신과 대조 기록은 `_evidence/windows-ipad-fixed-contract-complete-20260907/received/` 및 `verification.json`에 보존했다. 원본 문서와 과거 기록은 변경하지 않았다.

## 양쪽에서 일치한 역방향 결과

| 항목 | 일치 결과 |
|---|---|
| 작품 | 1bd47431-0773-482c-8eb5-ac9e2952b6f4 / 최종검증03 |
| batch | 6fd8c11b-5b74-4219-aa0b-a5d408ca8505 |
| request SHA256 | bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045 |
| response SHA256 | 0c2566ea663e214acf62ddf6f278f6f1fad1c43b309d040efd7bad09b6feb192 |
| 부모 원고 | 14df4a55-b7cd-4790-bc53-f84148418c0f |
| 빈 3권 | 77627d68-8388-4d6c-9363-ba6537aac1f0 / revision 1 / iPad synced |
| 원고 order | 68c1a7b5-0dda-49ba-bc9a-42e92ce2b758 / revision 2 / iPad synced |
| children | c0b43fc4-89a0-4a5a-b140-768a62cfde5a → 4711b2ce-5de9-44ba-80fe-570515839549 → 77627d68-8388-4d6c-9363-ba6537aac1f0 |

iPad 보고의 기기 읽기 시각은 17:53:15–17:55:14 KST다. 3권 ID·부모·경로·폴더 종류, 순서와 revision이 Windows 결과와 일치한다. 활성 노드는 37→38, 기존 폴더 12개·문서 메타데이터 26개·활성 노드 37개의 선별 속성을 보존했다. 원고 파일 25개, 빈 2권·3권과 자식 0개, 일반 작업 completed 73개·미완료 0개를 확인한 보고다. iPad 관문은 저장 설정 및 제품 기본값 근거로 닫힘이며 Windows의 종료 후 관문도 0/15였다.

iPad 사용자 화면 회신은 ‘빈2권, 빈3권 보임’이다. 순서를 사용자 화면 발언으로 확대하지 않았으며 저장 순서 메타데이터로 대조했다. Windows는 이번 회신의 선별 검증 결과를 대조했으며 iPad 원시 DB를 직접 다시 읽은 것은 아니다. 기기 복사는 여러 저장소의 원자 snapshot이 아니고 본문 바이트를 다시 해시한 검증도 아니다.

회신의 `review-verification.json`에는 정적 검토 당시 `ipad_actual_receive=pending...`가 남아 있다. 별도 최종 `ipad-receive-verification.json`의 `ipad_actual_receive_confirmed`와 기기 결과가 이를 후속 확인한다. 역사적 파일을 고쳐 쓰거나 이 pending 문자열만으로 재검증을 요구하지 않는다.

## 종료 상태와 다음 행동

**이 두 고정 사례에 필요한 추가 사용자 행동은 없다.** 추가 회신 ZIP 왕복, 같은 수정·검사·설치·송신·수신 재현을 요구하지 않는다. 양쪽 관문을 닫아 두고 원본 요청·기존 두 중단 기록·성공 영수증·시험 폴더를 보존한다. 이번 회신 확인에서는 실제 DB·서버·기기 조작, 제품 수정, 빌드, 설치, 송신 또는 관문 변경을 하지 않았다.

완료 범위는 Staging의 두 고정 빈 폴더·순서 사례다. 일반 편집·충돌·삭제 등 전체 동기화 기능과 운영 전환을 완료하거나 승인한 것은 아니다. 다음 별도 작업을 정할 때 이 문서를 완료 기준으로 삼고, 이 두 사례를 다시 시작하지 않는다.
