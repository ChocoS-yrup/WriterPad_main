"""Inspect embedded code/resources, then run only isolated package smoke paths."""
import ast
import hashlib
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
from types import CodeType

from PyInstaller.archive.readers import CArchiveReader
import pefile

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT/'_evidence/windows-integrated-editor-candidate-20260913'
SOURCE = HERE/'source'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalize(code):
    if not isinstance(code, CodeType):
        return code
    return code.replace(co_filename='<verified>', co_consts=tuple(normalize(c) for c in code.co_consts))


def constant(path, name):
    tree = ast.parse(path.read_bytes())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id == name for t in n.targets))


def main():
    manifest = json.loads((HERE/'source-manifest.json').read_text('utf-8'))
    for row in manifest['files']:
        assert sha((SOURCE/row['path']).read_bytes()) == row['sha256'], row['path']
        assert sha((ROOT/row['path']).read_bytes()) == row['workspace_sha256'], row['path']
    exe = HERE/'candidate/작가님 힘내세요.exe'
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(n for n,e in archive.toc.items() if e[-1] == 'z'))
    modules = sorted(p.stem for p in SOURCE.glob('*.py') if p.stem in pyz.toc)
    for name in modules:
        assert normalize(pyz.extract(name)) == normalize(compile((SOURCE/(name+'.py')).read_bytes(),name,'exec')), name
    assert normalize(marshal.loads(archive.extract('main'))) == normalize(compile((SOURCE/'main.py').read_bytes(),'main','exec'))
    assert {'integrated_editor_plan','integrated_editor_runtime','integrated_editor_store',
            'integrated_editor_sync','integrated_editor_ui','integrated_editor_smoke','sync_contract',
            'text_editor','cloud_config','security_manager','normal_editor_network'} <= set(modules)
    assert constant(SOURCE/'integrated_editor_plan.py','INTEGRATED_EDITOR_ONLY') is True
    assert constant(ROOT/'integrated_editor_plan.py','INTEGRATED_EDITOR_ONLY') is False
    for module, flag in (('normal_editor_build','NORMAL_EDITOR_ONLY'),('general_editor_build','GENERAL_EDITOR_ONLY'),
                         ('general_validation_build','GENERAL_VALIDATION_ONLY'),('body_validation_build','BODY_VALIDATION_ONLY')):
        assert constant(SOURCE/(module+'.py'),flag) is False
    tree = ast.parse((SOURCE/'main.py').read_bytes())
    branch = next(n for n in tree.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id == 'INTEGRATED_EDITOR_ONLY')
    ordinary = next(n for n in tree.body if isinstance(n,ast.ImportFrom) and n.module == 'mode_assistant')
    assert branch.lineno < ordinary.lineno
    assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr == 'exit' for n in ast.walk(branch))
    data_names = ['style.qss','app_icon.ico','model_catalog.json','release_cloud_config.json']
    for name in data_names:
        assert archive.extract(name) == (SOURCE/name).read_bytes()
    config = json.loads(archive.extract('release_cloud_config.json'))
    assert config['supabase_url'] == 'https://mhpnszcorfzrvhyondxr.supabase.co'
    assert set(config) == {'supabase_url','supabase_publishable_key'}
    runtimes = ['MSVCP140.dll','MSVCP140_1.dll','MSVCP140_2.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll',
                'msvcp140_atomic_wait.dll','msvcp140_codecvt_ids.dll','vcruntime140_threads.dll']
    folded = {n.casefold() for n in archive.toc}
    assert all(n.casefold() in folded for n in runtimes)
    assert not any(n.startswith('icu') and '/' not in n and '\\' not in n for n in folded)
    assert not any(n.endswith(('.sqlite3','.db','.env')) or n.endswith('reviewed-execution.json') for n in folded)
    pe = pefile.PE(str(exe), fast_load=False)
    security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_SECURITY']]
    assert pe.FILE_HEADER.Machine == 0x8664
    report = dict(candidate_id=manifest['candidate_id'], exe_sha256=sha(exe.read_bytes()), exe_bytes=exe.stat().st_size,
        source_snapshot_sha256=manifest['snapshot_sha256'], packaged_modules_equal_frozen_source=modules,
        integrated_flag_true_only_in_candidate=True, ordinary_startup_preempted=True, staging_config_verified=True,
        data_files_equal=data_names, qt_runtime_dlls_verified=runtimes, pe_machine=hex(pe.FILE_HEADER.Machine),
        authenticode_present=bool(security.Size), installed=False, live_database_or_network_used=False)
    (HERE/'build-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('candidate_id','exe_sha256','exe_bytes','source_snapshot_sha256')}),flush=True)
    smokes = []
    for flag in ('--qt-import-smoke-test','--integrated-editor-startup-smoke-test'):
        sandbox = HERE/('smoke-'+flag.strip('-'))
        sandbox.mkdir(exist_ok=False)
        for name in ('appdata','root','temp'):
            (sandbox/name).mkdir()
        env = os.environ.copy()
        env.update(LOCALAPPDATA=str(sandbox/'appdata'),APPDATA=str(sandbox/'appdata'),
            ANTIGRAVITY_PROFILE='integrated-candidate-smoke',ANTIGRAVITY_APP_DATA_DIR=str(sandbox/'appdata'),
            ANTIGRAVITY_ROOT_DIR=str(sandbox/'root'),TEMP=str(sandbox/'temp'),TMP=str(sandbox/'temp'),
            QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',WRITERPAD_INTEGRATED_SMOKE_DIR=str(sandbox))
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH','').split(os.pathsep) if 'codex-runtimes' not in p.casefold())
        result = subprocess.run([str(exe),flag],cwd=sandbox,env=env,capture_output=True,timeout=60,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (sandbox/'stdout.log').write_bytes(result.stdout)
        (sandbox/'stderr.log').write_bytes(result.stderr)
        row = dict(flag=flag,exit_code=result.returncode,isolated_root_empty=not any((sandbox/'root').iterdir()),
                   isolated_appdata_empty=not any((sandbox/'appdata').iterdir()))
        if flag == '--integrated-editor-startup-smoke-test' and (sandbox/'startup-result.json').exists():
            row['startup'] = json.loads((sandbox/'startup-result.json').read_text('utf-8'))
        smokes.append(row)
        (HERE/'smoke-result.json').write_text(json.dumps(smokes,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        assert result.returncode == 0 and row['isolated_root_empty'] and row['isolated_appdata_empty'], row
        if flag == '--integrated-editor-startup-smoke-test':
            assert row['startup']['passed'] and not row['startup']['denied_attempts']
    print('Both isolated EXE startup checks passed. Not installed; no live validation.',flush=True)


if __name__ == '__main__':
    main()
