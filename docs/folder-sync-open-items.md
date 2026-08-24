# 폴더 동기화 — 미해결 항목

두 클라이언트가 같이 읽어야 하는 목록이다. 합의된 것과 아직 아닌 것을 갈라
적는다. 단계 번호는 쓰지 않는다. 항목은 이름으로 부른다.

**이 파일은 지금 `WriterPad_main` 에만 있다 — 2026-08-25 확인.** iPad 저장소의
`docs/` 에는 없다. 그러니 여기 적은 것을 iPad 가 읽고 있다고 가정하면 안 된다.
아래 "계약 pin" 의 오기가 한쪽에만 있어서 아무도 대조하지 못한 것이 그 결과다.
같은 경로로 양쪽에 두고 갱신할 때 양쪽 커밋하는 것이 합의된 방향이며, iPad 쪽
파일은 아직 만들지 않았다.

기준 시점의 Windows 커밋:

```
fix: stop retrying folder refusals that no wait can change
fix: give an imported project the ids the server already holds
fix: drop identity entries when the trash is emptied
fix: follow a folder another device pulled back out of the trash
fix: follow a folder another device moved to the trash
fix: tell the server when a folder goes to the trash
feat: publish created folders to the server, not only renamed ones
fix: move identity with a renamed or dragged binder item
feat: let the local identity file issue every sync UUID
```

브랜치는 `codex/windows-stage4a-local-uuid-identity` 다. 정확한 SHA 는
`git log --oneline` 으로 읽어라.

기준 시점의 iPad 커밋:

```
ad37c7dc7c983142ca2fc4fc3fa31ebb0746d58d  나가는 쪽 굳음 화면 표시
aaee856123edc1aa5e295503db63f679f029d9cf  폴더 기준선 재설정
5d39b3b1834ee6590ac645d9df9bef71d6f00c7f  굳은 폴더 재측정 (시험만, 제품 변경 0)
50e4fc213f7ccfc2230e195cc29cddb8034f438b  폴더 거부 표시
6ab898abe63ec11f18cc8223756b2a609ea80451  폴더 에러 코드 분류
afb96bee0773d4961f9bad1fea76321eeb71a2bf  폴더 tombstone 순서, 최초 휴지통 제외
  브랜치 codex/ipad-outbound-stall-visibility 가 끝, 아래로 쌓여 있다
  PR #16 ← #15 ← #14 ← #13 ← #12, 전부 draft — merge 하지 않는다
  merge 는 실서버 종단간 검증 뒤에 양쪽을 함께 정리한다
```

---

## 지금 서 있는 자리

서버에 배포된 RPC는 `ensure_project`, `commit_document`, `commit_folder`,
편집 리스 계열, 프로젝트 휴지통 계열뿐이다. `atomic_structure_commit` 과
`document_commit` 은 **서버에 없다.** 양쪽 클라이언트 모두 호출하지 않는다는
것을 확인했다. 호출하는 코드가 Windows 에 남아 있지만 제품 경로에서 도달하지
않는다.

폴더는 `folder_id` + `parent_folder_id` 로 서버에 있고, 문서는 `document_id` 와
`relative_path` 로 있다. 문서에는 부모나 순서 컬럼이 없다. 형제 순서는
`__antigravity__/tree-order.json` 이라는 클라이언트 전용 문서가 담는다.

---

## 합의된 것 — 다시 논의하지 않는다

**휴지통 안의 폴더는 최초 업로드에서 보내지 않는다.**
서버 `commit_folder` 는 `base_revision = 0` 에 `p_is_deleted = true` 를 받으면
`INVALID_ARGUMENT` 다. 없는 폴더를 삭제 상태로 만들어 주지 않는다. 따라서 최초
연결 전에 이미 휴지통에 있던 폴더는 서버로 가지 않는다. `메인/휴지통` 고정
폴더 자체는 live 로 올린다.

그 결과로 **휴지통 안의 폴더 껍데기는 기기 간에 공유되지 않는다.** 문서는
`is_deleted` tombstone 으로 양쪽 휴지통에 나타나지만, 그 문서를 담고 있던 폴더는
받는 쪽 휴지통에 재현되지 않는다. 원위치 자동 복원이 v1 범위 밖인 것과 같은
이유다. 휴지통 항목의 완전한 구조는 백업·복원 축이 보존한다. identity 가
`uuid` + `parent_uuid` + `order` 를 그대로 들고 있고, 교차 검증에서 휴지통 항목의
UUID·parent·order·bytes·SHA-256 이 모두 일치했다.

