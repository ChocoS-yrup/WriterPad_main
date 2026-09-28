"""Current app client's read-only proof. No claim, DB writes or session refresh."""
import copy
import hashlib
import re
from datetime import datetime, timezone

from sync_contract import SyncContractError


def now():
    return datetime.now(timezone.utc).isoformat()


def format_recovery_read(report):
    if report['stale']:
        result = '조회 중 연결·로그인·관문 또는 쓰기 기준이 바뀌었습니다. 결과를 승인에 사용할 수 없습니다.'
    elif report['error_code']:
        result = '로그인 원장 조회 중단: ' + report['error_code']
    else:
        result = '현재 로그인 원장 조회 완료: ' + ', '.join(f'{k}={v}' for k,v in report['proof']['counts'].items())
    return result + '\n복구 회차·송신·관문 변경 없음. 실행 승인이 아닙니다. 결과를 내보내 주세요.'


class RecoveryReadOnlyManagerMixin:
    def inspect_recovery_server_readonly(self):
        return self._inspect_recovery_server_readonly()

    def inspect_post_coordination_server_readonly(self):
        return self._inspect_recovery_server_readonly(post_coordination=True)

    def _inspect_recovery_server_readonly(self, *, post_coordination=False):
        from contract_http_zero_recovery import read_server_ledger
        from reviewed_contract_sender import REVIEWED_BATCH, REVIEWED_REQUEST_SHA256
        with self._contract_lock:
            if getattr(self, '_recovery_read_busy', False) or getattr(self, '_review_execution_busy', False):
                raise SyncContractError('RECOVERY_READ_BUSY')
            cache_name = '_last_post_coordination_read' if post_coordination else '_last_recovery_read'
            setattr(self, cache_name, None)
            if not self.is_v2_enabled or self.contract_path_enabled():
                raise SyncContractError('RECOVERY_READ_CLOSED_PROJECT_REQUIRED')
            key = self._contract_context_key()
            pull_identity = self._v2_pull_identity()
            epoch = self._contract_write_epoch
            store, client = self._v2_store, self.supabase
            root = self._v2_wpm.writing_root_path
            envelope = self.reverse_contract_review()
            batch = envelope['request']['batch']['batch_id']
            if (batch,envelope['request_sha256']) != (REVIEWED_BATCH,REVIEWED_REQUEST_SHA256):
                raise SyncContractError('RECOVERY_READ_ORIGINAL_REQUIRED')
            ledger = store
            if post_coordination:
                from contract_post_coordination_resume import ResumeLedger
                ledger = ResumeLedger(store)
            recovery = ledger.inspect_http_zero_recovery(key[4],envelope)
            if not recovery['local_candidate']:
                raise SyncContractError('RECOVERY_READ_ORIGINAL_REQUIRED')
            token = getattr(client, '_antigravity_access_token', '')
            if not isinstance(token,str) or not token:
                raise SyncContractError('RECOVERY_READ_LOGIN_REQUIRED')
            self._recovery_read_busy = True
        report = {'kind':'contract_recovery_readonly_observation','format_version':1,
                  'started_at':now(),'finished_at':None,'project_id':envelope['request']['project_id'],
                  'batch_id':batch,'request_sha256':envelope['request_sha256'],
                  'proof':None,'error_code':None,'stale':False,'ledger_empty':None,
                  'original_verified':True,'recovery_round_present':False,'execution_authorized':False,
                  'scope':'Current app client auth.get_user with captured JWT plus one fresh ledger RPC; no claim, refresh, gate setter, DB write or full baseline validation.'}
        if post_coordination:
            from contract_post_coordination_resume import POLICY, PARENT_ID
            report.update(kind='post_coordination_readonly_observation', policy=POLICY,
                          parent_recovery_id=PARENT_ID, preserved_history_verified=True)

        def changed():
            return (key != self._contract_context_key() or epoch != self._contract_write_epoch
                    or pull_identity != self._v2_pull_identity()
                    or self._v2_store is not store or self.supabase is not client
                    or getattr(client,'_antigravity_access_token',None) != token
                    or self.contract_path_enabled() or getattr(self,'_review_execution_busy',False))
        try:
            # An explicit captured JWT prevents get_user from calling get_session
            # (which can refresh). No token is exported or copied outside this app.
            user = getattr(client.auth.get_user(jwt=token),'user',None)
            user_id = getattr(user,'id',None)
            if (not isinstance(user_id,str)
                    or hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:16] != envelope['account_marker']):
                raise SyncContractError('RECOVERY_READ_ACCOUNT_MISMATCH')
            with self._contract_lock:
                if changed():
                    raise SyncContractError('RECOVERY_READ_CONTEXT_CHANGED')
            proof = read_server_ledger(client,envelope,self._response_data)
            report['proof'] = copy.deepcopy(proof)
            report['ledger_empty'] = not any(proof['counts'].values())
        except Exception as error:
            code = getattr(error,'code','RECOVERY_READ_FAILED')
            report['error_code'] = code if isinstance(code,str) and re.fullmatch('[A-Z0-9_]{1,80}',code) else 'RECOVERY_READ_FAILED'
        finally:
            with self._contract_lock:
                try:
                    report['stale'] = changed()
                    current = ledger.inspect_http_zero_recovery(key[4],envelope)
                    report['original_verified'] = current['original_verified']
                    report['recovery_round_present'] = current['round'] is not None
                    report['stale'] = report['stale'] or not current['local_candidate']
                    report['finished_at'] = now()
                    if post_coordination:
                        report['preserved_history_verified'] = current['original_verified']
                    setattr(self, cache_name, (copy.deepcopy(report),key,root))
                finally:
                    self._recovery_read_busy = False
        return report

    def export_recovery_server_readonly(self, destination):
        return self._export_contract_observation(destination, '_last_recovery_read')

    def export_post_coordination_server_readonly(self, destination):
        return self._export_contract_observation(destination, '_last_post_coordination_read')

    def launch_recovery_server_readonly(self, *, post_coordination=False):
        from PyQt6.QtCore import QThread, pyqtSignal
        if getattr(self,'_recovery_read_worker',None) is not None:
            raise SyncContractError('RECOVERY_READ_BUSY')
        manager = self
        class Worker(QThread):
            resultReady = pyqtSignal(object)
            def run(self):
                try:
                    action = manager.inspect_post_coordination_server_readonly if post_coordination else manager.inspect_recovery_server_readonly
                    self.resultReady.emit(action())
                except Exception as error:
                    code = getattr(error,'code','RECOVERY_READ_FAILED')
                    self.resultReady.emit({'stale':False,'error_code':code if isinstance(code,str) and re.fullmatch('[A-Z0-9_]{1,80}',code) else 'RECOVERY_READ_FAILED'})
        worker = Worker(self)
        self._recovery_read_worker = worker
        def finished():
            if self._recovery_read_worker is worker:
                self._recovery_read_worker = None
        worker.finished.connect(finished)
        worker.finished.connect(worker.deleteLater)
        return worker
