"""Preparation component: exact requests, one-use budgets and caller-owned grants.

No default network transport, authentication, UI, gate change, or auto recovery.
Live composition must supply independently verified authority and local checks.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID

import httpx

from body_validation_transport import strict_json
from general_validation_plan import PROJECT_ID, STAGING_URL, DOCUMENT_ID, STAGES, document_request, order_request
from sync_contract import validate_atomic_structure_response, validate_document_commit_response


class GeneralValidationDenied(RuntimeError):
    pass


class DurableExecutionJournal:
    """Exclusive, durable stage reservation; never removes or reopens old runs.

    The owning UI selects a private directory and a fixed plan/stage filename.
    Closing an interrupted run preserves evidence and does not permit a retry.
    """
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._stream = self.path.open('xb')
        try:
            self.append({'event':'reserved'})
        except Exception:
            self._stream.close()
            raise

    def append(self, event):
        # Never accept payloads, bearer tokens, account IDs or arbitrary errors.
        allowed = {'event','sequence','method','path','request_sha256','outcome'}
        if set(event)-allowed:
            raise GeneralValidationDenied('PRIVATE_DATA_NOT_ALLOWED_IN_JOURNAL')
        data = (json.dumps(event,sort_keys=True,separators=(',',':'))+'\n').encode()
        with self._lock:
            self._stream.write(data)
            self._stream.flush()
            os.fsync(self._stream.fileno())

    def close(self):
        with self._lock:
            self._stream.close()


class ForegroundAuthority:
    """An injected check must include actual account/binding/gates/foreground state.

    Construction is not authentication. The component rechecks caller authority
    before dispatch and before accepting the response; it cannot revive itself.
    """
    def __init__(self, *, account_id, local_key, access_token, check_current,
                 clock=time.monotonic, lifetime=300):
        UUID(account_id)
        if not local_key or not access_token or not callable(check_current) or not 0 < lifetime <= 300:
            raise GeneralValidationDenied('MISSING_AUTHORITY')
        self.account_id, self.local_key = account_id, local_key
        self._bearer = hashlib.sha256(('Bearer '+access_token).encode()).digest()
        self._check_current, self._clock = check_current, clock
        self._deadline = clock()+lifetime
        self._cancelled = threading.Event()
        self.check()

    def cancel(self):
        self._cancelled.set()

    def check(self):
        if self._cancelled.is_set() or self._clock() >= self._deadline:
            raise GeneralValidationDenied('FOREGROUND_AUTHORITY_ENDED')
        if self._check_current(self.account_id, self.local_key, PROJECT_ID) is not True:
            self.cancel()
            raise GeneralValidationDenied('ACCOUNT_BINDING_OR_AUTHORITY_CHANGED')

    def check_bearer(self, request):
        self.check()
        actual = hashlib.sha256(request.headers.get('authorization','').encode()).digest()
        if actual != self._bearer:
            self.cancel()
            raise GeneralValidationDenied('BEARER_CHANGED')


@dataclass(frozen=True)
class FrozenRequest:
    method: str
    url: str
    body: bytes

    @classmethod
    def capture(cls, request):
        value = cls(request.method, str(request.url), bytes(request.content))
        value.validate_scope()
        return value

    @property
    def sha256(self):
        return hashlib.sha256((self.method+'\n'+self.url+'\n').encode()+self.body).hexdigest()

    def validate_scope(self):
        url = httpx.URL(self.url)
        if (url.scheme != 'https' or url.host != httpx.URL(STAGING_URL).host
                or url.port not in (None,443) or url.userinfo or url.fragment
                or url.raw_path.split(b'?')[0] != url.path.encode('ascii')):
            raise GeneralValidationDenied('ENDPOINT_OR_PATH_CHANGED')
        params = list(url.params.multi_items())
        if self.method == 'GET':
            if (url.path not in ['/rest/v1/'+t for t in ('projects','project_sync_settings','documents','folders','tree_orders')]
                    or self.body or sorted(params) != sorted([('select','*'),('project_id','eq.'+PROJECT_ID)])):
                raise GeneralValidationDenied('UNREVIEWED_READ')
        elif self.method == 'POST':
            if url.path == '/rest/v1/rpc/get_sync_handshake' and not params:
                from sync_contract import CANONICAL_CONTRACT_SHA256
                if strict_json(self.body) != {'p_project_id':PROJECT_ID,
                                              'p_contract_sha256':CANONICAL_CONTRACT_SHA256}:
                    raise GeneralValidationDenied('HANDSHAKE_SCOPE_CHANGED')
                return
            if params or url.path not in ('/rest/v1/rpc/document_commit','/rest/v1/rpc/atomic_structure_commit'):
                raise GeneralValidationDenied('UNREVIEWED_RPC')
            data = strict_json(self.body)
            if not isinstance(data,dict) or set(data) != {'p_request'}:
                raise GeneralValidationDenied('RPC_WRAPPER_CHANGED')
            req = data['p_request']
            if (not isinstance(req,dict) or req.get('project_id') != PROJECT_ID
                    or req.get('project_sync_mode') != 'ID_BASED' or req.get('migration_epoch') != 1):
                raise GeneralValidationDenied('CONTRACT_PROJECT_CHANGED')
            if url.path.endswith('/document_commit'):
                intents = req.get('ordered_intents',[])
                if len(intents) != 1 or intents[0].get('document_id') != DOCUMENT_ID:
                    raise GeneralValidationDenied('DOCUMENT_CHANGED')
                intent, batch = intents[0], req.get('batch',{})
                stage = next((s for s in STAGES if s.base_revision == intent.get('base_revision')), None)
                if stage is None:
                    raise GeneralValidationDenied('STAGE_CHANGED')
                expected = document_request(stage.name, batch.get('writer_device_id'),
                    operation_id=intent.get('operation_id'), batch_id=batch.get('batch_id'),
                    client_build_id=batch.get('client_build_id'))
            else:
                intents, batch = req.get('ordered_intents',[]), req.get('batch',{})
                if len(intents) != 1 or not isinstance(intents[0],dict):
                    raise GeneralValidationDenied('ORDER_INTENTS_CHANGED')
                expected = order_request(batch.get('writer_device_id'),
                    operation_id=intents[0].get('operation_id'), batch_id=batch.get('batch_id'),
                    client_build_id=batch.get('client_build_id'))
            if req != expected:
                raise GeneralValidationDenied('CONTRACT_PAYLOAD_CHANGED')
        else:
            raise GeneralValidationDenied('METHOD_REFUSED')


class GeneralHTTPBoundary(httpx.BaseTransport):
    """One ordered occurrence per frozen request, including SDK duplicate sends.

    A failure ends this boundary. Live composition must use a fixed stage path
    in DurableExecutionJournal so a new object cannot restart a prior attempt.
    """
    def __init__(self, *, authority, requests, check_local, persist_attempt, inner):
        if not requests or not callable(check_local) or not callable(persist_attempt):
            raise GeneralValidationDenied('MISSING_EXECUTION_SCOPE')
        self._authority, self._requests = authority, tuple(requests)
        for req in self._requests:
            req.validate_scope()
        self._check_local, self._persist_attempt, self._inner = check_local, persist_attempt, inner
        self._lock = threading.RLock()
        self._index = 0
        self._stopped = False

    @property
    def consumed(self):
        return self._index

    def handle_request(self, request):
        # Serialize a single window's requests, including the response boundary.
        with self._lock:
            response = None
            try:
                if self._stopped or self._index >= len(self._requests):
                    raise GeneralValidationDenied('SCOPE_FINISHED_OR_STOPPED')
                self._authority.check_bearer(request)
                if any(h in request.headers for h in ('range','range-unit')):
                    raise GeneralValidationDenied('PARTIAL_SNAPSHOT_REFUSED')
                if any(request.headers.get(h,'public') != 'public' for h in ('accept-profile','content-profile')):
                    raise GeneralValidationDenied('SCHEMA_CHANGED')
                frozen = type(self._requests[self._index]).capture(request)
                if frozen != self._requests[self._index]:
                    raise GeneralValidationDenied('REQUEST_BYTES_OR_ORDER_CHANGED')
                if self._check_local() is not True:
                    raise GeneralValidationDenied('LOCAL_BASELINE_OR_QUEUE_CHANGED')
                # Persist before the one-use budget is spent at the wire boundary.
                # Any persistence failure stops the session before network access.
                self._persist_attempt({'event':'http_attempt','sequence':self._index+1,
                                       'method':request.method,'path':request.url.path,
                                       'request_sha256':frozen.sha256})
                self._authority.check_bearer(request)
                if self._check_local() is not True:
                    raise GeneralValidationDenied('LOCAL_CHANGED_BEFORE_WIRE')
                self._index += 1
                response = self._inner.handle_request(request)
                response.read()
                if self._stopped:
                    raise GeneralValidationDenied('SCOPE_FINISHED_OR_STOPPED')
                self._authority.check_bearer(request)
                if self._check_local() is not True:
                    raise GeneralValidationDenied('LOCAL_CHANGED_DURING_REQUEST')
                if not 200 <= response.status_code < 300:
                    raise GeneralValidationDenied('HTTP_RESULT_NOT_SUCCESS')
                if request.method == 'POST' and request.url.path.endswith('/get_sync_handshake'):
                    from sync_contract import read_handshake_compatibility, require_server_compatibility
                    result = strict_json(response.content)
                    if isinstance(result, list) and len(result) == 1:
                        result = result[0]
                    if not isinstance(result,dict) or (result.get('project_id'), result.get('project_sync_mode'),
                            result.get('migration_epoch')) != (PROJECT_ID,'ID_BASED',1):
                        raise GeneralValidationDenied('HANDSHAKE_PROJECT_CHANGED')
                    require_server_compatibility(**read_handshake_compatibility(result))
                elif request.method == 'POST':
                    contract = strict_json(request.content)['p_request']
                    require_exact_result(contract, strict_json(response.content),
                        [i['base_revision']+1 for i in contract['ordered_intents']])
                return response
            except Exception:
                self._stopped = True
                if response is not None:
                    response.close()
                raise

    def close(self):
        self._stopped = True
        self._inner.close()


def require_exact_result(request, response, revisions):
    """Add planned exact revisions to the ordinary product receipt validators."""
    validator = (validate_document_commit_response if request['kind']=='document_commit_request'
                 else validate_atomic_structure_response)
    validator(request, response)
    if (response.get('applied') is not True or response.get('status') != 'committed'
            or [r.get('result_revision') for r in response.get('results',[])] != list(revisions)):
        raise GeneralValidationDenied('UNEXPECTED_RECEIPT_OR_REVISION')
    return response


def check_store_binding(store, local_key):
    """Read-only binding check; never opens gates or rewrites a checkpoint."""
    project = store.get_project(local_key)
    if not project or (project['project_id'],project['project_sync_mode'],project['migration_epoch']) != (PROJECT_ID,'ID_BASED',1):
        raise GeneralValidationDenied('LOCAL_SERVER_BINDING_CHANGED')
    return project
