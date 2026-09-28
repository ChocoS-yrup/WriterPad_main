"""Fixed editor proposal; never grants runtime execution by itself."""
from general_validation_plan import (PROJECT_ID, PROJECT_NAME, STAGING_URL, DOCUMENT_ID,
    PARENT_ID, ORDER_ID, NAME, PATH, Stage, STAGES as PREVIOUS_STAGES, WINDOWS, digest)
from sync_contract import build_document_commit_request

PLAN_ID = 'general-editor-20260913-v1'
MANUAL = WINDOWS + 'Windows 일반 편집 검증 20260913\n'
IPAD = MANUAL + 'iPad 일반 편집 검증 20260913\n'
AUTO = IPAD + 'Windows 자동저장 검증 20260913\n'
STAGES = PREVIOUS_STAGES + (
    Stage('manual', 'windows', 3, 4, MANUAL),
    Stage('ipad', 'ipad', 4, 5, IPAD),
    Stage('auto', 'windows', 5, 6, AUTO),
)
WINDOWS_STAGES = {s.name:s for s in STAGES if s.name in ('manual','auto')}

def document_request(stage, *, writer_device_id, operation_id, batch_id, client_build_id):
    return build_document_commit_request(project_id=PROJECT_ID,project_sync_mode='ID_BASED',migration_epoch=1,
        writer_device_id=writer_device_id,document_id=DOCUMENT_ID,intent_kind='update',
        base_revision=stage.base_revision,parent_folder_id=PARENT_ID,name=NAME,content=stage.content,
        is_deleted=False,structure_revision=1,operation_id=operation_id,batch_id=batch_id,client_build_id=client_build_id)
