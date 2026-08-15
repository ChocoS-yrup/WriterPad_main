begin;

-- Clients must distinguish a genuinely empty active project from one hidden
-- by RLS because it is in the project trash. This RPC is the sync handshake
-- used on app start, project open, foreground resume, and reconnect.
create or replace function public.get_project_status(p_project_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_project public.projects%rowtype;
  v_is_member boolean := false;
  v_purged_owner uuid;
begin
  if v_user_id is null then
    raise exception using errcode = 'P0001', message = 'AUTH_REQUIRED';
  end if;
  if p_project_id is null then
    raise exception using errcode = 'P0001', message = 'INVALID_ARGUMENT';
  end if;

  select * into v_project
  from public.projects
  where project_id = p_project_id;

  if found then
    select exists (
      select 1
      from public.project_members member
      where member.project_id = p_project_id
        and member.user_id = v_user_id
    ) into v_is_member;

    if v_project.owner_id <> v_user_id and not v_is_member then
      raise exception using errcode = 'P0001', message = 'FORBIDDEN';
    end if;

    return pg_catalog.jsonb_build_object(
      'project_id', v_project.project_id,
      'state', case
        when v_project.trashed_at is null then 'active'
        else 'trashed'
      end,
      'name', v_project.name,
      'updated_at', v_project.updated_at,
      'trashed_at', v_project.trashed_at
    );
  end if;

  select owner_id into v_purged_owner
  from private.project_purge_tombstones
  where project_id = p_project_id;

  if found and v_purged_owner = v_user_id then
    return pg_catalog.jsonb_build_object(
      'project_id', p_project_id,
      'state', 'purged'
    );
  end if;

  raise exception using errcode = 'P0001', message = 'PROJECT_NOT_FOUND';
end;
$$;

revoke all on function public.get_project_status(uuid)
  from public, anon;
grant execute on function public.get_project_status(uuid)
  to authenticated;

commit;