**폴더 삭제 커밋은 가장 깊은 자식부터 보낸다.**
서버는 live 하위 **폴더**가 남아 있으면 `FOLDER_NOT_EMPTY` 로 거절한다. 하위
문서는 세지 않는다. 생성·이동·이름변경은 반대로 부모 우선이다.

**같은 폴더를 양쪽이 바꾸면 늦게 커밋한 쪽이 이긴다.**
last-write-wins 다. 진 쪽은 pull 로 상대 이름을 따라가고, 그쪽 사용자가 다시
바꾸지 않으면 그대로 수렴한다. **별도 충돌 화면이나 사용자 선택 흐름을 만들지
않는다.** 작가 한 사람이 두 기기를 번갈아 쓰는 프로그램이라 "누가 옳은가" 분쟁이
없고, 마지막에 만진 것이 최신 의도다.

Windows 는 이미 그렇게 동작한다. 조정 루프는 살아 있는 서버 폴더를 만나면 이름을
비교조차 하지 않고 넘어가고, 이름 변경은 durable 한 rename intent 가 있어야만
발동한다. 즉 사용자가 실제로 바꿨을 때만 밀어 넣는다.

**문서 부모 컬럼을 서버에 추가한다.**
`public.documents` 에 `parent_folder_id uuid` 를 넣고, 경로 유니크 인덱스를
`(project_id, parent_folder_id, 이름)` 유니크로 바꾸고, `commit_document` 에
`p_parent_folder_id` 를 더한다. `order_index` 컬럼은 만들지 않는다. 형제 순서는
`tree_order` 가 계속 담당한다.

- `parent_folder_id` = 문서 트리 위치의 authoritative 값
- `relative_path` = 과도기 호환 및 무결성 검사용
- 두 값 불일치 = 조용히 한쪽을 신뢰하지 말고 명시적 오류

착수 전이다. 서버 마이그레이션과 양쪽 클라이언트가 함께 움직여야 한다.

---

## Windows 쪽 — 해결된 것

**폴더 삭제·복원의 양방향 — 해결됨**

Windows 는 폴더 투영을 live 행으로만 풀었기 때문에, 다른 기기가 지운 폴더가 여기서
보이지 않았다. 안에 있던 문서는 tombstone 으로 와서 각자 휴지통으로 들어가고
디렉터리만 빈 채로 남았다. iPad 쪽에 빈 폴더를 남겼던 결함의 거울상이다.

이제 양방향 모두 따라간다.

- 원격이 지운 폴더는 로컬에서 **휴지통으로 옮긴다.** 지우지 않으므로 안에 남은
  추적 밖 파일과 아직 안 올린 문서의 바이트가 보존된다. 얕은 것부터 처리하고,
  identity 가 따라가므로 UUID 가 유지된다.
- 원격이 복원한 폴더는 로컬에서도 휴지통에서 꺼낸다. 목적지 부모는 서버 행의
  `parent_folder_id` 에서 얻는다. 로컬 휴지통 색인은 이 기기가 마지막으로 본 위치만
  알기 때문이다.

복원을 따라가는 것이 특히 중요하다. 따라가지 않으면 바깥으로 내보내는 쪽이 identity 를
읽고 "아직 휴지통에 있다" 고 판단해 삭제를 다시 발행하고, 상대 기기의 복원을 조용히
되돌린다.

열려 있는 문서가 그 폴더 아래 있거나, 그 아래 문서에 아직 보내지 않은 작업이
남아 있으면 폴더를 건드리지 않는다. 복원 목적지가 이미 차 있으면
`RESTORE_TARGET_TAKEN` 으로 보고하고 휴지통에 그대로 둔다.

**가져오기 프로젝트의 identity 정렬 — 해결됨**

가져오기는 표준 폴더에 새 UUID 를 발급하고 pull 이 받아온 문서는 identity 에 아예
기록하지 않았다. 프로젝트를 여는 경로가 identity 와 파일 트리를 대조하므로 그
프로젝트는 **열리지 않았다.** 폴더도 어긋나 있었다. 같은 폴더를 서버는 상대 기기가
발급한 id 로 이미 들고 있어서, 폴더 발행이 영원히 이름 충돌로 보고만 했다.

