# 시험 정렬 문서 UUID 교정 완료 — 2026-09-11

사용자 지시 `교정 승인`에 따라 Staging 서버와 Windows 실제 동기화 장부의 교정을 완료했다. 실제 iPad 재개와 원고 관찰은 아직 실행하지 않았다.

## 교정 결과

- 대상: `본문수신검증 20260911`, project UUID `9a78c51c-7de9-43a8-be54-d25a22d08a28`.
- 내부 경로: `__antigravity__/tree-order.json`.
- 잘못된 UUID `6df5660a-c1ae-492f-bb1d-26b582508074`를 UUIDv5 `ef6e1de1-a3d0-5959-96be-58f87a683cc0`으로 교정했다.
- 서버: documents 1행과 document_versions 1행의 document_id만 변경. 잘못된 UUID의 문서/버전은 0행이며 tombstone을 만들지 않았다.
- Windows: sync_documents 1행과 완료된 sync_operations 1행의 document_id만 변경. 작업 ID와 완료 상태, 본문, payload hash, 시각은 동일하다.
- 정렬 버전 ID `6e0ea926-3226-40a9-893f-454a2d323049`, 작업 ID `3691241c-9a6f-45ff-851b-75c3fc121179`를 보존했다.
- 정렬 내용 385 bytes/revision 1/SHA `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be` 보존.
- 일반 원고 UUID `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 93 bytes/revision 1/SHA `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d` 보존.
- 폴더 11개의 UUID·부모·이름·revision 1 보존. 서버의 원고 parent/name/structure_revision null 상태도 동일하다.

## 실행과 검증

사전 상태가 이전 검토 당시 설치 파일·AppData 파일 및 DB 전체 행/스키마와 일치함을 확인하고 DB/WAL/SHM의 새 백업을 만들었다. 서버 대상 문서·버전·폴더 원본은 비공개로 보존했다.

서버 SQL 실행본에서는 PL/pgSQL 지역 변수와 information_schema 컬럼의 이름 충돌을 피하도록 루프 변수 이름을 명확히 했고 CASE 비교에 괄호를 넣었다. 승인한 변경 대상과 절차는 그대로다. 단일 SERIALIZABLE 트랜잭션이 성공했고 별도 읽기로 commit 결과를 확인했다. 쓰기 호출 1회이며 재시도하지 않았다. 서버 제약/트리거/RLS/스키마 변경은 없다.

서버 트랜잭션 내부에서 public 15개 테이블 전체 행을 대조했다. 후속 별도 읽기에서도 다른 13개 테이블의 전후 지문이 동일하고, 대상 문서/버전은 document_id 이외 모든 필드가 동일했다.

Windows 실제 장부에서도 독점 트랜잭션을 완료했다. 원래 변경 방지 트리거를 복구했으며, 30개 테이블의 전후 행/스키마를 비교해 document_id 두 필드 이외 차이가 없음을 확인했다. integrity 정상, FK 위반 0. 설치 디렉터리의 모든 파일과 AppData의 DB/sidecar 이외 모든 파일은 바이트·mtime이 동일하다. 관문과 hold 설정도 동일하다. 실제 Windows 앱은 계속 종료 상태다. iPad의 현재 상태를 별도로 조회하거나 조작하지 않았다.

로컬 DB 의미적 지문:
- 교정 전 `e3e3ddc0d5af03d117b922ee2241d577c939afd9b95ec0cd6c7f3e6836c8eb6e`
- 교정 후 `e45d701afc399a89d4f095b470473186eff2c5a9420f78faa5aa70665c112df6`

## iPad에서 이어갈 일

이 결과는 서버와 Windows 교정 완료 증거다. **실제 iPad 재개·설치·marker 정리·송신·관문 개방 승인은 아니다.**

1. 결과 ZIP의 manifest와 `target-baseline.json`을 대조한다. 정렬 문서는 새 UUID 하나뿐이고 revision/content가 그대로인 기준이다. 기존 journal의 작품 UUID·로컬 이름·transaction/owner는 유지한다. 새 작품 생성·UUID 재발급·binding 재부착을 하지 않는다.
2. 이전 `xcresult_summary_sha256` 불일치는 보존된 원시 요약/공유용 요약을 읽어 해시 출처만 보완한다. 요청 문서를 이 ZIP에 함께 넣었다. 빌드/시험/기기 검사를 반복하지 않는다.
3. 해시 보완과 기준 대조가 끝나면 **iPad 작업에서 같은 journal의 수신 재개 1회 범위를 사용자에게 제시**한다. Windows 사전 검토 회신을 다시 기다리는 단계는 추가하지 않는다. 이번 교정 승인을 iPad 실행 승인으로 해석하지 않는다.
4. 그 재개가 승인되면 기존 후보·서명 유효성·송신 잠금·동일 계정/Staging/대상 UUID·중단 상태를 확인하고 한 번 재개한다. 서명 만료 기록은 2026-09-12 18:19:25 KST다. 만료/신뢰 오류가 있으면 자동 재설치하지 않는다. 예상 밖 프로세스 상태가 나오면 반복 실행/종료하지 않는다.
5. 성공 후 오프라인에서 본문 3줄을 편집 없이 관찰한다. 종료 후 보존본에서 journal 0/공개 작품 1/정상 내부 baseline 1/대상 미완료 큐 0/원고·폴더 보존을 확인한다. 기존 잘못된 marker 한 파일은 남는 것이 예상값이며 삭제하지 않는다.

현재 사용자는 이 결과 ZIP을 iPad 작업에 전달하고 양쪽 앱의 종료 상태를 유지하면 된다. 다음 실제 조작은 iPad 작업에서 안내한다.

## 증거와 범위

증거: `_evidence/windows-control-document-repair-20260911/`
공유 ZIP은 계정/기기 식별자·토큰·원본 DB·실행 바이너리를 포함하지 않는다. 일반 원고는 승인된 합성 시험용 텍스트뿐이다.

이전 잘못된 초기 업로드 증거와 교정 전 백업은 그대로 보존한다. 과거 성공 보고를 덮어쓰지 않는다. 설치, 일반 원고 송신, iPad 실제 수신, marker 삭제, 관문 개방, prod 전환은 이번에 수행하지 않았다.
