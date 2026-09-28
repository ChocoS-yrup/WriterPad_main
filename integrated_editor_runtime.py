"""Disabled candidate entry point. Local startup never reads credentials or HTTP.

A later reviewed candidate must supply an execution-scope file. This module
neither creates approval files nor changes the original gate/hold/preferences.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from integrated_editor_plan import DIRECTORY, PROJECT_ID, PROJECT_NAME, PROTECTED_DOCUMENT_ID, STAGING_URL
from integrated_editor_store import IntegratedStore, require, safe_path, digest


def protected_files_match(files):
    for name, expected in files.items():
        path = safe_path(name)
        if expected is None:
            if path.exists():
                return False
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def copy_source_database(db_path, directory):
    """Capture DB/WAL/SHM as files; SQLite never opens the original database."""
    source = safe_path(db_path)
    paths = [source, safe_path(str(source) + '-wal'), safe_path(str(source) + '-shm')]
    captured = {p: p.read_bytes() if p.exists() else None for p in paths}
    require(captured[source] is not None, 'SOURCE_DATABASE_MISSING')
    target = safe_path(Path(directory) / ('seed-' + str(uuid4())))
    target.mkdir(parents=True, exist_ok=False)
    for path, data in captured.items():
        if data is not None:
            with (target / path.name).open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    require(all((p.read_bytes() if p.exists() else None) == data for p, data in captured.items()),
            'SOURCE_CHANGED_DURING_COPY')
    return target / source.name


def read_seed(db_path, writing_root):
    """Read a preserved DB copy only on explicitly launched candidate startup."""
    path, root = safe_path(db_path), safe_path(writing_root)
    key = os.path.normcase(str(root))
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        project = conn.execute('SELECT * FROM sync_projects WHERE local_key=?', (key,)).fetchone()
        require(project and project['project_id'] == PROJECT_ID and project['project_name'] == PROJECT_NAME
                and project['project_sync_mode'] == 'ID_BASED' and project['migration_epoch'] == 1,
                'SOURCE_BINDING_CHANGED')
        require(not conn.execute("SELECT 1 FROM sync_operations WHERE local_key=? AND status NOT IN ('completed','cancelled')",
                                 (key,)).fetchone(), 'SOURCE_PENDING_WORK')
        nodes = []
        for table, kind, id_column in (('sync_documents', 'document', 'document_id'), ('sync_folders', 'folder', 'folder_id')):
            for r in conn.execute('SELECT * FROM ' + table + ' WHERE local_key=?', (key,)):
                if kind == 'document' and r['server_path'].startswith('__antigravity__/'):
                    continue
                require(r['revision'] >= 1, 'SOURCE_UNCOMMITTED_NODE')
                node = dict(id=r[id_column], kind=kind, parent=r['parent_folder_id'], name=r['name'],
                    deleted=bool(r['is_deleted']), revision=r['revision'], srev=r['structure_revision'] if kind == 'document' else 1)
                if kind == 'document':
                    node['content'] = r['base_content']
                    target = safe_path(root / r['local_path'])
                    require(root in target.parents and target.read_bytes() == node['content'].encode('utf-8'),
                            'SOURCE_HAS_UNSAVED_CONTENT')
                nodes.append(node)
        orders = [dict(id=r['tree_order_id'], parent=r['parent_folder_id'],
                       children=json.loads(r['children_json']), revision=r['revision'])
                  for r in conn.execute('SELECT * FROM sync_tree_orders WHERE local_key=?', (key,))]
        completed = next(n for n in nodes if n['id'] == PROTECTED_DOCUMENT_ID)
        require(completed['revision'] == 9 and digest(completed['content']) ==
                '570040e0bd94d5ff31c64799d549775ef74eb14503b7fa374f170ad7297b764f', 'COMPLETED_BASE_CHANGED')
        return nodes, orders, key
    finally:
        conn.close()


def runtime_paths():
    from integrated_editor_plan import INTEGRATED_EDITOR_ONLY
    from general_validation_ui import INSTALL_ROOT
    require(INTEGRATED_EDITOR_ONLY and getattr(sys, 'frozen', False), 'INTEGRATED_CANDIDATE_REQUIRED')
    require(Path(sys.executable).parent.resolve() == INSTALL_ROOT.resolve(), 'INSTALLATION_PATH_CHANGED')
    require(not any(os.environ.get(k) for k in ('ANTIGRAVITY_PROFILE', 'ANTIGRAVITY_ROOT_DIR',
        'ANTIGRAVITY_APP_DATA_DIR', 'ANTIGRAVITY_FORCE_PROJECT_ID', 'ANTIGRAVITY_INSTANCE_KEY')), 'RUNTIME_OVERRIDE_REFUSED')
    appdata = safe_path(Path(os.environ['LOCALAPPDATA']) / 'AntigravityWriter')
    return appdata, safe_path(INSTALL_ROOT / '작품목록' / PROJECT_NAME / '집필모드')


def network_factory(store, foreground):
    from cloud_config import load_cloud_client_config
    from security_manager import SecurityManager
    from integrated_editor_sync import IntegratedNetwork
    appdata, root = runtime_paths()
    scope_file = safe_path(appdata / DIRECTORY / 'reviewed-execution.json')
    require(scope_file.is_file(), 'NO_EXECUTION_SCOPE')
    raw = scope_file.read_bytes()
    scope = json.loads(raw)
    require(set(scope) == {'approval', 'protected_files'} and isinstance(scope['protected_files'], dict),
            'INVALID_EXECUTION_SCOPE')
    # Fixed original DB/hold/device/manuscript are mandatory preservation anchors.
    from normal_editor_plan import PATH
    expected_paths = [appdata / 'sync_v2.sqlite3', appdata / 'sync_v2.sqlite3-wal',
                      appdata / 'sync_v2.sqlite3-shm', appdata / '.device_id',
                      appdata / ('.general-test-send-hold-' + PROJECT_ID), root / PATH]
    require(all(str(p) in scope['protected_files'] for p in expected_paths), 'PRESERVATION_ANCHORS_MISSING')
    token, _ = SecurityManager.get_supabase_session()
    require(token, 'SAVED_LOGIN_REQUIRED')
    token_sha = hashlib.sha256(token.encode()).digest()
    config = load_cloud_client_config(Path(sys._MEIPASS))
    require(config.is_ready and config.url == STAGING_URL, 'STAGING_REQUIRED')
    def current():
        if not foreground() or scope_file.read_bytes() != raw:
            return False
        if hashlib.sha256(SecurityManager.get_supabase_session()[0].encode()).digest() != token_sha:
            return False
        return protected_files_match(scope['protected_files'])
    require(current(), 'PRESERVED_STATE_CHANGED')
    return IntegratedNetwork(store, scope['approval'], access_token=token,
                             api_key=config.publishable_key, check_current=current)


def run_integrated_editor_app(argv):
    if '--integrated-editor-startup-smoke-test' in argv:
        from integrated_editor_smoke import run_startup_smoke
        return run_startup_smoke(argv)
    from PyQt6.QtCore import QLockFile
    from PyQt6.QtWidgets import QApplication
    from integrated_editor_ui import IntegratedWritingWidget
    appdata, root = runtime_paths()
    app = QApplication([argv[0]])
    lock = QLockFile(str(appdata / 'general-validation-execution.lock'))
    lock.setStaleLockTime(0)
    require(lock.tryLock(0), 'ANOTHER_EDITOR_RUNNING')
    try:
        device = (appdata / '.device_id').read_text('utf-8').strip()
        key = os.path.normcase(str(root))
        store = IntegratedStore(appdata / DIRECTORY, device_id=device, local_project_id=key)
        if not store.state()['seeded']:
            copied_db = copy_source_database(appdata / 'sync_v2.sqlite3', store.directory)
            nodes, orders, _ = read_seed(copied_db, root)
            store.seed(nodes, orders)
        store.materialize()
        window = IntegratedWritingWidget(store, network_factory=network_factory)
        window.show()
        return app.exec()
    finally:
        lock.unlock()
