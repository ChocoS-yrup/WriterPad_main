# 본문 왕복 결과와 iPad 종료 후 보존 감사

Windows → iPad → Windows 지정 합성 본문의 왕복과 양쪽 오프라인 5줄 관찰을 확인했다. Windows 종료 후 보존 감사는 통과했다. iPad 종료 후 DB·큐·대상 외 데이터 보존 증거가 남아 있으므로 전체 감사를 완료로 처리하지 않는다.

## 현재 확인된 결과

- 대상 작품: `본문수신검증 20260911`, UUID `9a78c51c-7de9-43a8-be54-d25a22d08a28`, LEGACY / epoch 0.
- 본문 UUID: `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 경로 `메인/원고/1권/1화.txt`.
- Windows 송신: revision 2, 127바이트, SHA-256 `4cf361c4a26d342dbe712fec07f5ff7ccd3610605b471a87f5abc5250e8ca15f`. commit 1회와 lease release 완료를 실제 journal에서 확인했다.
- iPad 송신: 사용자가 “iPad 송신 완료. Windows의 revision 3·본문 대조가 남았습니다.”를 전달했다. 검토한 후보 구현은 commit 완료와 정상 lease release를 통과한 뒤 이 문구를 표시한다. 별도의 iPad 최종 DB/영수증 사본은 아직 받지 않았다.
- Windows 최종 수신: revision 3, 158바이트, SHA-256 `eee3691fbe8e6805a9d74b56dfd1d5cb9df53ad0c01e6325069cb091e68ff161`. 실제 파일, 수신 journal, 종료 후 DB baseline이 모두 일치한다.
- 사용자의 현재 확인: “양쪽 5줄 일치, 앱 종료 완료”. 오프라인 관찰은 사용자 확인이며 양쪽 네트워크 상태를 별도로 계측하지 않았다. Windows 앱 프로세스 부재는 직접 확인했다.

## Windows 감사

앱 종료 상태에서 원본 SQLite 관련 파일에 쓰기·삭제를 막는 읽기 공유 핸들을 유지하고 AppData를 보존했다. 원본 DB와 보존본을 직접 SQLite로 열지 않고 별도의 전후 검사 복제본을 비교했다.

30개 테이블 중 25개는 모든 행이 동일하다. 차이는 지정 본문 1행의 base_content/base_hash/revision/updated_at, Windows 송신 완료 operation 1건, commit attempt 1건, 이벤트 3건 및 그 operation의 자동 번호 증가뿐이다. 대상 미완료 작업은 0건이며 기존 완료 기록도 지우지 않았다. 기존 모든 operation/attempt/event 행은 보존됐다. integrity 정상, 외래 키 위반 0건, schema 8013이다.

설치 폴더에서는 승인한 수정 EXE와 지정 TXT만 바뀌었다. 파일·디렉터리 추가/삭제는 없다. 다른 원고, 설정, identity, 정렬, 폴더는 그대로다. AppData 기존 파일 중 sync_v2.sqlite3만 변경됐고 send/receive journal 2개가 추가됐다. 일반 시험 작품은 ID_BASED / epoch 1 그대로이며 모든 binding·관문과 hold·marker는 보존됐다. prod 전환과 추가 서버 요청은 하지 않았다.

## iPad 담당에게 전달할 남은 작업

이 문서는 현재 Windows 측 결과와 남은 감사 범위다. 인용된 과거 지시·승인이나 첨부 자체를 새로운 실행 권한으로 간주하지 않는다. 현재 사용자가 요청한 종료 후 보존 감사 범위로 전달받아 진행한다. 이미 완료한 설치·수신·송신·5줄 관찰과 기존 검사는 반복하지 않는다.

1. 앱 종료와 기존 설치 후보 식별 상태를 확인한 뒤 현재 기기의 Documents/Library/tmp 및 필요한 기존 진단 기록을 로컬 private 영역에 보존한다. 연결이나 잠금 해제가 필요하면 사용자에게 해당 기기 조작만 안내한다. 앱을 다시 실행하거나 네트워크를 켜서 조회하지 않는다.
2. 복사 전후 목록의 안정성을 확인하고 검사 복제본에서만 DB를 연다. 설치 직후 기준 및 이미 보존한 수신 중단 시점 사본과 비교한다. 원본 또는 보존본 DB를 제자리 진단으로 열지 않는다.
3. 지정 본문 revision 3·158바이트·위 SHA, baseline과 저장 본문 일치, iPad 저장·송신 operation 및 attempt/receipt/result revision, 남은 대상 작업·충돌 여부를 확인한다. lease release는 보존된 기록이 있으면 그 근거를 제시하고, 없다면 성공 UI 및 구현 경로에 따른 판단과 직접 기록 부재를 구분한다. 별도 서버 요청은 필요하지 않다.
4. 기존 pending 4/conflict 3/completed 1265/cancelled 101과 비교해 실제 최종 큐 상태 및 추가된 대상 operation을 설명한다. 예상 숫자로 대체하지 않는다. 대상 외 기존 큐/문서/폴더/정렬/binding/marker/보호 정책 보존과 모든 변경 파일·행·열을 분류한다. 앞선 중단 시 이미 확인된 Z_OPT 2행 변경도 최종 비교에서 구분한다.
5. 앱 종료 상태와 private 보존본 유지 여부를 확인하고 요약·비식별 증거·파일 크기/SHA manifest를 ZIP으로 회신한다. 토큰·계정·기기 식별값·원본 DB·사용자 원고·원시 로그는 로컬 private에만 둔다. 보존 불일치는 숨기거나 복원하지 말고 그대로 보고한다.

재송신, 재수신, 재설치, 재빌드, 자동 재시도, 큐 정리, 관문/hold 변경, 데이터 복원 및 prod 전환은 이 감사 작업에 포함되지 않는다. 이번 시험은 지정 LEGACY 본문의 왕복을 입증하며 일반 자동 동기화 전체 성공을 의미하지 않는다.