이제 pull 이 끝난 뒤 identity 를 서버 id 로 다시 세운다. 프로젝트 uuid 는 서버
`project_id`, 폴더는 서버 `folder_id`, 문서는 서버 `document_id` 를 그대로 쓴다.
서버가 모르는 경로만 이 기기가 발급한 id 를 유지하므로, 중단된 가져오기를 재개해도
UUID 가 다시 발급되지 않는다. 계획이 모호하면 쓰지 않고 거부한다.

형제 순서는 서버 `tree_order` 를 따르고, 표준 폴더 아홉은 힌트가 없거나 부분적이어도
양쪽이 공유하는 배열(원고·캐릭터·설정집·메모장·스토리 플롯·흐름정리·복선·장소·휴지통)
을 유지한다.

이 재작성은 **가져오기 거래 안에서만** 안전하다. 방금 만들어진 프로젝트라 그 id 를
아직 아무도 참조하지 않았기 때문이다. 사용자가 작업해 온 프로젝트에는 절대 쓰지
않는다.

**폴더 에러 코드 분류 — 해결됨**

`_stable_error_code` 가 서버의 폴더 코드 **여섯 개를 모두** 몰랐다. 전부 빈
문자열로 떨어지고 마지막 폴백이 `{"kind": "retry"}` 라, 기다려도 바뀌지 않는
거절을 60 초마다 영원히 다시 보냈다. iPad 가 셋을 놓쳤다면 여기는 여섯을 다
놓쳤다.

여기가 더 나빴던 이유는 폴더 발행이 tree-order 커밋 안에 실려 있기 때문이다.
거절된 폴더 하나가 형제 순서 동기화 전체를 끌고 내려가 그것까지 다시 보냈다.

이제 기다림으로 바뀌지 않는 거절은 한 줄 보고하고 건너뛰며, 그 pass 는 처리할 수
있는 나머지 폴더를 계속 처리한다. 연결 끊김이나 세션 만료는 예전대로 올려 보내
재시도한다. 이름 변경 의도는 일부러 pending 으로 남긴다. 사용자의 rename 이라
여기서 버리면 조용히 사라진다.

`PARENT_FOLDER_NOT_FOUND` 는 `FOLDER_NOT_FOUND` 보다 **앞에** 두어야 한다. 코드
대조가 부분 문자열이고 짧은 쪽이 긴 쪽 안에 들어 있다.

**굳은 폴더 작업 — 여기에는 없다**

iPad 가 겪은 굳음이 Windows 에도 있는지 물어와 확인했다. 없다. 다른 기기가 폴더
이름을 바꿔 서버 revision 이 앞서 나간 상태를 만들고 다시 발행해도 그대로
진행된다.

구조가 다르기 때문이다. **폴더 발행이 대기열이 아니라 조정 루프다.**

```
매 pass 마다
  identity 를 읽어 "있어야 할 상태" 를 만들고
  서버 폴더 투영을 새로 읽어 "지금 상태" 를 만들고
  둘의 차이만 보낸다 — base_revision 은 방금 읽은 값
```

`base_revision` 이 operation 에 박히지 않으므로, 서버가 앞서 나가도 다음 pass 가
새 값으로 읽어 맞춘다. iPad 가 굳는 원인인 "박제된 base_revision" 이 여기에는
존재하지 않는다. 이름 변경 경로도 같은 자리에서 폴더 투영을 새로 읽는다.

**같은 질문을 다시 하지 않도록 적어둔다.** 이것은 이 구조가 유지되는 동안만
참이다. 폴더 발행을 큐로 바꾸면 같은 구멍이 생긴다.

**영구 삭제가 identity 노드를 남긴다 — 해결됨**

`delete_from_trash` 와 `empty_trash` 가 파일만 지우고 identity 노드를 남겨서,
**휴지통을 비우면 그 프로젝트가 다음에 열리지 않았다.** 원고는 디스크에 그대로
있는데 열 수가 없는 상태였다.

영구 삭제는 UUID 가 정당하게 사라지는 유일한 경우이므로 다른 identity 변경과 같은
journal 거래로 처리한다. 자손도 함께 지운다. 중단되면 파일이 실제로 사라졌는지 보고
판단하고, 삭제가 실패했으면 파일과 노드를 둘 다 남긴다.

