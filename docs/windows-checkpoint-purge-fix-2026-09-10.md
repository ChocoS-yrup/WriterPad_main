# iPad 추가 검토 반영 — 작품 영구 삭제 시 관측 이력 정리

2026-09-10. iPad가 지적한 추가 누락 1건을 재현·수정했고 관련 격리 검사 5개가 통과했다. 앞서 완료한 로컬 관측 갱신·송신 보류 해제 구현을 다시 만들거나 130개 전체 검사를 반복하지 않았다.

## 회신 대조

- 수신 ZIP SHA256: `664f342178206dc36ff609236575890477a238e85e0cce6a888b0889f49f504e`.
- CRC, manifest 6개 파일의 크기/해시, 중복·추가·경로 이탈·link 부재를 확인했다. 첨부 패치와 시험 코드를 읽은 후 적용했다.
- 제안 패치 기준 store SHA256 `6da6f94c6ba7575c68d4cb2f6cdd9c53e26033630a6ea0461e162bf3b649efdc`가 현재 수정 전 파일과 일치했다.
- iPad는 앞선 두 잔여 기능을 소스에서 수용했다. 첨부의 후속 설치 순서는 기존 사용자 금지 범위를 변경하는 승인으로 취급하지 않았다.

## 수정과 검증

`sync_server_checkpoint_observations`에는 프로젝트 FK/cascade가 없어서, 작품 영구 삭제 뒤 해당 UUID·local_key·관측 이력이 남았다. 수정 전 Windows 합성 fixture에서도 1행이 남는 실패를 재현했다.

`sync_v2_store.py`의 기존 `purge_project_records()` transaction 및 purge guard 안에 해당 local_key의 관측 이력 삭제를 추가했다. 평상시 이력 삭제 금지, 다른 작품 이력 보존, 실패 시 전체 롤백을 유지한다. 로컬 스키마는 **8013 그대로**이며 서버 스키마나 계약은 바꾸지 않았다.

| 검사 | 결과 |
|---|---|
| 대상 작품 영구 삭제 시 해당 관측 이력만 삭제 | 통과 |
| 평상시 관측 이력 삭제 금지 | 통과 |
| 후속 프로젝트 삭제 실패 시 프로젝트·관측 복원 및 purge guard 정리 | 통과 |
| 기존 purge의 append-only 보호 개폐 | 통과 |
| 기존 purge의 다른 작품 보존 | 통과 |

첨부 시험 파일은 검토한 그대로 `tests/test_checkpoint_purge_review.py`에 배치했다. 새 3개는 기존 합성 fixture의 소켓 차단을 유지한다. 관련 기존 purge 2개도 임시 DB에서 실행했다. 실제 작품 삭제나 실제 DB 수정은 하지 않았다.

원시 로그는 `before-tests.log`(수정 전 3개 중 1개 실패)와 `final-tests.log`(수정 후 관련 5개 통과)에 보존했다. 이전 130개 결과와 이번 5개 실행 결과를 구분한다.

## 갱신한 소스 후보 식별

`source-manifest.json`은 이전 선택 소스 15개 중 store의 해시를 갱신하고 새 시험 파일을 더한 16개 목록이다. 나머지 14개 파일이 이전 manifest와 일치하는 것도 확인했다. 설치 파일이나 전체 프로그램 독립 배포본은 아니다. 기존 repository와 선행 R1~R3 변경이 있는 작업 트리를 전제로 한다.

후보 digest의 재계산 규칙을 명시한다. manifest의 `files` 배열 순서를 그대로 유지하고, Python 표준 JSON 직렬화 후 UTF-8 bytes에 SHA256을 적용한다.

```python
payload = json.dumps(manifest["files"], sort_keys=True,
                     separators=(",", ":"), ensure_ascii=True).encode("utf-8")
candidate_sha256 = hashlib.sha256(payload).hexdigest()
```

별도 빌드 EXE/설치본 hash, 실제 앱의 새 handshake·관문·보류 해제·송수신 관측은 없다. 계측하지 않은 runtime 값은 null이며, 디스크/합성 검사 결과를 설치 앱의 완료로 대체하지 않는다.

## 지금 할 일과 다음 순서

1. **사용자:** 결과 ZIP을 iPad 작업에 전달하면 된다. 이번에는 작은 purge 수정과 그 검사 결과만 확인하면 충분하다. 양쪽 앱에서 누를 버튼은 없다.
2. **iPad:** 기존 두 기능 수용 결과를 유지하고 이번 추가 수정 결과를 대조한다. 제품 코드·서버 migration·이전 기기 검증을 반복할 필요는 없다. 전역 동기화도 지금 켜지 않는다.
3. **Windows:** 다음 단계는 검토 가능한 EXE 후보 빌드와 설치 전 자료 보존·8013 업그레이드 확인 준비다. 실제 설치·수신·관문 개방·보류 해제·한 방향씩 합성 원고 교차 검증은 해당 범위가 허용된 뒤 진행한다.

이번 작업에서 빌드·설치·실제 DB 변경·서버 송신·관문 개방·실제 보류 해제·prod 전환·커밋·푸시는 하지 않았다. 실제 실행 보류의 근거는 사용자님의 기존 설치·송신·관문 개방·prod 전환 금지다.
