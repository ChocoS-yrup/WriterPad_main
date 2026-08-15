import unittest
from pathlib import Path


class SupabaseMigrationTestCase(unittest.TestCase):
    def test_v2_rpc_permission_repair_covers_every_windows_rpc(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20260728010000_repair_v2_rpc_permissions.sql"
        ).read_text(encoding="utf-8").lower()

        signatures = (
            "private.has_project_role(uuid, uuid, text)",
            "public.ensure_project(uuid, text)",
            "public.acquire_edit_lease(uuid, uuid, integer)",
            "public.renew_edit_lease(uuid, uuid, uuid, integer)",
            "public.release_edit_lease(uuid, uuid, uuid)",
            "public.get_edit_lease(uuid, uuid)",
            "public.commit_document(",
        )
        for signature in signatures:
            with self.subTest(signature=signature):
                self.assertIn(f"grant execute on function {signature}", migration)

        self.assertIn("to authenticated", migration)
        self.assertIn("from public, anon", migration)


if __name__ == "__main__":
    unittest.main()
