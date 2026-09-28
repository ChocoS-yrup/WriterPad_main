"""Freeze current dirty sources and build separately; never install or launch live."""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT/'_evidence/windows-integrated-editor-candidate-20260913'
PRIOR = ROOT/'_evidence/windows-normal-editor-candidate-20260913/source-manifest.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    HERE.mkdir(exist_ok=False)
    previous = json.loads(PRIOR.read_text('utf-8'))
    dependencies = {name: importlib.metadata.version(name) for name in previous['dependencies']}
    assert dependencies == previous['dependencies'], 'BUILD_DEPENDENCIES_CHANGED'
    names = {row['path'] for row in previous['files']}
    names.update(p.name for p in ROOT.glob('integrated_editor_*.py'))
    names.update(['tests/test_integrated_editor.py', 'scripts/run_integrated_editor_isolated.py',
                  'scripts/build_integrated_editor_candidate.py', 'scripts/verify_integrated_editor_candidate.py'])
    source = HERE/'source'
    source.mkdir()
    rows = []
    for name in sorted(names):
        original = (ROOT/name).read_bytes()
        data = original
        if name == 'integrated_editor_plan.py':
            assert data.count(b'INTEGRATED_EDITOR_ONLY = False') == 1
            data = data.replace(b'INTEGRATED_EDITOR_ONLY = False', b'INTEGRATED_EDITOR_ONLY = True')
        target = source/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append(dict(path=name, bytes=len(data), sha256=sha(data), workspace_sha256=sha(original)))
    manifest = dict(candidate_id='windows-staging-integrated-editor-20260913-v1',
        created_at_utc=datetime.now(timezone.utc).isoformat(), files=rows, dependencies=dependencies,
        snapshot_sha256=sha(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()),
        installed=False, source_flag_remains_false=True, live_execution_approved=False)
    (HERE/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    for name in ('build','candidate','cache','temp'):
        (HERE/name).mkdir()
    env = os.environ.copy()
    env.update(PYINSTALLER_CONFIG_DIR=str(HERE/'cache'), TEMP=str(HERE/'temp'), TMP=str(HERE/'temp'),
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
        WRITERPAD_CONTRACT_REVIEW_UI='0', WRITERPAD_GENERAL_TEST_GATE_UI='0')
    env['PATH'] = os.pathsep.join(p for p in env.get('PATH','').split(os.pathsep) if 'codex-runtimes' not in p.casefold())
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--workpath', str(HERE/'build'),
               '--distpath', str(HERE/'candidate'), '작가님힘내세요.spec']
    print('Building frozen integrated candidate; installation unchanged.', flush=True)
    with (HERE/'build.log').open('w',encoding='utf-8') as log:
        result = subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (HERE/'build-result.json').write_text(json.dumps(dict(exit_code=result.returncode, installed=False))+'\n',encoding='utf-8')
    print('Build exit:', result.returncode, flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
