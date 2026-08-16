# 폴더 동기화 — 미해결 항목

두 클라이언트가 같이 읽는 목록이다. 합의된 것과 아직 아닌 것을 갈라 적는다.
단계 번호는 쓰지 않는다. 항목은 이름으로 부른다.

기준 시점의 Windows 커밋:

```
0363fcc  fix: tell the server when a folder goes to the trash
c9c7504  feat: publish created folders to the server, not only renamed ones
fa8e8b8  fix: move identity with a renamed or dragged binder item
2376057  feat: let the local identity file issue every sync UUID
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

**iPad 가 지운 폴더를 Windows 가 어떻게 받는가 — 미검증**

Windows 는 pull 에서 `is_deleted` 폴더 행을 걸러내기만 한다. 로컬 디렉터리를
휴지통으로 옮기는 경로는 문서에만 있고 폴더에는 없다. 어제 관찰된 것과 **거울상**
증상이 날 수 있다. iPad 에서 문서가 든 폴더를 지우면 Windows 바인더에 빈 폴더가
남을 수 있다.

이건 Windows 쪽 구현이다. 삭제된 폴더 행을 가짜 transport 로 흉내 내면 iPad 없이
검증된다. 실기기 확인은 마지막에만 필요하다.

**가져오기 프로젝트의 identity 정렬 — 미해결**

`initialize_existing_project` 는 identity 파일이 없으면 표준 폴더 UUID 를 새로
발급한다. 그 뒤 서버에서 문서를 pull 하면 서버가 발급한 id 가 들어온다. 그래서
iPad 가 만든 프로젝트를 Windows 가 가져오면 **설계상 반드시 어긋난다.**

- identity: Windows 가 방금 발급한 폴더 UUID, 문서 노드는 아예 없음
- 서버: iPad 가 발급한 `folder_id` / `document_id`

지금은 Windows 가 이 상태를 감지하고 **멈춘 뒤 보고만 한다.** 남의 이름을 쓰는
폴더는 `FOLDER_NAME_TAKEN`, 그 자식들은 `PARENT_NOT_PUBLISHED` 로 기록되고 서버에
RPC 를 한 번도 보내지 않는다. 진단은 `SyncV2Store.diagnostics()` 로 읽는다.

iPad 회신의 "identity 보존 Windows 가져오기는 native 경로로 취급하고, 기존 서버
프로젝트 가져오기에만 legacy migration 을 허용해야 한다" 가 이 항목이다.

**영구 삭제가 identity 노드를 남긴다 — 미해결**

`delete_from_trash` 와 `empty_trash` 는 파일을 지우지만 identity 노드는 지우지
않는다. 디스크에 없는 노드가 남고 `audit()` 이 `missing_on_disk` 로 보고한다.
폴더 tombstone 발행에는 오히려 유리하게 작용하지만 정합성 문제로 남아 있다.

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
