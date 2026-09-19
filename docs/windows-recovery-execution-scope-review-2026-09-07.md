# Windows: 별도 HTTP 0 복구 실행 범위안 대조

2026-09-07 KST. 첨부는 실행 범위안이며 새 사용자 실행 승인이 아니다.

## 대조 완료

- 수신 ZIP SHA256 `4093d6954f15b573da6daf803612bee4ae003b00fb0cb9f3e83cf296d5c084de`, manifest 13개 항목 검증.
- iPad가 대조한 이전 Windows 결과 ZIP `1e641e656b6fdb973647792b5fb6fc012c9f3a6474ad693b8a2f73045160646c`과 근거 34개가 로컬 자료와 일치한다.
- 현재 관련 소스/시험 9개와 설치 EXE `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`가 검토본과 일치한다. 제품 수정·재빌드·설치·완료 시험 반복은 하지 않았다.
- 원본 요청 bytes와 고정 batch/request SHA256, 빈 3권 create/base 0, 기존 tree_order reorder/base 1 및 두 operation ID가 일치한다.
- 실제 DB를 다시 immutable 읽기 전용으로 확인했다. schema 8011, 원본 준비 1개·기존 stopped/HTTP 0 실행 1개, 관문 0/15개 열림, 복구 테이블 3개 존재/행 각 0개다. 이전 조회 후의 선별 protected_state 전체와 같다. 원고 본문이나 전체 DB는 읽지 않았다.
- 서버를 다시 호출하거나 첨부 스크립트를 실행하지 않았다. 보관된 02:38:55 원장 증명을 새 실행의 fresh proof로 사용하지 않는다. 현재 앱의 모든 준비 조건까지 확인했다는 뜻은 아니다.

## 새 승인으로 허용할 수 있는 범위

- Staging `mhpnszcorfzrvhyondxr`, 최종검증03 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`.
- 고정 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`.
- request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`.
- 기존 원본 및 stopped 기록을 보존하고, 제품 UI에서 새 승인과 별도 로컬 복구 회차 **1개**를 생성한다. 외부에서 승인 JSON을 만들어 주입하지 않는다.
- 작업은 원고 아래 빈 3권 생성(예상 revision 1), 기존 원고 순서를 `[1권, 2권, 3권]`으로 갱신(예상 revision 2)하는 두 가지다. 새 3권은 그대로 비워 둔다.
- 직전 검사 통과 시 해당 Windows 작품 관문만 일시 사용하고 `atomic_structure_commit` 쓰기 HTTP **최대 1회**를 수행한다. 종료 시 원래 작품의 관문 닫힘을 확인한다. iPad 관문은 계속 닫아 둔다.
- 이후 원본/복구 영수증/서버 결과의 읽기 전용 확인과 일반 수신 대조를 수행한다. 추가 회차·재전송·요청 재발급·원본/실행 기록 초기화·수동 완료·원고 자동 생성·추가 구조 변경은 제외한다.

## 다음 사용자 행동

실제 시험을 계속하려면 양쪽 최종검증03의 편집·구조 변경을 멈추고 위 범위에 대한 새 승인 의사를 남겨야 한다. 첨부의 예시 문구나 이전 구현·설치·읽기 승인만으로 실행하지 않는다.

승인을 받은 뒤에도 최신 준비 관찰을 확인하고 버튼 조작 시점을 별도로 안내한다. 지금 `HTTP 0 중단 · 별도 복구 승인` 또는 기존 검토 배치 송신 버튼을 누르지 않는다. 회차 생성 뒤 HTTP 0으로 다시 멈춰도 이번 한 회차는 소모되며 재클릭하지 않는다.

근거: `_evidence/windows-recovery-execution-scope-review-20260907/verification.json`, `before-state.json`, `after-state.json`, `received/`.
