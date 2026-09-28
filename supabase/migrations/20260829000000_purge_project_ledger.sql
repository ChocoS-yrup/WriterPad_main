begin;

-- 작품 영구 삭제와 추가 전용 원장이 정면으로 부딪쳤다. 원장 트리거가 DELETE 를
-- 무조건 거부하는 탓에, purge_project 가 projects 행을 지우는 순간 cascade 가
-- sync_batches 와 sync_operations 에 닿고 APPEND_ONLY_LEDGER 로 트랜잭션 전체가
-- 되돌아갔다. 클라이언트에는 분류되지 않는 오류로 도착해 "서버 작품 휴지통
-- 작업을 완료하지 못했습니다" 한 줄만 남았다.
--
-- 원장은 클라이언트가 지나간 기록을 고쳐 쓰는 것을 막으려고 있는 것이지, 주인이
-- 자기 작품을 지우는 것을 막으려고 있는 것이 아니다. 그래서 금지를 없애지 않고
-- 범위만 좁힌다. UPDATE 는 여전히 예외 없이 거부한다. DELETE 는 purge_project 가
-- 자기 트랜잭션 안에서 연 창 안에서만 통과한다.
--
-- private.unicode15_* 와 private.storage_name_v2_* 의 같은 이름 트리거는 건드리지
-- 않는다. 그쪽은 작품과 무관한 유니코드 참조 자료이고 진짜로 불변이어야 한다.

create or replace function private.reject_ledger_mutation()
returns trigger
language plpgsql
set search_path to ''
as $$
begin
  if tg_op = 'DELETE'
     and pg_catalog.current_setting('writerpad.purging_project', true) = 'on'
  then
    return old;
  end if;
  raise exception using errcode = 'P0001', message = 'APPEND_ONLY_LEDGER';
end;
$$;

revoke all on function private.reject_ledger_mutation()
  from public, anon, authenticated;

drop trigger if exists sync_batches_append_only on public.sync_batches;
create trigger sync_batches_append_only
  before delete or update on public.sync_batches
  for each row execute function private.reject_ledger_mutation();

drop trigger if exists sync_batch_results_append_only on public.sync_batch_results;
create trigger sync_batch_results_append_only
  before delete or update on public.sync_batch_results
  for each row execute function private.reject_ledger_mutation();

drop trigger if exists sync_operations_append_only on public.sync_operations;
create trigger sync_operations_append_only
  before delete or update on public.sync_operations
  for each row execute function private.reject_ledger_mutation();

drop trigger if exists sync_operation_attempts_append_only on public.sync_operation_attempts;
create trigger sync_operation_attempts_append_only
  before delete or update on public.sync_operation_attempts
  for each row execute function private.reject_ledger_mutation();

drop trigger if exists sync_operation_events_append_only on public.sync_operation_events;
create trigger sync_operation_events_append_only
  before delete or update on public.sync_operation_events
  for each row execute function private.reject_ledger_mutation();

-- 20260728020000 의 본문 그대로이고, 원장 삭제 창을 여닫는 두 줄만 는다.
-- set_config 의 세 번째 인자가 참이면 이 트랜잭션 안에서만 사는 값이라,
-- 커밋이든 롤백이든 창은 저절로 닫힌다. 열어 두고 빠져나갈 길이 없다.
create or replace function public.purge_project(p_project_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_now timestamptz := pg_catalog.transaction_timestamp();
  v_project public.projects%rowtype;
  v_tombstone_owner uuid;
begin
  if v_user_id is null then
    raise exception using errcode = 'P0001', message = 'AUTH_REQUIRED';
  end if;
  if p_project_id is null then
    raise exception using errcode = 'P0001', message = 'INVALID_ARGUMENT';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('project:' || p_project_id::text, 0)
  );

  select * into v_project
  from public.projects
  where project_id = p_project_id
  for update;

  if not found then
    select owner_id into v_tombstone_owner
    from private.project_purge_tombstones
    where project_id = p_project_id;

    if found and v_tombstone_owner = v_user_id then
      return pg_catalog.jsonb_build_object(
        'status', 'purged',
        'project_id', p_project_id,
        'already_purged', true
      );
    end if;
    raise exception using errcode = 'P0001', message = 'PROJECT_NOT_FOUND';
  end if;
  if v_project.owner_id <> v_user_id then
    raise exception using errcode = 'P0001', message = 'FORBIDDEN';
  end if;
  if v_project.trashed_at is null then
    raise exception using errcode = 'P0001', message = 'PROJECT_NOT_TRASHED';
  end if;

  insert into private.project_purge_tombstones (
    project_id, owner_id, purged_by, purged_at
  ) values (
    p_project_id, v_project.owner_id, v_user_id, v_now
  )
  on conflict (project_id) do nothing;

  perform pg_catalog.set_config('writerpad.purging_project', 'on', true);
  delete from public.projects where project_id = p_project_id;
  perform pg_catalog.set_config('writerpad.purging_project', 'off', true);

  return pg_catalog.jsonb_build_object(
    'status', 'purged',
    'project_id', p_project_id,
    'already_purged', false,
    'purged_at', v_now
  );
end;
$$;

revoke all on function public.purge_project(uuid) from public, anon;
grant execute on function public.purge_project(uuid) to authenticated;

commit;
