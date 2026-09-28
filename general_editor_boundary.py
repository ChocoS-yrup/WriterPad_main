"""Only the two Windows editor updates; old creation/order requests stay denied."""
import httpx
import general_editor_plan as plan
from body_validation_transport import strict_json
from general_validation_boundary import FrozenRequest, GeneralValidationDenied

class EditorRequest(FrozenRequest):
    def validate_scope(self):
        url = httpx.URL(self.url)
        if self.method == 'GET' or url.path == '/rest/v1/rpc/get_sync_handshake':
            return super().validate_scope()
        if (self.method != 'POST' or str(url) != plan.STAGING_URL+'/rest/v1/rpc/document_commit'):
            raise GeneralValidationDenied('EDITOR_RPC_REFUSED')
        data = strict_json(self.body)
        if not isinstance(data,dict) or set(data) != {'p_request'}:
            raise GeneralValidationDenied('EDITOR_WRAPPER_CHANGED')
        req = data['p_request']
        if not isinstance(req,dict):
            raise GeneralValidationDenied('EDITOR_CONTRACT_CHANGED')
        intents,batch = req.get('ordered_intents'),req.get('batch')
        if not isinstance(intents,list) or len(intents)!=1 or not isinstance(intents[0],dict) or not isinstance(batch,dict):
            raise GeneralValidationDenied('EDITOR_INTENT_CHANGED')
        intent = intents[0]
        stage = next((s for s in plan.WINDOWS_STAGES.values() if s.base_revision==intent.get('base_revision')),None)
        if stage is None:
            raise GeneralValidationDenied('EDITOR_STAGE_CHANGED')
        expected = plan.document_request(stage,writer_device_id=batch.get('writer_device_id'),
            operation_id=intent.get('operation_id'),batch_id=batch.get('batch_id'),client_build_id=batch.get('client_build_id'))
        if req != expected:
            raise GeneralValidationDenied('EDITOR_PAYLOAD_CHANGED')
