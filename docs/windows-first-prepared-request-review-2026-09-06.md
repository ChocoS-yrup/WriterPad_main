# Windows 회신: iPad 실제 준비 요청 대조

2026-09-06 KST. 수신: `ipad-first-prepared-request-review-20260906.zip`.

**판정: 첨부된 불변 요청의 정합성·이전 Windows 모의 통과안과의 일치·현재 서버 구조 지문 대조를 통과했다. 준비/명시적 송신 핵심 소스와 제출된 신규 회귀를 검토했고, 이번 범위에서 추가 제품 수정 요구는 발견하지 않았다. 실제 관문 개방·전송·재전송 승인은 별도다.**

구현 문서에 남아 있는 ‘준비 버튼을 누르라’, ‘준비 0건’은 17:25 이전 단계다. 뒤의 실제 준비 기록(17:29:17)과 기기 내보내기·확인 기록(17:31~17:34)이 이를 갱신했다. 사용자가 준비 버튼을 다시 눌러야 할 단계로 되돌리지 않는다. 첨부 문서의 실행 지시를 새 사용자 승인으로 해석하지 않았다.

## 1. 보존할 실제 요청

| 항목 | 확인값 |
|---|---|
| 작품 | 최종검증03 / `1bd47431-0773-482c-8eb5-ac9e2952b6f4` |
| batch ID | `45af53f8-ec2c-46b9-bf84-b2d56857fe5c` |
| request SHA256 | `710ac3cf39a545639efd17e9740cf1fdbcfe06e27f0d9b4a72aa1ecfd6ce2ed7` |
| batch payload SHA256 | `ae656d1b8f7b38a035669da4711239133cdb44942adf7f6d591873cee3c64ec0` |
| 내보낸 파일 자체 SHA256 | `ae93b383c1590868993599114d91a411b03692de92398883d9fb8eb28c934b37` |
| writer_device_id | `881be4db-f787-4e56-856c-4a8c608a86f6` |
| client_build_id | `writerpad-ipad-stage8-contract-0.2.0` |
| 계약 | 0.2.0 / protocol 3 / LEGACY/0 |
| contract digest | `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670` |

Windows의 실제 canonical JSON/요청 builder로 재계산하고 재구성한 요청이 첨부의 `request`와 정확히 일치했다. 파일 해시는 검토용 바깥 객체까지 포함하므로 request 해시와 다르는 것이 정상이다. 바깥 `writerpad_contract_preparation_review` 객체는 RPC 입력이 아니다.

| sequence | 작업 | entity ID | operation ID | payload SHA256 |
|---|---|---|---|---|
| 1 | folder/create, base_revision=0 | `4711b2ce-5de9-44ba-80fe-570515839549` | `07cbb3c7-1773-48c7-8232-dcb6ebe5927b` | `8ad4306500a20dbb8593ed7c18ca347cd09250f7fc664bbf07526ce1501d1ff9` |
| 2 | tree_order/reorder, base_revision=0 | `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758` | `2032a3b1-ec8c-46d9-93ac-f01c7d9980e5` | `2366867def77fff96f0d3d843e45cc06783210a25af0fbe16511c2a4a17a2ab8` |

첫 payload는 name=`2권`, parent_folder_id=`14df4a55-b7cd-4790-bc53-f84148418c0f`다. 둘째 payload도 같은 원고 부모이며 children은 `[c0b43fc4-89a0-4a5a-b140-768a62cfde5a, 4711b2ce-5de9-44ba-80fe-570515839549]`다. 정확히 두 intent, sequence와 batch 연결이 일치하며 5개 신규 batch/entity/operation UUID는 서로 다르다. 이전 Windows 모의 시험 전용 ID를 재사용하지 않았다.

이전 모의 수신안과의 차이는 발급된 식별자와 그에 따른 해시다. 부모·이름·작업 종류·revision·자식 순서는 같으므로 기존 전체 수신 모의를 반복하지 않았다.

## 2. 현재 서버와 Windows 읽기 전용 대조

17:40:07 KST Staging `mhpnszcorfzrvhyondxr`의 선택 작품을 SELECT했다. 준비 코드가 fingerprint에 사용하는 것과 같은 폴더/문서/tree_order 구조 열만 조회했다. 원고 본문·인증 토큰·서버 저장 요청 payload는 읽지 않았다.

