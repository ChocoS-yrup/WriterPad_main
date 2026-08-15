begin;

alter table public.projects
  add column if not exists trashed_at timestamptz,
  add column if not exists trashed_by uuid references auth.users(id) on delete restrict;

alter table public.projects
  drop constraint if exists projects_trash_state_ck;

alter table public.projects
  add constraint projects_trash_state_ck check (
    (trashed_at is null and trashed_by is null)
    or (trashed_at is not null and trashed_by is not null)
  );

create index if not exists projects_owner_trash_idx
  on public.projects(owner_id, trashed_at, project_id);

-- Keep a permanent, minimal marker after physical deletion. Otherwise an
-- offline/stale client could call ensure_project with the old UUID and
-- accidentally resurrect a project that the owner permanently deleted.
create table if not exists private.project_purge_tombstones (
  project_id uuid primary key,
  owner_id uuid not null,
  purged_by uuid not null,
  purged_at timestamptz not null default pg_catalog.transaction_timestamp()
);

alter table private.project_purge_tombstones enable row level security;

revoke all on table private.project_purge_tombstones
  from public, anon, authenticated;

create or replace function private.has_project_role(
  p_project_id uuid,
  p_user_id uuid,
  p_minimum_role text default 'viewer'
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select
    p_user_id is not null
    and p_minimum_role in ('owner', 'editor', 'viewer')
    and exists (
      select 1
      from public.projects p
      left join public.project_members m
        on m.project_id = p.project_id
       and m.user_id = p_user_id
      where p.project_id = p_project_id
        and p.trashed_at is null
        and (
          p.owner_id = p_user_id
          or case p_minimum_role
            when 'owner' then m.role = 'owner'
            when 'editor' then m.role in ('owner', 'editor')
            when 'viewer' then m.role in ('owner', 'editor', 'viewer')
          end
        )
    );
$$;

revoke all on function private.has_project_role(uuid, uuid, text) from public;
grant execute on function private.has_project_role(uuid, uuid, text) to authenticated;

-- Reject old clients trying to recreate a permanently deleted UUID, and make
-- a project in the trash unavailable through the normal active-project RPC.
create or replace function public.ensure_project(
  p_project_id uuid,
  p_name text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_project public.projects%rowtype;
begin
  if v_user_id is null then
    raise exception using errcode = 'P0001', message = 'AUTH_REQUIRED';
  end if;
  if p_project_id is null or p_name is null or pg_catalog.btrim(p_name) = '' then
    raise exception using errcode = 'P0001', message = 'INVALID_ARGUMENT';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('project:' || p_project_id::text, 0)
  );

  if exists (
    select 1
    from private.project_purge_tombstones
    where project_id = p_project_id
  ) then
    raise exception using errcode = 'P0001', message = 'PROJECT_PURGED';
  end if;

  select * into v_project
  from public.projects
  where project_id = p_project_id
  for update;

  if not found then
    insert into public.projects (project_id, owner_id, name)
    values (p_project_id, v_user_id, pg_catalog.btrim(p_name));
    insert into public.project_members (project_id, user_id, role)
    values (p_project_id, v_user_id, 'owner')
    on conflict (project_id, user_id) do update set role = 'owner';
  elsif v_project.trashed_at is not null then
    raise exception using errcode = 'P0001', message = 'PROJECT_TRASHED';
  elsif not private.has_project_role(p_project_id, v_user_id, 'editor') then
    raise exception using errcode = 'P0001', message = 'FORBIDDEN';
  else
    update public.projects
    set name = pg_catalog.btrim(p_name),
        updated_at = pg_catalog.transaction_timestamp()
    where project_id = p_project_id;
  end if;

  return pg_catalog.jsonb_build_object(
    'project_id', p_project_id,
    'name', pg_catalog.btrim(p_name)
  );
end;
$$;

create or replace function public.list_trashed_projects()
returns table (
  project_id uuid,
  name text,
  trashed_at timestamptz,
  updated_at timestamptz
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
begin
  if v_user_id is null then
    raise exception using errcode = 'P0001', message = 'AUTH_REQUIRED';
  end if;

  return query
  select p.project_id, p.name, p.trashed_at, p.updated_at
  from public.projects p
  where p.owner_id = v_user_id
    and p.trashed_at is not null
  order by p.trashed_at desc, p.name, p.project_id;
end;
$$;

create or replace function public.trash_project(p_project_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_now timestamptz := pg_catalog.transaction_timestamp();
  v_project public.projects%rowtype;
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
    if exists (
      select 1 from private.project_purge_tombstones
      where project_id = p_project_id
    ) then
      raise exception using errcode = 'P0001', message = 'PROJECT_PURGED';
    end if;
    raise exception using errcode = 'P0001', message = 'PROJECT_NOT_FOUND';
  end if;
  if v_project.owner_id <> v_user_id then
    raise exception using errcode = 'P0001', message = 'FORBIDDEN';
  end if;

  if v_project.trashed_at is null then
    update public.projects
    set trashed_at = v_now,
        trashed_by = v_user_id,
        updated_at = v_now
    where project_id = p_project_id
    returning * into v_project;

    delete from public.edit_leases lease
    using public.documents document
    where document.project_id = p_project_id
      and lease.document_id = document.document_id;
  end if;

  return pg_catalog.jsonb_build_object(
    'status', 'trashed',
    'project_id', v_project.project_id,
    'name', v_project.name,
    'trashed_at', v_project.trashed_at
  );
end;
$$;

create or replace function public.restore_project(p_project_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_now timestamptz := pg_catalog.transaction_timestamp();
  v_project public.projects%rowtype;
  v_was_trashed boolean;
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
    if exists (
      select 1 from private.project_purge_tombstones
      where project_id = p_project_id
    ) then
      raise exception using errcode = 'P0001', message = 'PROJECT_PURGED';
    end if;
    raise exception using errcode = 'P0001', message = 'PROJECT_NOT_FOUND';
  end if;
  if v_project.owner_id <> v_user_id then
    raise exception using errcode = 'P0001', message = 'FORBIDDEN';
  end if;

  v_was_trashed := v_project.trashed_at is not null;
  if v_was_trashed then
    update public.projects
    set trashed_at = null,
        trashed_by = null,
        updated_at = v_now
    where project_id = p_project_id
    returning * into v_project;
  end if;

  return pg_catalog.jsonb_build_object(
    'status', 'active',
    'project_id', v_project.project_id,
    'name', v_project.name,
    'restored', v_was_trashed
  );
end;
$$;

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

  delete from public.projects where project_id = p_project_id;

  return pg_catalog.jsonb_build_object(
    'status', 'purged',
    'project_id', p_project_id,
    'already_purged', false,
    'purged_at', v_now
  );
end;
$$;

revoke all on function public.ensure_project(uuid, text) from public, anon;
revoke all on function public.list_trashed_projects() from public, anon;
revoke all on function public.trash_project(uuid) from public, anon;
revoke all on function public.restore_project(uuid) from public, anon;
revoke all on function public.purge_project(uuid) from public, anon;

grant execute on function public.ensure_project(uuid, text) to authenticated;
grant execute on function public.list_trashed_projects() to authenticated;
grant execute on function public.trash_project(uuid) to authenticated;
grant execute on function public.restore_project(uuid) to authenticated;
grant execute on function public.purge_project(uuid) to authenticated;

commit;
