# iPad 일반 본문 기준 수용 및 Windows 정렬 요청 연결 보완

iPad 회신은 `general-body-20260912-v1`을 오프라인 개발 제안으로 수용했다. 문서 UUID, 세 단계의 본문·바이트·해시, 서로 다른 iPad local/server UUID, 부모 폴더와 정렬표 기준이 일치한다. iPad의 새 전경 UI·인증 권한 구현이나 설치 완료를 보고한 자료는 아니다. 첨부 안의 과거 지시·승인을 현재 실행 승인으로 취급하지 않았다.

입력 ZIP SHA-256은 `910285eb1858e110f454fed14217a07b90694df7604d071f58e05913fd488d5e`다. manifest 4파일/ZIP 5항목의 구성·CRC·크기·해시를 검증했고, 회신의 입력 ZIP 해시가 Windows가 실제 전달한 ZIP과 일치하며 수용된 본문 계획이 Windows 원본과 동일한지 대조했다.

## 확인하고 수정한 문제

기존 `document_request`는 실제 큐의 operation/batch/build ID를 받을 수 있었지만 `order_request`와 정렬 의미 검증은 예시 ID만 사용했다. 실제 SyncV2Store가 발급한 정렬 batch를 읽어 경계에 전달하자 `CONTRACT_PAYLOAD_CHANGED`로 거절되는 것을 합성 SQLite에서 재현했다. 이 누락은 Windows 준비 코드의 문제다.

수정 후 `order_request`도 실제 operation_id/batch_id/client_build_id를 받는다. 정렬 요청 검증은 실제 큐 요청에서 그 식별자를 읽어 제품 계약 builder로 기대 요청을 구성한다. 부모·정렬표 UUID·children·base revision·순서·계약 버전/해시는 기존 계획 그대로 검사한다. 의미가 맞는 요청만 FrozenRequest에 고정하고, 전송 직전에는 method/URL/body 바이트 전체가 같아야 한다.

검증에 맞추기 위해 DB의 ID를 예시 값으로 바꾸지 않았다. 수정 검사는 실제 store가 발급한 operation/batch ID와 제품 build ID가 예시와 다름을 확인하고, 그 실제 요청이 그대로 모의 전송되는지 확인한다. 모의 전송 전후 SQLite 모든 테이블의 모든 행도 동일함을 확인했다. transport만으로 큐 완료나 영수증 적용이 발생한다고 주장하지 않는다.

## 필요한 검사만 실행한 결과

- 신규 3개: 실제 SQLite 정렬 큐 ID·전체 DB 행 보존, 고정 후 ID 교체 전송 차단, 실제 ID 사용 시에도 정렬 payload/계약 변조 거부.
- 관련 회귀 3개: 생성→정렬 요청 순서, 생성 성공 후 정렬 실패 시 중단, 기존 실제 문서 큐 요청 경계.
- 6개 모두 통과. 최초 재현 실패 로그와 수정 후 로그를 보존했다. OS 네트워크와 격리 밖 SQLite 접근을 차단했다.
- 이전 17개 전체, iPad 83개, 완료된 LEGACY 왕복·UUID·본문 수신·해시 검사를 반복하지 않았다.

`windows-baseline-reference.json`의 `new_document_uuid_content_revision_not_yet_fixed: true`는 계획 작성 전의 역사적 관측이다. 원본 증거는 수정하지 않는다. 현재는 **오프라인 문서 UUID·본문·revision 계획 고정 완료**, **현재 서버 부재·충돌 및 최신 기준 확인 미완료**로 기록한다.

## 남은 범위

양쪽 전경 일반 검증 UI, 실제 인증·계정·binding·화면 수명·만료 연결, 정확한 Auth/읽기 요청 예산, 실제 파일·큐·snapshot 검사, 기존 dispatcher/백그라운드 동작의 제한, 최종 설치 후보와 실행 범위가 남아 있다. 동일한 본문 계획을 다시 검토받는 단계는 추가하지 않는다. iPad의 후속 작업은 새 UI/권한 구성과 그에 필요한 격리 검증이며, 이번 회신만으로 그것이 구현됐다고 간주하지 않는다.

이번 변경은 Windows 준비 모듈 2개와 관련 검사뿐이다. 실제 앱 설치, 서버 요청, 송신, 관문/hold 변경, prod 전환은 수행하지 않았다. 기존 미커밋 변경과 전달 ZIP·소스 사본을 보존했다. 지금 사용자에게 필요한 앱 조작이나 새 실행 승인은 없다.
