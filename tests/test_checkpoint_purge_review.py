"""Proposed Windows regressions; reuse only the existing synthetic fixture."""
import sqlite3
import unittest
import uuid

from tests import test_server_checkpoint as checkpoint_fixtures


class CheckpointPurgeReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = checkpoint_fixtures.CheckpointTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.accept()
        self.store = self.fixture.store
        self.key = self.fixture.key
        self.project_id = self.fixture.case.project_id

    def count(self, key):
        with self.store._reader() as connection:
            return connection.execute(
                "SELECT count(*) FROM sync_server_checkpoint_observations WHERE local_key=?",
                (key,),
            ).fetchone()[0]

    def test_permanent_project_purge_removes_only_its_checkpoint_observations(self):
        other_key = self.fixture.case.case.fixture.context["local_key"]
        other = self.store.get_project(other_key)
        with self.store._transaction() as connection:
            connection.execute(
                """INSERT INTO sync_server_checkpoint_observations
                   SELECT ?, ?, ?, old_mode, old_epoch, new_mode, new_epoch,
                          observed_at, source_json
                   FROM sync_server_checkpoint_observations WHERE local_key=?""",
                (str(uuid.uuid4()), other_key, other["project_id"], self.key),
            )
        self.assertEqual(self.count(self.key), 1)
        self.assertTrue(self.store.purge_project_records(self.project_id))
        self.assertIsNone(self.store.get_project_by_id(self.project_id))
        self.assertEqual(self.count(self.key), 0)
        self.assertEqual(self.count(other_key), 1)
        self.assertEqual(self.store.get_project(other_key), other)

    def test_normal_checkpoint_history_delete_remains_forbidden(self):
        with self.assertRaises(sqlite3.IntegrityError), self.store._transaction() as connection:
            connection.execute(
                "DELETE FROM sync_server_checkpoint_observations WHERE local_key=?", (self.key,)
            )
        self.assertEqual(self.count(self.key), 1)

    def test_later_purge_failure_restores_project_and_checkpoint_history(self):
        before = self.store.get_project(self.key)
        with self.store._transaction() as connection:
            connection.execute(
                """CREATE TRIGGER synthetic_checkpoint_purge_failure
                   BEFORE DELETE ON sync_projects
                   BEGIN SELECT RAISE(ABORT, 'synthetic purge failure'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.purge_project_records(self.project_id)
        self.assertEqual(self.store.get_project(self.key), before)
        self.assertEqual(self.count(self.key), 1)
        with self.store._reader() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sync_purge_gate").fetchone()[0], 0)