서버 쪽 영구 삭제는 `__antigravity__/trash-purge.json` 이 담당하는 별도 경로이고
여기서 건드리지 않는다.

---

## iPad 쪽 — 해결된 것

`afb96be` 와 그 부모 `cf69d17` 에서 확인했다. Windows 가 커밋 원문을 직접 읽고
대조한 결과다.

**폴더 operation 순서 — 해결됨**

정렬은 원래 맞았고 dispatcher 가 같은 batch 의 폴더를 병렬 전송해 도착 순서를
뒤집고 있었다. 이제 폴더 줄만 claim 순서대로 직렬로 비우고 문서 줄은 계속
병렬로 흐른다. 삭제뿐 아니라 **생성·복원까지 함께 보호된다** — 병렬 전송은
자식이 부모보다 먼저 닿아 `PARENT_FOLDER_NOT_FOUND` 를 내는 경로이기도 했다.

3 단 중첩을 부모 우선으로 enqueue 해도 완료 순서가 `C → B → A` 만 허용되는
시험이 있다.

**폴더 복원 발행 — 원래 되어 있었다**

같은 UUID 와 부모 id 를 유지하며 `is_deleted = false` 를 발행한다. 얕은 것부터
보낸다.

**최초 스냅샷의 휴지통 하위 폴더 — 해결됨**

최초 스냅샷을 live 노드에서만 만든다. `메인/휴지통` 고정 폴더 자체는 live 로
올리고 그 아래 폴더 operation 은 0 건이다. 폴더 노드가 컬렉션에 직접 들어 있어
빈 폴더는 그대로 살아남는다.

**빈 폴더와 `tree_order` — 정상**

빈 폴더도 부모의 자식 이름 배열에 들어간다. 예: `"메인/설정집": ["빈 폴더"]`.
Windows 의 pull 보류 조건에 걸리지 않는다.

**폴더 에러 코드 분류 — 해결됨**

`PARENT_FOLDER_NOT_FOUND`, `FOLDER_NAME_CONFLICT`, `FOLDER_CYCLE` 이 client
enum 에 없어 `.serverRejected` 로 떨어지고 무조건 재시도였다. 재시도 상한 5 분에
횟수 제한이 없어, 이름이 겹친 폴더 생성처럼 사람이 개입해야 풀리는 상태를 영원히
다시 보냈다. 세 코드를 넣고 conflict 로 분류했다.

`FOLDER_NOT_EMPTY`, `FOLDER_ALREADY_EXISTS`, `FOLDER_NOT_FOUND` 는 원래 있었다.
이제 여섯 개가 모두 자동 재시도에서 빠진다. 진단에서 operation id 는 뺐다. 넣으면
같은 조작을 다시 시도할 때마다 하나의 상태가 여러 사건으로 보인다.

**폴더 거부 표시 — 해결됨**

거부가 화면에서 두 가지로 어긋나 있었다. 문서 오류가 함께 난 pull 에서는 폴더
거부가 *이름을 고치라* 는 안내문 틀에 끼워져 **사용자가 틀린 지시를 받았고**,
거부만 있었던 pull 은 아무 일 없었던 것처럼 끝났다.

거부 항목에 종류를 달아 갈랐다. 이름을 고치면 풀리는 것만 그 안내문에 들어가고,
나머지는 사실대로 적은 전용 상태로 간다. 그 상태는 실패가 아니고 재시도 버튼도
달지 않는다. 눌러도 바뀌지 않기 때문이다. 분기는 `.waiting` **앞**에 둔다.
`.waiting` 은 지나가는 상태라 뒤에 두면 적용하지 않은 항목이 다시 가려진다.

이름 안내를 받는 자리는 폴더 거부 8 곳 중 2 곳(`isNameAllowed` 실패)과 문서 쪽
1 곳뿐이다. 나머지는 보낸 기기에서 이름을 고쳐도 풀리지 않는다.

**나가는 쪽 굳음 화면 표시 — 해결됨**

받는 쪽 거부는 위에서 보이게 했지만, **내 변경을 서버에 못 올린 상태**는 여전히
조용했다. pull 이 끝나 화면 상태를 정하기 직전에 대기열에서 세워 둔 폴더 변경을
읽어 올린다.