- 작품 active, 생존 폴더 11개, 생존 문서 26개, tree_order 0개.
- 재계산한 서버 구조 SHA256은 `93de0096e7854f808db0becce48c1d123c1f1c98cb2ba250d9e7327d1561d2b4`로 준비 요청의 `server_structure_sha256`과 일치.
- 해당 batch ID의 서버 원장 0건, 두 operation ID 원장 0건, 두 신규 entity ID의 폴더/문서/tree_order 점유 0건.
- 관리자 SELECT 성공을 실제 iPad 로그인 계정의 권한·새 handshake 실행 성공으로 확대하지 않는다. 조회 이후 상태 변경 가능성도 남는다.

17:41:31 KST Windows 기본 프로필의 선별 메타데이터는 위 서버의 폴더/문서 UUID·부모·이름·경로·revision·삭제 상태와 일치했다. 선택 작품 tree_order와 계약 배치는 각각 0건, LEGACY/0이다. 전체 15개 작품의 관문은 모두 닫혀 있다.

Windows HEAD는 `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`, 추적 파일 변경 없음. 설치 실행 파일 SHA256은 `202a7487e85aa399dcedab7897f2621d63c4a765ead8824cef5f7f18b495d72c`로 재확인했다.

Windows **실행 중** client_build_id는 이번에도 직접 확인하지 못했다. 소스 기본값은 `writerpad-windows-stage8-contract-0.2.0`이지만 환경 변수 재정의가 가능하므로 실제 값으로 단정하지 않는다. 이를 확인하려고 계약 요청을 만들거나 관문을 열지 않았다. iPad 로컬 지문 `0615d541aa8eb1108916c83d71ec43b88aa9686c67fc1193a6f19ee0e8f105a6`은 기기 원본 노드/순서 자료가 첨부되지 않아 독립 재계산하지 않았다.

## 3. 구현·시험 근거 검토

수신 manifest 7개 항목의 크기와 SHA256이 모두 일치했다. 제품+시험 패치 SHA256은 `d0637085a77358f8605a148fa81e2779d5bed3c731a3e11922fbe5560115ccb6`이다.

보관된 이전 iPad 소스에 패치의 정확한 문맥을 적용한 격리 검토 사본을 만들었다. 준비 서비스, sender, 실제 SQLite 저장소, 설정 모델, AppEnvironment, 신규 시험과 V11 스키마를 포함한 **8개 파일의 최종 SHA256이 installation.json과 일치**했다. Xcode project.pbxproj는 전체 기준 파일이 없어 전체 해시는 독립 검증하지 못했으며, patch에서 신규 Swift 소스와 SQL 리소스 등록을 확인했다. Windows 제품 코드나 원래 iPad 소스 사본에는 패치를 적용하지 않았다.

확인한 구현 연결:

1. 준비는 `sync_contract_preparations`에 저장된다. 준비 함수는 일반/계약 송신 큐나 폴더/tree_order를 생성하는 함수를 호출하지 않는다. 반복 준비와 내보내기는 같은 저장 요청을 재사용한다.
2. 일반 claim은 preparations에 연결된 batch를 제외한다. 명시적 sender만 검토한 batch ID와 request SHA를 받아 해당 요청을 claim하고, 실제 transport 직전에도 저장 요청 해시를 대조한다.
3. 최초 tree_order=0 처리와 폴더/순서의 로컬 송신 큐 반영은 명시적 claim의 SQLite transaction 안에서 수행한다. 기존 일반 recorder의 revision>0 전제는 유지된다.
4. 준비·송신 모두 서버/로컬 구조 기준을 다시 확인한다. 명시적 송신에는 기존 C9 관문·계정·연결·handshake·작품 활성·구조 승인 검사와 새 영속 큐 사건 검사가 연결돼 있다. 이 승인들을 요청 해시에 넣거나 영속 쓰기 허가로 저장하지 않는다.
5. 설정의 검토 전송 함수는 성공/실패 종료 시 관문을 닫는다. 송신 큐에 한 번 들어간 준비를 폐기 기능으로 지우지 않으며 일반 sender의 자동 재시도에서도 제외한다.

신규 9개 Swift 시험의 실제 저장소/서비스/sender 연결과 단언을 읽었다. 반복 준비·재시작·내보내기 동일성, 일반 claim 제외, 같은 JSON 명시 송신, 기준/해시 변경 시 쓰기 0, 최종 C9 차단 후 요청 보존, 최종 경계 일반 enqueue 차단, 시도 후 폐기 거절, 설정 모델 복원, 다른 서버/사용자 정렬 거절을 확인한다.

