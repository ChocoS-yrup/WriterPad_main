"""Candidate-only composition. Startup opens local state without authentication."""
import hashlib
import os
from pathlib import Path
import sys
from PyQt6.QtCore import QLockFile
from PyQt6.QtWidgets import QApplication

import normal_editor_plan as plan
from general_validation_service import require


def runtime_paths():
    from normal_editor_build import NORMAL_EDITOR_ONLY
    from general_validation_ui import INSTALL_ROOT
    from body_validation_service import DEVICE_ID
    require(NORMAL_EDITOR_ONLY and getattr(sys,'frozen',False),'NORMAL_CANDIDATE_REQUIRED')
    require(Path(sys.executable).parent.resolve()==INSTALL_ROOT.resolve(),'INSTALLATION_PATH_CHANGED')
    require(not any(os.environ.get(k) for k in ('ANTIGRAVITY_PROFILE','ANTIGRAVITY_ROOT_DIR',
        'ANTIGRAVITY_APP_DATA_DIR','ANTIGRAVITY_FORCE_PROJECT_ID','ANTIGRAVITY_INSTANCE_KEY')),'RUNTIME_OVERRIDE_REFUSED')
    appdata = Path(os.environ['LOCALAPPDATA'])/'AntigravityWriter'
    require((appdata/'.device_id').read_text('utf-8').strip()==DEVICE_ID,'DEVICE_CHANGED')
    return appdata,INSTALL_ROOT,DEVICE_ID


def open_local():
    from sync_v2_store import SyncV2Store
    from project_manager_writing import WritingProjectManager
    from normal_editor_local import LocalEditor
    from general_validation_control import pending_controls
    appdata,root,device = runtime_paths()
    require(not pending_controls(appdata/'general-validation-20260912') and not pending_controls(
        appdata/'general-editor-20260913',plan_id='general-editor-20260913-v1'),'CONTROL_RESTORE_REQUIRED')
    store = SyncV2Store.open_existing_current_schema(str(appdata/'sync_v2.sqlite3'))
    wpm = object.__new__(WritingProjectManager)
    wpm.current_project = plan.PROJECT_NAME
    wpm.writing_root_path = str(root/'작품목록'/plan.PROJECT_NAME/'집필모드')
    return LocalEditor(store=store,wpm=wpm,directory=appdata/plan.DIRECTORY,device_id=device)


def open_network(ticket,local):
    from cloud_config import load_cloud_client_config
    from general_validation_transport import GeneralTransport
    from sync_manager import SyncManager
    from security_manager import SecurityManager
    from normal_editor_network import NormalNetworkService
    appdata,root,device = runtime_paths()
    require(local.directory==appdata/plan.DIRECTORY and local.root==root/'작품목록'/plan.PROJECT_NAME/'집필모드'
        and Path(local.store.db_path).resolve()==(appdata/'sync_v2.sqlite3').resolve(),'NORMAL_RUNTIME_BINDING_CHANGED')
    config = load_cloud_client_config(Path(sys._MEIPASS))
    require(config.is_ready and config.url==plan.STAGING_URL,'STAGING_REQUIRED')
    ticket.check()
    transport = GeneralTransport(ticket)
    client = None
    try:
        client = SyncManager.create_supabase_client(config,http_transport=transport,preserve_rejected_session=True)
        ticket.check()
        require(client is not None and getattr(client,'_antigravity_authenticated',False),'SAVED_LOGIN_REQUIRED')
        user,session = client.auth.get_user().user,client.auth.get_session()
        require(user is not None and session is not None and str(user.id)==str(session.user.id),'AUTH_SESSION_CHANGED')
        transport.bind_verified()
        token_sha = hashlib.sha256(session.access_token.encode()).digest()
        def current():
            ticket.check()
            access,_ = SecurityManager.get_supabase_session()
            return hashlib.sha256(access.encode()).digest()==token_sha and (appdata/'.device_id').read_text('utf-8').strip()==device
        return NormalNetworkService(local=local,client=client,transport=transport,ticket=ticket,device_id=device,
            account_id=str(user.id),access_token=session.access_token,session_current=current)
    except Exception:
        if client:SyncManager._close_supabase_client(client)
        else:transport.close()
        SyncManager.release_auth_lease()
        raise


def run_normal_editor_app(argv):
    from normal_editor_ui import NormalWritingWidget
    app = QApplication([argv[0]])
    if '--normal-editor-startup-smoke-test' in argv:
        # Exercise the packaged product UI without installed files, Auth or DB.
        from types import SimpleNamespace
        from PyQt6.QtCore import QTimer
        def forbidden(*args,**kwargs):
            raise AssertionError('SMOKE_MUST_NOT_EXECUTE')
        local = SimpleNamespace(expected_disk=plan.INITIAL_CONTENT,
            draft=lambda:plan.INITIAL_CONTENT,write=forbidden,
            preserve_draft=lambda text:None,status=lambda:'시작 검사 · 통신 없음')
        window = NormalWritingWidget(local,network_factory=forbidden,closer=forbidden,is_active=lambda:True)
        window.show()
        QTimer.singleShot(0,window.close)
        return app.exec()
    appdata,_,_ = runtime_paths()
    lock = QLockFile(str(appdata/'general-validation-execution.lock'))
    lock.setStaleLockTime(0)
    require(lock.tryLock(0),'ANOTHER_VALIDATION_RUNNING')
    try:
        local = open_local()
        window = NormalWritingWidget(local,network_factory=open_network)
        window.show()
        return app.exec()
    finally:lock.unlock()