`notApplied` 를 재사용하지 않고 `notPublished` 를 새로 뒀다. 저쪽은 서버 것을 안
받은 것이고 이쪽은 내 것을 못 보낸 것이라 **방향이 반대**다. 같은 문장으로 말하면
둘 다 틀린다. 문구는 `"서버에 못 올린 변경 있음"`, `.warning`, 재시도 버튼 없음.

**실서버 검증 때 iPad 화면에 뜨는 두 문구는 실패가 아니라 정상 동작이다.**

```
"적용하지 않은 항목 있음"    받는 쪽 — 서버 변경을 로컬에 적용하지 않았다
"서버에 못 올린 변경 있음"    나가는 쪽 — 로컬 변경이 서버로 못 갔다
```

둘 다 원고 바이트와 무관하다. 어느 쪽이 뜨든 그 폴더 가지만의 이야기다.

---

## 합의된 차이 — 맞추지 않기로 한 것

같은 결과를 다른 방식으로 얻는다. 양쪽 다 바이트를 잃지 않으므로 통일 비용이
이득보다 크다고 판단했다.

**원격 폴더 tombstone 을 로컬에서 처리하는 방식**

| | Windows | iPad |
|---|---|---|
| 빈 폴더 | 휴지통으로 이동 | 디스크·메타데이터에서 제거 |
| 내용이 남은 폴더 | 휴지통으로 이동 (바이트 보존) | 삭제 거부 |
| 다시 live | 휴지통에서 꺼냄 | 서버 UUID 로 재생성 |

문서가 먼저 tombstone 되어 각자 휴지통으로 빠지므로 대개 같은 상태로 수렴한다.
갈리는 경우는 하나다. **한 번도 동기화된 적 없는 파일이 그 폴더에 남아 있을 때**
iPad 는 폴더를 제자리에 두고 Windows 는 휴지통에 둔다. 트리 모양은 달라지지만
원고는 양쪽 다 온전하다.

**막지 않는 것을 확인했다.** 거부는 대기열이 아니라 pull 적용부에 있고 아무것도
enqueue 하지 않는다. 매 pull 마다 서버 폴더 목록에서 새로 계산되므로 남은 파일이
정리되면 그다음 pull 에서 자연히 지워진다. 폴더 operation 을 만드는 곳은 사용자
조작과 최초 스냅샷뿐이라 거부된 폴더가 서버로 되살아나는 되돌이도 없다.

보이지 않던 문제는 "폴더 거부 표시" 에서 해결됐다. 거부는 이제 사실대로 화면에
나온다.

**identity 의 프로젝트 UUID**

Windows 는 서버 프로젝트를 가져올 때 identity 의 project uuid 를 서버
`project_id` 로 채택한다. iPad 는 로컬 `ProjectID` 를 유지한다. iPad 의
`.windowsImport` 는 서버에서 내려받는 경로가 아니라 로컬 스냅샷을 빈 서버
프로젝트에 올리는 경로라 성격 자체가 다르다.

동기화는 `sync_projects.project_id`(서버 값)를 쓰고 identity 의 project uuid 를
읽지 않는다. 백업이 플랫폼을 건너갈 때는 manifest 값이 정본이라 스스로 맞는다.
그래서 맞추지 않는다.

---

## iPad 에 남은 것

**(b) 굳은 폴더 작업 — 동시 이름 변경 경로는 닫혔고, 영구 거절 경로는 열려 있다**

굳는 성질부터 적는다. claim 이 끝난 것으로 보는 상태는 `completed` 와 `cancelled`
둘뿐이라 `conflict` 는 잠근다. 재시도에서 conflict 로 옮긴 것은 서버로 나가는
트래픽을 멈춘 것이지 잠금을 푼 것이 아니었다.

**잠금은 두 겹이다.** claim 조건 말고도, 앞 작업이 살아 있으면 뒤 작업의
`base_revision` 이 NULL 로 들어가고 claim 은 그것이 NULL 이 아닐 것을 요구한다.
NULL 은 앞 작업이 **성공할 때만** 채워진다. claim 조건만 고쳐서는 안 풀린다.

