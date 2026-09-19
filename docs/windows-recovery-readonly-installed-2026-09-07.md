# Windows 원장 독립 조회 설치 완료

2026-09-07 02:37:45 KST. 사용자 앱 종료를 확인한 뒤 준비된 독립 읽기 설치본으로 교체했다.

- 설치 경로: `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`.
- 설치 SHA256: `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`, 79,300,754 bytes.
- 이전 진단 EXE를 `_evidence/windows-recovery-readonly-20260907/previous-installed.exe`에 백업했다. 이전 해시는 `a4ab309d0fd7e481a0cb1c7b6bc178919609d5076c686bfaf35d5bd81d6bd9b0`.
- 설치본 Qt smoke 종료 코드 0. 프로젝트·인증 초기화 이전 검사이며 실제 로그인 RPC 검증은 아니다.
- 설치 전후 DB를 immutable 읽기 전용으로 확인했다. 원본 준비 1개·기존 실행 1개(stopped/HTTP 0), 열려 있는 관문 0/15개, 복구 회차·진행 사건·영수증 0개를 확인했다. 선별 protected_state가 동일하다.
- DB는 아직 schema 8010이며 설치된 새 소스는 8011이다. 일반 앱 시작에 따른 마이그레이션과 실제 로그인 읽기 결과는 다음 확인 대상이다. 실제 회차 생성·계약 송신·재전송·관문 개방은 수행하지 않았다.

## 사용자가 지금 할 일

1. 집필프로그램을 열고 최종검증03을 연다.
2. 설정의 클라우드 영역에서 **서버 원장 조회 · 회차 생성 없음**을 한 번 누른다.
3. 완료 또는 오류가 표시되면 **원장 조회 결과 내보내기**로 `windows-recovery-readonly.json`을 작품 밖에 저장해 Windows 작업에 전달한다. 예: `D:\안티그래비티\scratch\집필프로그램\windows-recovery-readonly.json`.

복구 승인·검토 배치 송신 버튼은 사용하지 않는다. 이 단계는 현재 앱 로그인으로 새 nonce의 원장 읽기만 확인한다. 결과 수신 후 원본/스키마/관문/회차를 읽기 전용으로 대조하고 iPad 전달 ZIP을 작성한다.

근거: `_evidence/windows-recovery-readonly-20260907/installation.json`, `before-state.json`, `after-state.json`, `install.ps1`, `read_protected_state.py`.
