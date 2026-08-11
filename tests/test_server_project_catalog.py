import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from server_project_import import (
    ACCESS_DENIED,
    AUTH_REQUIRED,
    INVALID_RESPONSE,
    NETWORK_UNAVAILABLE,
    LOCAL_CURRENT,
    LOCAL_MISSING,
    LOCAL_OTHER,
    ServerProjectCatalogError,
    ServerProjectCatalogService,
)
from sync_v2_store import SyncV2Store


class _FakeQuery:
    def __init__(self, client):
        self.client = client

    def select(self, fields):
        self.client.selected_fields = fields
        return self

    def execute(self):
        if self.client.error:
            raise self.client.error
        return SimpleNamespace(data=self.client.rows)


class _FakeSupabase:
    def __init__(self, rows=None, error=None, authenticated=True):
        self.rows = [] if rows is None else rows
        self.error = error
        self._antigravity_authenticated = authenticated
        self.selected_table = None
        self.selected_fields = None

    def table(self, name):
        self.selected_table = name
        return _FakeQuery(self)


class _HttpError(RuntimeError):
    def __init__(self, message, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _server_row(project_id=None, name="서버 작품", updated_at="2026-07-28T10:00:00Z"):
    return {
        "project_id": project_id or str(uuid.uuid4()),
        "name": name,
        "created_at": "2026-07-27T10:00:00Z",
        "updated_at": updated_at,
    }


class ServerProjectCatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = SyncV2Store(
            str(Path(self.temp_dir.name, "sync.sqlite3"))
        )

    def test_rls_allowed_projects_are_validated_and_stably_sorted(self):
        second_id = str(uuid.uuid4())
        first_id = str(uuid.uuid4())
        client = _FakeSupabase([
            _server_row(second_id, "나 작품"),
            _server_row(first_id, "가 작품"),
        ])

        projects = ServerProjectCatalogService(client, self.store).list_projects()

        self.assertEqual([project.name for project in projects], ["가 작품", "나 작품"])
        self.assertEqual(projects[0].project_id, first_id)
        self.assertEqual(
            projects[0].masked_project_id,
            f"{first_id[:8]}…{first_id[-4:]}",
        )
        self.assertEqual(client.selected_table, "projects")
        self.assertEqual(
            client.selected_fields,
            "project_id,name,created_at,updated_at",
        )

    def test_authentication_failure_and_empty_catalog_are_distinct(self):
        empty = ServerProjectCatalogService(
            _FakeSupabase([]), self.store
        ).list_projects()
        self.assertEqual(empty, [])

        with self.assertRaises(ServerProjectCatalogError) as caught:
            ServerProjectCatalogService(
                _FakeSupabase([], authenticated=False), self.store
            ).list_projects()
        self.assertEqual(caught.exception.code, AUTH_REQUIRED)

    def test_network_and_rls_failures_are_not_converted_to_empty_catalog(self):
        cases = (
            (_HttpError("connection timed out"), NETWORK_UNAVAILABLE),
            (_HttpError("permission denied for table projects", 403), ACCESS_DENIED),
        )
        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ServerProjectCatalogError) as caught:
                    ServerProjectCatalogService(
                        _FakeSupabase(error=error), self.store
                    ).list_projects()
                self.assertEqual(caught.exception.code, expected_code)

    def test_same_name_with_different_uuid_is_not_treated_as_imported(self):
        bound_id = str(uuid.uuid4())
        unbound_id = str(uuid.uuid4())
        self.store.configure_project(
            str(Path(self.temp_dir.name, "같은 이름", "집필모드")),
            "같은 이름",
            bound_id,
        )
        Path(self.temp_dir.name, "같은 이름", "집필모드").mkdir(
            parents=True
        )
        client = _FakeSupabase([
            _server_row(unbound_id, "같은 이름"),
            _server_row(bound_id, "같은 이름"),
        ])

        projects = ServerProjectCatalogService(client, self.store).list_projects()
        by_id = {project.project_id: project for project in projects}

        self.assertTrue(by_id[bound_id].already_imported)
        self.assertEqual(by_id[bound_id].local_project_name, "같은 이름")
        self.assertFalse(by_id[unbound_id].already_imported)
        self.assertEqual(by_id[unbound_id].local_project_name, "")

    def test_invalid_uuid_or_shape_is_rejected_as_invalid_response(self):
        invalid_rows = (
            [{"project_id": "not-a-uuid", "name": "작품",
              "created_at": "now", "updated_at": "now"}],
            [{"project_id": str(uuid.uuid4()), "name": "작품"}],
            {"project_id": str(uuid.uuid4())},
        )
        for rows in invalid_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ServerProjectCatalogError) as caught:
                    ServerProjectCatalogService(
                        _FakeSupabase(rows), self.store
                    ).list_projects()
                self.assertEqual(caught.exception.code, INVALID_RESPONSE)

    def test_store_project_id_lookup_normalizes_uuid_and_lists_bindings(self):
        project_id = str(uuid.uuid4())
        context = self.store.configure_project(
            str(Path(self.temp_dir.name, "연결 작품", "집필모드")),
            "연결 작품",
            project_id.upper(),
        )

        found = self.store.get_project_by_id(project_id)

        self.assertEqual(found["local_key"], context["local_key"])
        self.assertEqual(found["project_id"], project_id)
        self.assertEqual(
            [binding["project_id"] for binding in self.store.list_projects()],
            [project_id],
        )

    def test_incomplete_import_binding_is_not_reported_as_imported(self):
        project_id = str(uuid.uuid4())
        writing_root = str(
            Path(self.temp_dir.name, "재개할 작품", "집필모드")
        )
        self.store.begin_project_import(
            writing_root, "재개할 작품", project_id
        )
        self.store.set_project_import_state(
            project_id, "failed", "안전한 오류 메시지"
        )
        client = _FakeSupabase([
            _server_row(project_id, "서버의 작품명")
        ])

        project = ServerProjectCatalogService(
            client, self.store
        ).list_projects()[0]

        self.assertFalse(project.already_imported)
        self.assertTrue(project.import_incomplete)
        self.assertEqual(project.local_project_name, "재개할 작품")

    def test_binding_state_distinguishes_current_other_and_missing_paths(self):
        workspace = Path(self.temp_dir.name, "현재", "작품목록")
        workspace.mkdir(parents=True)
        current_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())

        current_root = workspace / "현재 작품" / "집필모드"
        other_root = (
            Path(self.temp_dir.name, "다른 실행본", "작품목록")
            / "다른 작품" / "집필모드"
        )
        missing_root = workspace / "누락 작품" / "집필모드"
        current_root.mkdir(parents=True)
        other_root.mkdir(parents=True)
        self.store.configure_project(
            str(current_root), "현재 작품", current_id
        )
        self.store.configure_project(
            str(other_root), "다른 작품", other_id
        )
        self.store.configure_project(
            str(missing_root), "누락 작품", missing_id
        )
        client = _FakeSupabase([
            _server_row(current_id, "현재 작품"),
            _server_row(other_id, "다른 작품"),
            _server_row(missing_id, "누락 작품"),
        ])

        projects = ServerProjectCatalogService(
            client, self.store, str(workspace)
        ).list_projects()
        by_id = {project.project_id: project for project in projects}

        self.assertEqual(by_id[current_id].local_state, LOCAL_CURRENT)
        self.assertTrue(by_id[current_id].already_imported)
        self.assertEqual(by_id[other_id].local_state, LOCAL_OTHER)
        self.assertTrue(by_id[other_id].already_imported)
        self.assertFalse(by_id[other_id].can_import)
        self.assertEqual(by_id[missing_id].local_state, LOCAL_MISSING)
        self.assertFalse(by_id[missing_id].already_imported)
        self.assertTrue(by_id[missing_id].can_import)


if __name__ == "__main__":
    unittest.main(verbosity=2)