굳으면 그 폴더의 이후 모든 작업, 조상 폴더의 삭제, 그리고 그 폴더로 **들어오는**
원격 변경까지 멎는다. 셋 다 재현됐다. 원고 바이트는 안전하고 그 가지의 구조
동기화만 선다. 사용자가 스스로 풀 수 없다 — 이름을 다시 바꾸거나, 폴더를 지우거나,
앱을 껐다 켜거나, 재시도 버튼을 눌러도 풀리지 않는다. `cancelled` 를 쓰는 네 곳이
전부 `document_id` 기준이라 폴더 행(그 값이 NULL)에는 닿지 않는다.

**닫힌 것 — 동시 이름 변경**

두 기기가 같은 폴더 이름을 앞뒤로 바꾸면 늦은 쪽이 `REVISION_CONFLICT` 로 굳었다.
문서는 이 코드에서 자동 rebase 를 타지만 폴더 전송 경로는 그 분기를 지나지 않았다.
재측정에서 **제품 조작으로 닿는 것이 확인된 유일한 경로**였고, 이제 닫혔다.

서버가 이미 답을 주고 있었다. `commit_folder` 는 `REVISION_CONFLICT` 를 낼 때
detail 에 `current_revision` 을 싣는데 아무도 읽지 않았다. 그 값으로 기준선만
옮기므로 **서버에 다시 묻지 않는다.** claim 질의는 그대로다. 두 번째 겹도 앞
작업이 실제로 성공하므로 자연히 풀린다. 되풀이 방지로 서버 revision 이 현재
기준선보다 클 때만 옮긴다.

이로써 iPad 도 last-write-wins 로 수렴한다.

**열려 있는 것 — 영구 거절**

`FOLDER_NAME_CONFLICT`, `PARENT_FOLDER_NOT_FOUND`, `FOLDER_CYCLE` 등은 여전히
굳고, 굳으면 위의 잠금이 그대로다. `FOLDER_ALREADY_EXISTS` 도 같은 성질의 기준선
문제로 보이나 not-verified 다. 이 코드들에 제품 조작으로 닿는 빈도도 재지 않았다.

**여기서 양쪽이 갈린다.** Windows 는 같은 거절을 만나면 한 줄 보고하고 그 폴더만
건너뛰며 나머지를 계속 처리한다. iPad 는 그 줄이 선다. 고친다면 Windows 와 같은
모양 — 보고하고 건너뛰기 — 이 되겠지만, iPad 에서는 그것이 claim 영역이라 순서
보장의 뿌리를 건드린다. 그래서 승인 없이 진행하지 않는다.

**굳음 자체는 남았지만 이제 보인다.** iPad 화면에 `"서버에 못 올린 변경 있음"`
(주황)이 뜬다. 그 기기의 폴더 조작이 서버에 반영되지 않았다는 뜻이고, 로그를
뒤지지 않아도 알 수 있다. 사용자가 푸는 수단은 여전히 없다 — 화면은 상태를 말할
뿐이다.

**실서버 검증에서 폴더 줄이 서면 그것은 검증 실패가 아니라 이 항목의 재현이다.**
그 폴더 가지만 멎고 나머지는 흐른다. 화면에 그 문구가 뜨면 그대로 기록하면 된다.

**최초 batch 의 `ensure_project` bookkeeping — 미해결**

초기 batch 에 `ensure_project` 행을 기록하지만 dispatcher 가 claim 하지 않아
pending 으로 남는다. 실제 `ensure_project` 는 연결 직전에 따로 호출되므로
프로젝트 생성은 되고, batch 상태만 완료되지 않는다. 원고 손실 위험은 없다.
다른 작업과 같은 커밋에 섞지 않는다.

---

## Windows 에 남은 것

**문서 커밋 경로도 알 수 없는 코드를 무한 재시도한다 — 미해결, 급하지 않음**

폴더 경로는 고쳤지만 `_process_v2_operation` 의 마지막 폴백이 여전히
`{"kind": "retry"}` 다. `_stable_error_code` 가 아는 코드여도 그렇다. 문서
커밋에서 영구 거절이 나오면 같은 성질의 무한 재시도가 된다.

폴더만큼 급하지 않아 이번 범위에 넣지 않았다. 실제 도달 경로를 먼저 확인해야
한다.

---

## 계약 pin — 기록만 해둔다

**계약 pin 은 양쪽이 같다 — 아래 기재가 틀렸었다, 2026-08-25 정정**

