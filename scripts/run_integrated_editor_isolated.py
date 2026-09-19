"""One bundled run, or an explicit failure/affected subset; no real network/DB."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '_evidence/windows-integrated-editor-20260913' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
SANDBOX = OUT / 'private'
SANDBOX.mkdir(parents=True, exist_ok=False)
(SANDBOX / '.gitignore').write_text('*\n', encoding='utf-8')
os.environ.update(ANTIGRAVITY_PROFILE='integrated-editor-isolated', ANTIGRAVITY_APP_DATA_DIR=str(SANDBOX/'appdata'),
    ANTIGRAVITY_ROOT_DIR=str(SANDBOX/'installation'), QT_QPA_PLATFORM='offscreen', TEMP=str(SANDBOX), TMP=str(SANDBOX))
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def deny(*args, **kwargs):
    raise AssertionError('REAL_NETWORK_OR_CREDENTIALS_FORBIDDEN')


socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.socket.sendto = deny
import keyring
keyring.get_password = keyring.set_password = keyring.delete_password = deny
connect = sqlite3.connect


def isolated_connect(database, *args, **kwargs):
    raw = str(database)
    if raw != ':memory:':
        if raw.startswith('file:'):
            raw = unquote(urlsplit(raw).path).lstrip('/')
        if SANDBOX.resolve() not in Path(raw).resolve().parents:
            raise AssertionError('OUTSIDE_SQLITE_FORBIDDEN')
    return connect(database, *args, **kwargs)


sqlite3.connect = isolated_connect
names = sys.argv[1:] or ['test_integrated_editor', 'test_startup_mode']
sources = [*ROOT.glob('integrated_editor_*.py'), ROOT/'main.py', ROOT/'tests/test_integrated_editor.py']
source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
with (OUT/'tests.txt').open('w', encoding='utf-8') as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
report = dict(passed=result.wasSuccessful(), tests=result.testsRun, failures=len(result.failures),
    errors=len(result.errors), skipped=len(result.skipped), network_and_credentials_forbidden=True,
    sqlite_outside_sandbox_forbidden=True, selected_tests=names,
    source_sha256=source_hashes,
    source_unchanged_during_run=all(source_hashes[str(p.relative_to(ROOT))] == hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sources))
(OUT/'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(dict(evidence=str(OUT), **report), ensure_ascii=True))
if not result.wasSuccessful():
    print((OUT/'tests.txt').read_text('utf-8'))
raise SystemExit(0 if result.wasSuccessful() else 1)