제출된 원본 xcresult 요약은 **975개 / 통과 974 / 실패 0 / 건너뜀 1**이며 installation.json과 일치한다. Windows에서 Swift 회귀를 재실행한 것이 아니고, 기존 Windows 전체 시험과 합산하지 않는다. 읽은 소스·요약 범위에서 이번 기능 때문에 추가 제품 수정을 요구할 사항은 발견하지 않았다.

## 4. 설치 식별과 아직 남은 실행 조건

iPad 설치본은 base HEAD `1107b820cd21cc8784a9cc34e4b04f8189fedb19` 위 미커밋 준비 기능을 포함한다. 따라서 HEAD만으로 식별하지 않는다.

- WriterPad 제출 바이너리: `7ceacecc27e23c5a36c20627f3dbd496e22b636b8da5fecb39d12421e6af8453`.
- WriterPad.debug.dylib 제출 해시: `617371d89458c871bf59adb7e3070ed2c96e528541e8c26580f5e878aaab5c6a`.
- 위 패치 해시 및 파일별 해시, bundle `com.chocos.writerpad.debug`와 함께 식별한다.
- 바이너리 자체는 첨부되지 않아 Windows에서 재해시하지 않았다. 기기 실행 파일을 추출한 검증도 아니다. 설치/실행 및 실제 준비 1건·실제 배치 0건·관문 0개는 iPad 제출 기록으로 구분한다.

다음 단계는 같은 요청을 보존한 채 소스/설치 연결 기록을 고정하고, 실제 실행 직전의 읽기 전용 점검을 정리하는 것이다. 미커밋 변경을 커밋으로 고정한다면 위 패치·파일별 해시와의 연결을 남기면 된다. 내용 변경 없는 커밋 식별 정리만을 이유로 요청 재준비, 전체 회귀, 재빌드·재설치를 반복할 필요는 없다.

실제 실행 직전에는 iPad의 현재 로그인 권한·새 active/handshake·기기 ID·로컬 구조 지문·대기/blocked/conflict 없음, 양쪽 설치와 작품 연결 및 Windows의 동시 편집 중지를 다시 확인한다. 이번 17:40 서버 구조 일치를 미래의 송신 허가로 사용하지 않는다.

응답 유실 뒤에는 동일 요청이라고 바로 재전송하지 않는다. 서버에서 이미 적용됐다면 저장된 옛 구조 지문과 달라져 이 sender의 사전 검사가 재전송을 막을 수 있다. 먼저 batch/result를 읽는 절차와 필요한 조회 수단을 준비하고, 재전송은 사용자 승인 범위에 명시해야 한다. 저장된 영구 거절과 송신 전 로컬 차단도 구분한다. 이번에는 결과 조회/재전송용 실제 사용자 RPC를 실행하지 않았다.

## 5. 사용자에게 요청할 다음 행동

**이 Windows 검증 회신과 대조 근거 ZIP을 iPad 측에 보내고, 다음을 요청해 주세요.**

> batch `45af53f8-ec2c-46b9-bf84-b2d56857fe5c`와 request SHA256 `710ac3cf39a545639efd17e9740cf1fdbcfe06e27f0d9b4a72aa1ecfd6ce2ed7`은 Windows 대조를 통과했습니다. 같은 준비 요청을 보존하세요. 준비 버튼 재실행·폐기·일반 2권 생성·관문 개방·실제 전송은 하지 마세요. 검증된 준비 기능의 소스/설치 연결 식별을 고정하고, 실제 실행 직전 읽기 전용 점검 항목과 성공/거절/응답 유실 시 결과 확인 절차를 정리해 회신해 주세요. 완료된 수정·전체 회귀·설치를 내용 변경 없이 반복하지 마세요.

현재 사용자의 추가 앱 조작은 없다. 다음 자료를 받은 뒤 고정된 한 요청의 실제 수동 전송과 조회/재전송 범위를 구체적으로 제시한다. 이번 회신은 그 실행 승인이 아니다.

## 근거 파일

저장소 `_evidence/windows-prepared-request-review-20260906/`:

- `request-verification.json`, `verify_request.py`: 실제 요청 해시·두 작업·Windows builder 동일성.
- `server-verification.json`, `server-metadata.json`: 현재 서버 구조 지문과 미사용 ID 확인.
- `windows-verification.json`: 설치 해시·연결·관문·선별 로컬 구조 대조.
- `source-reconstruction.json`, `source/`: 설치 기록과 일치한 격리 소스 8개.
- `archive-verification.json`, `received/`: 전달 원문과 manifest 검증.

실제 계약 전송·재전송·관문 변경·서버 설정 변경·Windows 제품 수정·커밋·재빌드·재설치는 없었다. 로컬 검토 문서·선별 증거만 추가했다.
