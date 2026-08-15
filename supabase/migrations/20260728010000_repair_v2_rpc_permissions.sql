begin;

-- Re-apply the exact RPC permissions required by authenticated WriterPad
-- clients. This is intentionally idempotent so it can repair databases where
-- the v2 functions were created manually or the original grants were skipped.
grant usage on schema public to authenticated;
grant usage on schema private to authenticated;

revoke all on function private.has_project_role(uuid, uuid, text) from public, anon;
revoke all on function public.ensure_project(uuid, text) from public, anon;
revoke all on function public.acquire_edit_lease(uuid, uuid, integer) from public, anon;
revoke all on function public.renew_edit_lease(uuid, uuid, uuid, integer) from public, anon;
revoke all on function public.release_edit_lease(uuid, uuid, uuid) from public, anon;
revoke all on function public.get_edit_lease(uuid, uuid) from public, anon;
revoke all on function public.commit_document(
  uuid, uuid, bigint, uuid, uuid, text, text, boolean, uuid
) from public, anon;

grant execute on function private.has_project_role(uuid, uuid, text) to authenticated;
grant execute on function public.ensure_project(uuid, text) to authenticated;
grant execute on function public.acquire_edit_lease(uuid, uuid, integer) to authenticated;
grant execute on function public.renew_edit_lease(uuid, uuid, uuid, integer) to authenticated;
grant execute on function public.release_edit_lease(uuid, uuid, uuid) to authenticated;
grant execute on function public.get_edit_lease(uuid, uuid) to authenticated;
grant execute on function public.commit_document(
  uuid, uuid, bigint, uuid, uuid, text, text, boolean, uuid
) to authenticated;

commit;
