import unittest
from pathlib import Path


class ProjectStatusMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.sql = Path(
            "supabase/migrations/20260729010000_project_status.sql"
        ).read_text(encoding="utf-8").lower()

    def test_status_rpc_exposes_active_trash_and_owner_purge_tombstone(self):
        self.assertIn(
            "create or replace function public.get_project_status",
            self.sql,
        )
        self.assertIn("then 'active'", self.sql)
        self.assertIn("else 'trashed'", self.sql)
        self.assertIn("private.project_purge_tombstones", self.sql)
        self.assertIn("'state', 'purged'", self.sql)
        self.assertIn(
            "grant execute on function public.get_project_status(uuid)",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
