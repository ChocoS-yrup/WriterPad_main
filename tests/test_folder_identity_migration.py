import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from sync_v2_store import STAGE8_USER_VERSION, SyncV2Store


class FolderIdentityMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name, "sync.sqlite3"))
        self.store = SyncV2Store(self.db_path)
        self.context = self.store.configure_project(
            str(Path(self.temp.name, "writing")), "Folder identity"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_sqlite_schema_is_additive_and_keeps_legacy_epoch_zero(self):
        project = self.store.get_project(self.context["local_key"])
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)

        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                STAGE8_USER_VERSION,
            )
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(sync_folders)"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertTrue({
            "folder_id",
            "parent_folder_id",
            "local_path",
            "name",
            "storage_name_key",
            "revision",
        }.issubset(columns))

    def test_server_proven_folder_id_survives_rename_and_move(self):
        folder_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        self.store.replace_folder_snapshots(self.context["local_key"], [{
            "folder_id": folder_id,
            "parent_folder_id": None,
            "local_path": "메인/메모장/초안",
            "name": "초안",
            "revision": 1,
            "is_deleted": False,
        }])
        self.store.replace_folder_snapshots(self.context["local_key"], [
            {
                "folder_id": parent_id,
                "parent_folder_id": None,
                "local_path": "메인/메모장/정리",
                "name": "정리",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": folder_id,
                "parent_folder_id": parent_id,
                "local_path": "메인/메모장/정리/완성",
                "name": "완성",
                "revision": 2,
                "is_deleted": False,
            },
        ])

        folder = self.store.get_folder_by_id(folder_id)
        self.assertEqual(folder["folder_id"], folder_id)
        self.assertEqual(folder["parent_folder_id"], parent_id)
        self.assertEqual(folder["local_path"], "메인/메모장/정리/완성")
        self.assertEqual(folder["revision"], 2)


if __name__ == "__main__":
    unittest.main()
