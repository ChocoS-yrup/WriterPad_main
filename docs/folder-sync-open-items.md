# 폴더 동기화 — 미해결 항목

두 클라이언트가 같이 읽는 목록이다. 합의된 것과 아직 아닌 것을 갈라 적는다.
단계 번호는 쓰지 않는다. 항목은 이름으로 부른다.

기준 시점의 Windows 커밋:

```
fix: follow a folder another device moved to the trash
fix: tell the server when a folder goes to the trash
feat: publish created folders to the server, not only renamed ones
fix: move identity with a renamed or dragged binder item
feat: let the local identity file issue every sync UUID
```

브랜치는 `codex/windows-stage4a-local-uuid-identity` 다. 정확한 SHA 는
`git log --oneline` 으로 읽어라.

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

## Windows 가 확인해야 할 것

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

## iPad 가 답해야 할 것

**깊이 순서 — 미해결**

폴더 operation 이 부모 우선이라고 보고했다. 생성에는 맞지만 삭제에는 정반대다.
중첩 폴더를 통째로 버리면 `FOLDER_NOT_EMPTY` 로 실패한다. 삭제 operation 은 깊이
내림차순이어야 하고, 같은 배수 안에서 병렬 전송하면 안 된다.

**폴더 복원 발행 — 미확인**

휴지통에서 꺼낸 폴더를 `commit_folder(is_deleted = false)` 로 되돌리는가.
삭제만 보내고 복원을 안 보내면 반대쪽에서 폴더가 죽은 채로 남는다.

**빈 폴더가 `tree_order` 에 들어가는가 — 미확인**

Windows 는 서버 `folders` 투영과 `tree_order` 에서 유도한 폴더 집합이 어긋나면
pull 전체를 보류한다. 유도 집합은 `tree_order` 의 부모 키와, live 문서가 아닌
자식 이름으로 만들어진다.

```
tree_order 에 {"메인/설정집": ["빈폴더"]} 로 형제 이름이 들어감  → 정상
tree_order 에는 없고 folderSnapshot 에만 있음                    → 영구 블록
```

후자면 새 프로젝트가 처음부터 동기화되지 않고 원인이 사용자에게 보이지 않는다.
휴지통과 달리 서버가 막아주지 않는다.

**최초 batch 의 `ensure_project` bookkeeping — 미해결**

초기 batch 에 `ensure_project` 행을 기록하지만 dispatcher 가 claim 하지 않아
pending 으로 남는다고 보고했다. 원고 손실 위험은 없어 보인다. 폴더 차단 해소와
같은 커밋에 섞지 않는다.

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

staging 에서 확인이 필요하다. Windows 는 서버에 접속하지 않으므로 검증하지
못했다.
