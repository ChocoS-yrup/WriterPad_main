# 정렬 UUID 교정 후 iPad 수신 재개 — 검토 완료

2026-09-11 회신 `ipad-control-repair-resume-result-20260911.zip`을 Windows 교정 기준과 대조했다. **같은 journal의 1회 수신 재개와 오프라인 합성 본문 관찰을 완료로 인정한다.** 이번 수신 오류 복구 단계는 종료하며, 재개·교정·기존 시험을 반복하지 않는다. 일반 양방향 동기화 전체 완료를 뜻하지는 않는다.

입력 SHA-256: `90facd0b1a85975a0fbdff436381c8a0d6dfcbc9327cd61e156c3098f3b69362`.
35,380 bytes, manifest 27개 파일의 CRC·경로·중복·링크·정확한 구성·크기·SHA-256 검증 통과.

## 확인 결과

| 항목 | 확인한 결과 |
|---|---|
| 대상 작품 | 본문수신검증 20260911, `9a78c51c-7de9-43a8-be54-d25a22d08a28` |
| 재개 | baselinePull 시작·종료 1쌍, UI ‘가져옴’ 관찰 |
| 기존 수신 기록 | 동일 owner/transaction 보존, journal 1→0 |
| 작품 목록/연결 | 대상 공개 항목 1, binding 1 |
| 일반 원고 | UUID `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 93 bytes, revision 1 |
| 정렬 문서 | UUID `ef6e1de1-a3d0-5959-96be-58f87a683cc0`, 385 bytes, revision 1 |
| 폴더 | 11개, 기존 UUID·부모·이름·revision 보존 보고 |
| 대상 송신 자료 | operation/batch/recovery package 0, UUID tree_orders 0 |
| 기존 큐 | pending 4, conflict 3, completed 1265, cancelled 101 보존 |
| 사용자 본문 관찰 | 오프라인 세 줄 ‘모두 일치’ |
| 종료 상태 | 사용자 직접 종료, 이후 앱 부재 확인 및 오프라인·USB 연결 확인 |

본문 SHA `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d`와 정렬 SHA `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be`, 경로·UUID·바이트 수·revision·서버 nullable 구조 필드는 Windows 교정 후 기준과 일치한다.

사전 상태 JSON은 이전 실패 종료 상태 JSON과 정확히 같다. 사후 DB 테이블 지문/행 수 차이는 감사 보고의 차이 목록과 일치하며 전후 무결성 정상·FK 위반 0이다. 동결 소스 해시는 기존 manifest와 일치하고, 확보된 완전 소스 3개에서 관련 발췌를 직접 대조했다. 제공 검사 코드도 읽어 확인했다. 원시 기기 DB·파일 목록은 공유되지 않아 Windows에서 실제 DB 감사를 재실행하지는 않았다.

## 예외와 보존할 기록

- **75/76 검사 true.** `all_existing_Documents_directories_and_mtimes_preserved`는 false 그대로 유지한다. 디렉터리 202개 경로는 모두 보존됐지만 `ContractReviews`와 대상 `집필모드`의 수정 시각이 달라졌다. 해당 디렉터리의 기존 파일 바이트는 같다는 보고와 분류 근거를 확인했다. 복구 marker 생성/정리와 검토 자료 atomic 재출력 코드에 부합하지만, 파일시스템 호출 추적으로 원인이 확정된 것은 아니다. 원고 손상 증거로 해석하지 않으며 전체 검사 통과로 바꾸지도 않는다.
- 잘못된 UUID의 이전 marker는 **예상대로 잔존**한다. 812 bytes, SHA `f0e332c548a99431ce1f2f00a4037c73b30c83610bd3c4266a988574b96a1ceb` 유지. 해당 UUID의 성공 baseline은 없다. 이번 완료에 marker 삭제는 필요 없다.
- 완료 안내 문구는 관찰되지 않았다. ‘가져옴’ 상태, journal 제거, 목록 공개, 정상 baseline, 사용자 본문 확인을 완료 근거로 삼았다. 문구 관찰과 저장 완료를 혼동하지 않는다.
- 이번 실행 직전 독립적인 전체 바이트 백업을 새로 만든 것은 아니다. 현재 파일 열거가 이전 실패 후 보존 상태와 일치함을 확인해 기존 보존본의 복제본을 사용했다. 이 제한을 유지한다.
- 설치 바이너리를 직접 읽어 재해시하지 않았다. 기존 설치 등록/영수증과 당시 프로필 유효성을 확인한 보고다.
- 실제 앱의 HTTP 전체 추적이나 서버 전체 불변 검사가 없으므로 서버 쓰기 0회의 직접 계측으로 보고하지 않는다. 수신 가드·대상 장부·기존 큐·진단 이벤트로 범위를 확인한 보고다.
- 단계 중간 파일의 `pending/false`는 당시 기록이다. 최종 `resume-result-summary.json`, `end-state-verification.json`, `posttermination-offline-confirmation.json`을 따라 상태를 판단했다.

## 다음 단계와 사용자 행동

**지금 추가로 누를 버튼이나 다시 보낼 검토 회신은 없다.** 앱 종료·오프라인 상태를 유지한다. 이번 수신 재개, UUID 교정, 해시 보완, 완료된 격리 시험은 종결한다. 기존 marker 정리를 다음 검증의 선행 조건으로 추가하지 않는다.

다음 개발 검증은 지정 합성 원고의 **Windows→iPad 및 iPad→Windows 양방향 송신** 범위를 준비하는 단계다. 현재 수신 전용 후보/잠금 상태에서 이를 바로 실행할 수 있다고 간주하지 않는다. 필요한 코드·후보·시험 범위를 먼저 구체화하며 실제 송신·관문 변경·설치가 필요하면 그 구체적 범위에 대한 사용자 승인 후 진행한다. prod 전환은 포함하지 않는다. 이번 iPad 실행 승인은 회신에 보고된 과거 수신 범위이며 이후 작업의 승인이 아니다.

검토 근거: `_evidence/ipad-control-repair-resume-review-20260911/cross-verification.json`.
이번 Windows 검토는 로컬 첨부 자료 대조만 수행했으며 서버/기기 조회·변경이나 설치·송신은 수행하지 않았다.
