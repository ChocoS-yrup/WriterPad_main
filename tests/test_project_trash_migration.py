import unittest
from pathlib import Path


class ProjectTrashMigrationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20260728020000_project_trash.sql"
        ).read_text(encoding="utf-8").lower()

    def test_project_soft_delete_state_and_active_rls_guard_are_defined(self):
        self.assertIn("add column if not exists trashed_at", self.sql)
        self.assertIn("add column if not exists trashed_by", self.sql)
        self.assertIn("and p.trashed_at is null", self.sql)
        self.assertIn("create or replace function private.has_project_role", self.sql)

    def test_trash_restore_and_purge_rpcs_are_owner_only(self):
        for function_name in (
            "list_trashed_projects",
            "trash_project",
            "restore_project",
            "purge_project",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(
                    f"create or replace function public.{function_name}",
                    self.sql,
                )
                self.assertIn(
                    f"grant execute on function public.{function_name}",
                    self.sql,
                )

        self.assertIn("if v_project.owner_id <> v_user_id then", self.sql)
        self.assertGreaterEqual(self.sql.count("message = 'forbidden'"), 3)

    def test_first_delete_preserves_documents_and_releases_edit_leases(self):
        trash_body = self.sql.split(
            "create or replace function public.trash_project", 1
        )[1].split(
            "create or replace function public.restore_project", 1
        )[0]

        self.assertIn("set trashed_at = v_now", trash_body)
        self.assertIn("delete from public.edit_leases", trash_body)
        self.assertNotIn("delete from public.projects", trash_body)
        self.assertNotIn("delete from public.documents", trash_body)

    def test_permanent_delete_requires_trash_and_keeps_resurrection_marker(self):
        purge_body = self.sql.split(
            "create or replace function public.purge_project", 1
        )[1]

        self.assertIn("message = 'project_not_trashed'", purge_body)
        self.assertIn(
            "insert into private.project_purge_tombstones",
            purge_body,
        )
        self.assertIn("delete from public.projects", purge_body)
        self.assertIn("already_purged", purge_body)

        ensure_body = self.sql.split(
            "create or replace function public.ensure_project", 1
        )[1].split(
            "create or replace function public.list_trashed_projects", 1
        )[0]
        self.assertIn("private.project_purge_tombstones", ensure_body)
        self.assertIn("message = 'project_purged'", ensure_body)

    def test_mutating_rpcs_are_serialized_and_authenticated(self):
        self.assertGreaterEqual(
            self.sql.count("pg_catalog.pg_advisory_xact_lock"), 4
        )
        self.assertGreaterEqual(
            self.sql.count("message = 'auth_required'"), 5
        )
        self.assertIn(
            "revoke all on table private.project_purge_tombstones",
            self.sql,
        )
        self.assertIn(
            "alter table private.project_purge_tombstones enable row level security",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
