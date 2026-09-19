"""Offline ID_BASED validation proposal. This module never grants live access."""
from dataclasses import dataclass
import hashlib
from uuid import UUID, uuid5

from general_test_gate_target import PROJECT_ID, PROJECT_NAME, STAGING_URL
from sync_contract import build_document_commit_request, build_atomic_structure_request

PLAN_ID = 'general-body-20260912-v1'
IPAD_LOCAL_ID = 'a9452cd1-4474-40b5-80ca-fbb7871e98e5'
DOCUMENT_ID = 'db8a3cc2-8b1a-5539-841c-042de34f5fd6'
PARENT_ID = 'f4c92790-d675-4970-b1fc-b90f3a929ffb'
ORDER_ID = '31eb06be-9cc9-55db-9a05-5882172474ce'
NAME = '일반본문검증 20260912.txt'
PATH = '메인/원고/' + NAME
BASE = '일반 본문 검증 20260912\n이 문서는 일반 동기화 시험용 합성 원고입니다.\n끝.\n'
IPAD = BASE + 'iPad 일반 검증 20260912\n'
WINDOWS = IPAD + 'Windows 일반 검증 20260912\n'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def identifier(label):
    return str(uuid5(UUID(PROJECT_ID), PLAN_ID + '/' + label))


@dataclass(frozen=True)
class Stage:
    name: str
    sender: str
    base_revision: int
    result_revision: int
    content: str


STAGES = (
    Stage('windows_create', 'windows', 0, 1, BASE),
    Stage('ipad_update', 'ipad', 1, 2, IPAD),
    Stage('windows_update', 'windows', 2, 3, WINDOWS),
)


def document_request(stage_name, writer_device_id, *, operation_id=None, batch_id=None, client_build_id=None):
    stage = next((s for s in STAGES if s.name == stage_name), None)
    if stage is None:
        raise ValueError('UNKNOWN_GENERAL_STAGE')
    return build_document_commit_request(
        project_id=PROJECT_ID, project_sync_mode='ID_BASED', migration_epoch=1,
        writer_device_id=writer_device_id, document_id=DOCUMENT_ID,
        intent_kind='create' if stage.base_revision == 0 else 'update',
        base_revision=stage.base_revision, parent_folder_id=PARENT_ID,
        name=NAME, content=stage.content, is_deleted=False, structure_revision=1,
        operation_id=operation_id or identifier(stage_name + '/operation'),
        batch_id=batch_id or identifier(stage_name + '/batch'),
        client_build_id=client_build_id or ('windows-general-validation-preparation-20260912'
        if stage.sender == 'windows' else 'ipad-general-validation-preparation-20260912'),
    )


def order_request(writer_device_id, *, operation_id=None, batch_id=None, client_build_id=None):
    """Only after document revision 1 exists and the parent order is still []/1."""
    return build_atomic_structure_request(
        project_id=PROJECT_ID, project_sync_mode='ID_BASED', migration_epoch=1,
        writer_device_id=writer_device_id,
        batch_id=batch_id or identifier('windows_order/batch'),
        client_build_id=client_build_id or 'windows-general-validation-preparation-20260912',
        ordered_intents=[{
            'operation_id': operation_id or identifier('windows_order/operation'),
            'entity_kind':'tree_order', 'entity_id':ORDER_ID,
            'intent_kind':'reorder', 'base_revision':1,
            'payload':{'parent_folder_id':PARENT_ID, 'children':[DOCUMENT_ID]},
        }],
    )


def proposal():
    return {
        'plan_id':PLAN_ID, 'execution_authorized':False,
        'project_id':PROJECT_ID, 'project_name':PROJECT_NAME,
        'ipad_local_id':IPAD_LOCAL_ID, 'endpoint':STAGING_URL,
        'mode':'ID_BASED', 'epoch':1, 'document_id':DOCUMENT_ID,
        'parent_folder_id':PARENT_ID, 'name':NAME, 'relative_path':PATH,
        'structure_revision':1,
        'initial_document_must_be_absent':True,
        'parent_order':{'id':ORDER_ID,'before_revision':1,'before_children':[],
                        'after_revision':2,'after_children':[DOCUMENT_ID]},
        'stages':[{'stage':s.name,'sender':s.sender,'base_revision':s.base_revision,
                   'result_revision':s.result_revision,'bytes':len(s.content.encode()),
                   'sha256':digest(s.content),'content':s.content,
                   'batch_id':identifier(s.name+'/batch'),
                   'operation_id':identifier(s.name+'/operation')} for s in STAGES],
        'write_sequence':['windows:document_commit(create) once',
                          'windows:atomic_structure_commit(parent order) once',
                          'ipad:receive revision 1 and order 2; document_commit(update) once',
                          'windows:receive revision 2; document_commit(update) once',
                          'ipad:receive final revision 3'],
        'lease_requests':0, 'automatic_retries':0,
        'creation_and_order_are_separate_commits':True,
        'incomplete_creation_order_action':'Preserve the created document and all records; stop, no automatic delete or retry.',
        'runtime_device_and_account_binding_required':True,
        'server_absence_and_baseline_not_yet_verified':True,
    }