```
Windows  contract_version 0.2.0   lock 0.2.0 released · CHANGELOG 최신 0.2.0
iPad     contract_version 0.2.0   lock 0.2.0 released · CHANGELOG 최신 0.2.0
```

정정 전에는 iPad 를 0.3.0 으로, 양쪽이 다른 것으로 적어 두었다. 사실이 아니다.
iPad 저장소는 코드도 `sync-contract/contract-lock.json` 도 `CHANGELOG.md` 도
전부 0.2.0 이고, 0.3.0 산출물은 병합된 적 없는 브랜치 `becbf42` 에만 있다.
미병합 브랜치를 저장소 상태로 읽은 기재였다.

**게다가 방향이 반대였다.** 잠든 0.3.0 산출물을 실제로 들고 있는 쪽은 Windows 다
— 바로 아래 표가 그것이고, 한 문서가 스스로 앞뒤로 어긋나 있었다. 이 기재를
근거로 "iPad 는 이미 0.3.0 이니 서버만 따라오면 된다" 로 읽으면 틀린 판단이
나온다.

**동작 차이는 지금 0 이다.** 계약을 검증하는 경로는 Windows 의
`_uses_contract_structure()` 뒤에 있고 제품에서 한 번도 True 가 되지 않는다.
양쪽이 실제로 부르는 RPC 는 `ensure_project`, `commit_document`, `commit_folder`
와 리스·휴지통 계열뿐이며 계약 배치를 쓰지 않는다.

Windows 활성 트리는 내부적으로 일관된다. 확인한 결과는 이렇다.

```
normalize_storage_name      (v1)   제품 호출처 있음  ← 실제로 쓰는 것
normalize_storage_name_v2          제품 호출처 0 건  ← 시험에서만
storage_name_tables.py             v2 경로에만 물려 있어 함께 잠들어 있다
```

즉 **Windows 활성 트리에** 0.3.0 산출물이 미리 들어와 있되 켜지지 않은 상태다.

아래 문장이 0.3.0 CHANGELOG 의 배포 경계로 인용되어 있었다.

> Clients must retain the 0.2.0 pin until the server stage deploys
> storage-name-v2 and allowlists this release digest.

**출처 미확인 — 2026-08-25.** 이 문장은 양쪽 저장소의
`sync-contract/CHANGELOG.md` 어디에도 없다. 두 파일 모두 최신 항목이
`## 0.2.0 - 2026-08-11` 이고, 이 기계 전체에 `CHANGELOG.md` 는 그 하나뿐이다.
이 문서 안의 인용으로만 존재하므로 **근거로 쓰지 마라.** 지우지 않고 남기는
이유는 이것을 근거로 삼은 판단이 이미 나왔기 때문이다. 0.3.0 CHANGELOG 의
실물을 찾기 전까지 배포 경계에 대해 아는 것은 없다.

**아직 모르는 것.** 계약 트랙(`sync_contract_0_1_0_*`, `0_3_0_storage_name_v2`
마이그레이션 줄기)이 실제 서버에 적용된 적이 있는지 이 저장소에서는 판정할 수
없다. 활성 `supabase/migrations/` 는 그와 겹치지 않는 다른 줄기다.

**출처 주의.** 0.3.0 산출물이 `_사용안함_과거테스트흔적_20260815/` 안에도 있지만
그 폴더는 시험하다 늘어난 작업 폴더를 치워둔 잔재이고, `_forbidden` 이 붙은 것은
쓰지 말라는 표시다. **거기서 활성 트리로 무엇도 가져오지 마라.**

**결정.** 어느 쪽 pin 도 지금 바꾸지 않는다. 바꿀 이유가 생기는 시점은 계약
경로를 실제로 배선할 때이고, 그때는 서버 배포 상태를 먼저 확인해야 한다.
그전까지 이 항목은 기록으로만 둔다.

---

## 서버에 확인해야 할 것

**PostgREST 시그니처 — 미검증**

`commit_document` 에 인자를 더할 때 `create or replace function` 으로는 시그니처를
바꿀 수 없다. 그대로 하면 오버로드가 하나 더 생겨 PostgREST 가 모호해진다. 한
트랜잭션 안에서 `drop` 후 `create` 하면 오버로드가 생기지 않는다.

```sql
drop function public.commit_document(<기존 시그니처>);
create function public.commit_document(
  ..., p_parent_folder_id uuid default null
) ...
```

