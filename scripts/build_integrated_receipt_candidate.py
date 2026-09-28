"""Build a separately identified patch package using existing pinned tooling.

The wire BUILD_ID remains v1 so the preserved run and immutable batches retain
their binding. The candidate/package ID and EXE hash identify this revision.
Never installs or launches the user's active workspace.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT/'_evidence/windows-integrated-receipt-candidate-20260913'
PRIOR = ROOT/'_evidence/windows-integrated-editor-candidate-20260913'
PACKAGE = 'windows-staging-integrated-editor-20260913-receipt-v2'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    value.HERE = OUT
    return value

def build():
    prior = json.loads((PRIOR/'source-manifest.json').read_text('utf-8'))
    changed = {r['path'] for r in prior['files'] if sha(ROOT/r['path']) != r['workspace_sha256']}
    assert changed == {'integrated_editor_sync.py','tests/test_integrated_editor.py'}, changed
    code = module('build_integrated_editor_candidate').main()
    if code:
        return code
    path = OUT/'source-manifest.json'
    manifest = json.loads(path.read_text('utf-8'))
    manifest.update(candidate_id=PACKAGE, wire_build_id='windows-staging-integrated-editor-20260913-v1',
        supersedes_candidate_sha256='35057621d198a6d523e092e6c92942fd410ef8e027db5da4aaa1c3be37490612',
        changed_source_files=sorted(changed), builder_sha256=sha(Path(__file__)),
        compatibility='same wire build binding; new package ID and EXE hash; no existing state rewritten')
    path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return 0

def verify():
    verifier = module('verify_integrated_editor_candidate')
    verifier.SOURCE = OUT/'source'
    verifier.main()
    return 0

if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit({'build':build,'verify':verify}[sys.argv[1]]())
