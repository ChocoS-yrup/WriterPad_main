"""Packaged UI smoke using new synthetic files, with no installed state access."""
import json
import os
from pathlib import Path
import socket
import sqlite3
from urllib.parse import unquote, urlsplit


def run_startup_smoke(argv):
    root = Path(os.environ['WRITERPAD_INTEGRATED_SMOKE_DIR']).absolute()
    if root.resolve() != root or not root.is_dir() or root != Path.cwd().resolve():
        raise RuntimeError('INVALID_SMOKE_DIRECTORY')
    workspace = root / 'synthetic-workspace'
    workspace.mkdir(exist_ok=False)
    denied = []
    def deny(*args, **kwargs):
        denied.append('network_or_credentials')
        raise AssertionError('REAL_NETWORK_OR_CREDENTIALS_FORBIDDEN')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.socket.sendto = deny
    import keyring
    keyring.get_password = keyring.set_password = keyring.delete_password = deny
    connect = sqlite3.connect
    def isolated_connect(database, *args, **kwargs):
        raw = str(database)
        if raw.startswith('file:'):
            raw = unquote(urlsplit(raw).path).lstrip('/')
        if raw != ':memory:' and workspace.resolve() not in Path(raw).resolve().parents:
            denied.append('sqlite_outside_synthetic_workspace')
            raise AssertionError('OUTSIDE_SQLITE_FORBIDDEN')
        return connect(database, *args, **kwargs)
    sqlite3.connect = isolated_connect
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from integrated_editor_store import IntegratedStore
    from integrated_editor_ui import IntegratedWritingWidget
    app = QApplication([argv[0]])
    store = IntegratedStore(workspace, device_id='10000000-0000-4000-8000-000000000002',
                            local_project_id='synthetic-packaged-startup')
    store.seed([], [])
    first = store.create('document', None, '첫 문서.txt', content='패키지 시작 검사\n')
    second = store.create('document', None, '둘째 문서.txt', content='둘째 본문')
    window = IntegratedWritingWidget(store, network_factory=deny)
    window.show()
    result = dict(passed=False, denied_attempts=denied, real_network_blocked=True,
                  real_credentials_blocked=True, sqlite_confined=True)
    def verify():
        try:
            window.rebuild_tree(first)
            window.editor.setPlainText('통합 EXE 한글·Unicode 😀\n마지막 LF 없음')
            window.save()
            window.rebuild_tree(second)
            assert window.editor.toPlainText() == '둘째 본문'
            window.rebuild_tree(first)
            assert window.editor.toPlainText() == '통합 EXE 한글·Unicode 😀\n마지막 LF 없음'
            window.autosync.setChecked(True)
            window.start_sync(True)
            assert window.network is None and window.worker is None and not denied
            result.update(passed=True, document_count=2, local_save=True, document_switch=True,
                          startup_auth_calls=0, automatic_without_scope_calls=0)
        except Exception as error:
            result['error_type'] = type(error).__name__
            result['error'] = str(error)
        finally:
            window.close()
            (root/'startup-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            app.exit(0 if result['passed'] else 1)
    QTimer.singleShot(0, verify)
    return app.exec()
