begin;

-- Folder identity is additive. Legacy clients can continue exchanging the
-- hidden tree-order document while newer clients migrate to stable folder UUIDs.
create or replace function private.is_valid_entry_name(p_name text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select
    p_name is not null
    and p_name <> ''
    and p_name = pg_catalog.btrim(p_name)
    and pg_catalog.char_length(p_name) <= 255
    and p_name not in ('.', '..')
    and pg_catalog.right(p_name, 1) <> '.'
    and p_name !~ E'[<>:"/\\\\|?*]'
    and p_name !~ '[[:cntrl:]]'
    and pg_catalog.upper(pg_catalog.split_part(p_name, '.', 1)) <> all (
      array[
        'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5',
        'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5',
        'LPT6', 'LPT7', 'LPT8', 'LPT9'
      ]::text[]
    );
$$;

revoke all on function private.is_valid_entry_name(text) from public;

create table public.folders (
  folder_id uuid primary key,
  project_id uuid not null references public.projects(project_id) on delete cascade,
  parent_folder_id uuid,
  name text not null check (private.is_valid_entry_name(name)),
  revision bigint not null check (revision >= 1),
  current_version_id uuid,
  is_deleted boolean not null default false,
  deleted_at timestamptz,
  created_by uuid not null references auth.users(id) on delete restrict,
  updated_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default pg_catalog.transaction_timestamp(),
  updated_at timestamptz not null default pg_catalog.transaction_timestamp(),
  constraint folders_identity_project_uk unique (folder_id, project_id),
  constraint folders_not_own_parent_ck check (parent_folder_id is distinct from folder_id),
  constraint folders_deleted_at_ck check (
    (is_deleted and deleted_at is not null)
    or (not is_deleted and deleted_at is null)
  ),
  constraint folders_parent_fk
    foreign key (parent_folder_id, project_id)
    references public.folders(folder_id, project_id)
    deferrable initially deferred
);

-- Windows and iPad must agree on one live sibling even when only letter case differs.
create unique index folders_live_root_name_uidx
  on public.folders(project_id, pg_catalog.lower(name))
  where parent_folder_id is null and not is_deleted;

create unique index folders_live_child_name_uidx
  on public.folders(project_id, parent_folder_id, pg_catalog.lower(name))
  where parent_folder_id is not null and not is_deleted;

create index folders_project_revision_idx
  on public.folders(project_id, revision, folder_id);

create index folders_project_parent_idx
  on public.folders(project_id, parent_folder_id, folder_id);

create table public.folder_versions (
  version_id uuid primary key default pg_catalog.gen_random_uuid(),
  folder_id uuid not null,
  project_id uuid not null,
  revision bigint not null check (revision >= 1),
  base_revision bigint not null check (base_revision >= 0),
  operation_id uuid not null,
  device_id uuid not null,
  operation_kind text not null check (
    operation_kind in ('create', 'rename', 'move', 'update', 'delete', 'restore')
  ),
  parent_folder_id uuid,
  name text not null check (private.is_valid_entry_name(name)),
  is_deleted boolean not null,
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default pg_catalog.transaction_timestamp(),
  constraint folder_versions_folder_fk
    foreign key (folder_id, project_id)
    references public.folders(folder_id, project_id)
    on delete cascade,
  unique (folder_id, revision),
  unique (operation_id),
  unique (folder_id, version_id)
);

alter table public.folders
  add constraint folders_current_version_fk
  foreign key (folder_id, current_version_id)
  references public.folder_versions(folder_id, version_id)
  deferrable initially deferred;

create index folder_versions_project_created_idx
  on public.folder_versions(project_id, created_at, folder_id);

alter table public.folders enable row level security;
alter table public.folders force row level security;
alter table public.folder_versions enable row level security;
alter table public.folder_versions force row level security;

create policy folders_read_members
on public.folders
for select
using (private.has_project_role(project_id, auth.uid(), 'viewer'));

create policy folder_versions_read_members
on public.folder_versions
for select
using (private.has_project_role(project_id, auth.uid(), 'viewer'));

revoke all on table public.folders from public, anon, authenticated;
revoke all on table public.folder_versions from public, anon, authenticated;
grant select on table public.folders to authenticated;
grant select on table public.folder_versions to authenticated;

create or replace function public.commit_folder(
  p_folder_id uuid,
  p_project_id uuid,
  p_base_revision bigint,
  p_operation_id uuid,
  p_device_id uuid,
  p_parent_folder_id uuid,
  p_name text,
  p_is_deleted boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_now timestamptz := pg_catalog.transaction_timestamp();
  v_project public.projects%rowtype;
  v_folder public.folders%rowtype;
  v_parent public.folders%rowtype;
  v_version public.folder_versions%rowtype;
  v_revision bigint;
  v_kind text;
  v_cycle boolean := false;
  v_folder_exists boolean := false;
begin
  if v_user_id is null then
    raise exception using errcode = 'P0001', message = 'AUTH_REQUIRED';
  end if;
  if p_folder_id is null
     or p_project_id is null
     or p_operation_id is null
     or p_device_id is null
     or p_base_revision is null
     or p_base_revision < 0
     or p_is_deleted is null
     or not private.is_valid_entry_name(p_name) then
    raise exception using errcode = 'P0001', message = 'INVALID_ARGUMENT';
  end if;
  if p_parent_folder_id = p_folder_id then
    raise exception using errcode = 'P0001', message = 'FOLDER_CYCLE';
  end if;

  -- One project lock serializes sibling-name and ancestry checks. The operation
  -- and folder locks make a retry with the same UUID deterministic.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('operation:' || p_operation_id::text, 0)
  );
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('folder:' || p_folder_id::text, 0)
  );
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('project:' || p_project_id::text, 0)
  );

  select * into v_project
  from public.projects
  where project_id = p_project_id
  for update;

  if not found then
    if exists (
      select 1
      from private.project_purge_tombstones tombstone
      where tombstone.project_id = p_project_id
        and tombstone.owner_id = v_user_id
    ) then
      raise exception using errcode = 'P0001', message = 'PROJECT_PURGED';
    end if;
    raise exception using errcode = 'P0001', message = 'PROJECT_NOT_FOUND';
  end if;
  if v_project.trashed_at is not null then
    raise exception using errcode = 'P0001', message = 'PROJECT_TRASHED';
  end if;
  if not private.has_project_role(p_project_id, v_user_id, 'editor') then
    raise exception using errcode = 'P0001', message = 'FORBIDDEN';
  end if;

  select * into v_version
  from public.folder_versions
  where operation_id = p_operation_id;

  if found then
    if v_version.folder_id <> p_folder_id
       or v_version.project_id <> p_project_id
       or v_version.base_revision <> p_base_revision
       or v_version.device_id <> p_device_id
       or v_version.parent_folder_id is distinct from p_parent_folder_id
       or v_version.name <> p_name
       or v_version.is_deleted <> p_is_deleted
       or v_version.created_by <> v_user_id then
      raise exception using errcode = 'P0001', message = 'OPERATION_ID_REUSED';
    end if;

    return pg_catalog.jsonb_build_object(
      'status', 'replayed',
      'folder_id', v_version.folder_id,
      'version_id', v_version.version_id,
      'operation_id', v_version.operation_id,
      'operation_kind', v_version.operation_kind,
      'revision', v_version.revision,
      'parent_folder_id', v_version.parent_folder_id,
      'name', v_version.name,
      'is_deleted', v_version.is_deleted,
      'committed_at', v_version.created_at
    );
  end if;

  select * into v_folder
  from public.folders
  where folder_id = p_folder_id
  for update;
  v_folder_exists := found;

  if p_parent_folder_id is not null then
    select * into v_parent
    from public.folders
    where folder_id = p_parent_folder_id
      and project_id = p_project_id
    for update;

    if not found or v_parent.is_deleted then
      raise exception using errcode = 'P0001', message = 'PARENT_FOLDER_NOT_FOUND';
    end if;

    with recursive ancestors as (
      select folder.folder_id, folder.parent_folder_id
      from public.folders folder
      where folder.folder_id = p_parent_folder_id
        and folder.project_id = p_project_id
      union all
      select parent.folder_id, parent.parent_folder_id
      from public.folders parent
      join ancestors child on parent.folder_id = child.parent_folder_id
      where parent.project_id = p_project_id
    )
    select exists (
      select 1 from ancestors where folder_id = p_folder_id
    ) into v_cycle;

    if v_cycle then
      raise exception using errcode = 'P0001', message = 'FOLDER_CYCLE';
    end if;
  end if;

  if p_base_revision = 0 then
    if v_folder_exists then
      raise exception using errcode = 'P0001', message = 'FOLDER_ALREADY_EXISTS';
    end if;
    if p_is_deleted then
      raise exception using errcode = 'P0001', message = 'INVALID_ARGUMENT';
    end if;
    if exists (
      select 1
      from public.folders sibling
      where sibling.project_id = p_project_id
        and sibling.parent_folder_id is not distinct from p_parent_folder_id
        and pg_catalog.lower(sibling.name) = pg_catalog.lower(p_name)
        and not sibling.is_deleted
    ) then
      raise exception using errcode = 'P0001', message = 'FOLDER_NAME_CONFLICT';
    end if;

    v_revision := 1;
    v_kind := 'create';
    insert into public.folders (
      folder_id, project_id, parent_folder_id, name, revision,
      is_deleted, deleted_at, created_by, updated_by, created_at, updated_at
    ) values (
      p_folder_id, p_project_id, p_parent_folder_id, p_name, v_revision,
      false, null, v_user_id, v_user_id, v_now, v_now
    );
  else
    if not v_folder_exists or v_folder.project_id <> p_project_id then
      raise exception using errcode = 'P0001', message = 'FOLDER_NOT_FOUND';
    end if;
    if v_folder.revision <> p_base_revision then
      raise exception using
        errcode = 'P0001',
        message = 'REVISION_CONFLICT',
        detail = pg_catalog.jsonb_build_object(
          'current_revision', v_folder.revision,
          'parent_folder_id', v_folder.parent_folder_id,
          'name', v_folder.name,
          'is_deleted', v_folder.is_deleted
        )::text;
    end if;
    if p_is_deleted and exists (
      select 1
      from public.folders child
      where child.project_id = p_project_id
        and child.parent_folder_id = p_folder_id
        and not child.is_deleted
    ) then
      raise exception using errcode = 'P0001', message = 'FOLDER_NOT_EMPTY';
    end if;
    if not p_is_deleted and exists (
      select 1
      from public.folders sibling
      where sibling.project_id = p_project_id
        and sibling.parent_folder_id is not distinct from p_parent_folder_id
        and sibling.folder_id <> p_folder_id
        and pg_catalog.lower(sibling.name) = pg_catalog.lower(p_name)
        and not sibling.is_deleted
    ) then
      raise exception using errcode = 'P0001', message = 'FOLDER_NAME_CONFLICT';
    end if;

    v_revision := v_folder.revision + 1;
    v_kind := case
      when not v_folder.is_deleted and p_is_deleted then 'delete'
      when v_folder.is_deleted and not p_is_deleted then 'restore'
      when v_folder.parent_folder_id is distinct from p_parent_folder_id then 'move'
      when v_folder.name <> p_name then 'rename'
      else 'update'
    end;
  end if;

  insert into public.folder_versions (
    folder_id, project_id, revision, base_revision, operation_id, device_id,
    operation_kind, parent_folder_id, name, is_deleted, created_by, created_at
  ) values (
    p_folder_id, p_project_id, v_revision, p_base_revision, p_operation_id,
    p_device_id, v_kind, p_parent_folder_id, p_name, p_is_deleted,
    v_user_id, v_now
  )
  returning * into v_version;

  update public.folders
  set parent_folder_id = p_parent_folder_id,
      name = p_name,
      revision = v_revision,
      current_version_id = v_version.version_id,
      is_deleted = p_is_deleted,
      deleted_at = case when p_is_deleted then v_now else null end,
      updated_by = v_user_id,
      updated_at = v_now
  where folder_id = p_folder_id;

  return pg_catalog.jsonb_build_object(
    'status', 'committed',
    'folder_id', v_version.folder_id,
    'version_id', v_version.version_id,
    'operation_id', v_version.operation_id,
    'operation_kind', v_version.operation_kind,
    'revision', v_version.revision,
    'parent_folder_id', v_version.parent_folder_id,
    'name', v_version.name,
    'is_deleted', v_version.is_deleted,
    'committed_at', v_version.created_at
  );
end;
$$;

revoke all on function public.commit_folder(
  uuid, uuid, bigint, uuid, uuid, uuid, text, boolean
) from public, anon;
grant execute on function public.commit_folder(
  uuid, uuid, bigint, uuid, uuid, uuid, text, boolean
) to authenticated;

-- Realtime remains only a wake-up signal; clients reconcile folder revisions.
do $$
begin
  alter publication supabase_realtime add table public.folders;
exception
  when duplicate_object then null;
end;
$$;

comment on table public.folders is
  'Stable cross-device folder projection. Writes only through commit_folder.';
comment on table public.folder_versions is
  'Immutable folder history and operation-id idempotency log.';
comment on function public.commit_folder(
  uuid, uuid, bigint, uuid, uuid, uuid, text, boolean
) is
  'Atomically creates, renames, moves, deletes, or restores one stable folder UUID.';

commit;