PostgREST 는 클라이언트가 보낸 인자 이름 집합으로 함수를 찾고, 나머지 파라미터에
`default` 가 있으면 생략을 허용한다. 배포된 `commit_folder` 가 이미
`p_is_deleted boolean DEFAULT false` 로 선언되어 있다. 이 방식이면 구버전 iPad
바이너리가 그대로 동작하고 조율 배포가 필요 없다.

staging 에서 확인이 필요하다. 이 항목을 적을 당시 Windows 는 서버에 접속하지
않았으나 **더 이상 그렇지 않다** — 아래를 보라.

**서버 stage 가 storage-name-v2 를 배포하고 그 digest 를 allowlist 했는가 — 미확인**

0.3.0 채택은 우리가 정할 결정이 아니라 이 사실에 달려 있다. 다만 그 사실을 묻기
전에 위 "계약 pin" 의 정정이 먼저다. 서버가 배포했더라도 iPad 에는 가져올 0.3.0
이 병합돼 있지 않고, 그러면 `becbf42` 의 서버 쪽 산출물을 어떻게 가져올지의
문제로 되돌아간다.

---

## 지금 열려 있는 항목

2026-08-25 기준. 이름으로 부른다.

**Windows — `tests/` 안에서 끝난다**

- 없음. 셋째 다리 부정 경로 밀폐 시험과 `resource_probe.py` 죽은 대기 함수
  삭제는 이 커밋에서 처리했다.

**Windows — 제품 코드**

- `_contract_identity` 를 `sub` 기반 fail-closed 로. 급하지 않으나
  **supabase-py 업그레이드 전에는 반드시** — 그 업그레이드가 비공개 속성
  `_antigravity_email` 을 없애면 전 계정에서 동시에 identity 가 빈 값이 된다.

**Windows — 스테이징에 쓰는 것**

- canary 에 **합성 원고** 투입 → 원고 무손상 실증. 사용자의 실제 원고가 아니다.
  이 흐름에서 원고 내용이 서버로 나가는 첫 지점이다. 종료 조건에 아래
  기준선 갱신을 포함한다.
- canary 정리용 삭제 배치. **위 실증이 끝나고 기록된 뒤에만** — 순서가 강제된다.
  삭제 배치는 그 실증의 증거를 지운다.
- canary 기대 기준선을 이 문서에 명기. 담당은 Windows 다. 라이브 DB 를 읽을 수
  있는 쪽이 여기뿐이다.

**iPad**

- 스테이징 첫 핸드셰이크. 배선은 `4033edc` 에 있으나 서버와 한 번도 대화한 적이
  없다.
- 관문 열기.
- 계약 경로 활성화.
- `waitForSleep` 반환값 가르기 — backstop 만료가 정상 경로(지연 0)와 같은
  `false` 를 돌려준다.
- `main` 에 같은 flaky 결함이 있는지 확인.

**양쪽**

- 검증06·07 대조.

**닫음**

- `becbf42` 병합 — 무효. iPad 의 `4033edc` 가 독립 작성이고 기능이 더 넓으며,
  `becbf42` 판본은 핸드셰이크를 SQLite 에 영속하고 재시작 후 복원해 설계상
  기각되었다. 그 브랜치의 서버 쪽 산출물은 위 "storage-name-v2" 항목이 붙잡는다.

## canary 기대 기준선

`SYNC-V2-CANARY-20260823` (`4df996c8-6443-4429-9dad-8fe3573af3d1`) 의 아래 상태는
drift 가 아니라 계약 경로 구조 쓰기 실증이 남긴 것이다. 대조할 때 설명되지 않는
폴더로 읽지 마라.

```
메인/메모장/계약경로검증    폴더 rev 1
메인/메모장/계약경로검증2   폴더 rev 1
메인/메모장                order rev 2, children 2
```

출처는 계약 배치 둘이고 양쪽 다 `applied=1` 이다.

```
4f6f2a51-e296-4c2b-b5fa-76fb347b39c4   folder create rev 1 + reorder rev 1
0434134c-41a4-40af-b210-86965446ff10   folder create rev 1 + reorder rev 2
```

canary 에 합성 원고를 넣고 다시 실증하면 이 표가 낡는다. 그때 함께 갱신한다.
