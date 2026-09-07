"""Metadata-only observation of existing predicates. No network, refresh or dispatch."""
from datetime import datetime, timezone

LABELS = {
    'context_matches': '작품·계정 연결이 조회 기준과 다릅니다',
    'gate_allowed': '준비 상태 조회는 관문이 닫혀 있어야 합니다',
    'handshake_fresh': '현재 연결의 계약 확인이 유효하지 않습니다',
    'authority_allowed': '구조 기준 또는 작품 상태 확인이 필요합니다',
    'auth_unblocked': '인증이 차단된 상태입니다',
    'not_shutting_down': '앱이 종료 중입니다',
    'document_worker_idle': '문서 동기화 작업이 남아 있습니다',
    'structure_worker_idle': '구조 동기화 작업이 남아 있습니다',
    'server_work_idle': '다른 서버 작업이 실행 중입니다',
    'pull_idle': '서버 구조 수신이 진행 중입니다',
    'observation_current': '조회 도중 연결 또는 쓰기 기준이 변경됐습니다',
}

AUTHORITY_REASONS = frozenset({'initial', 'configure', 'release', 'selection',
    'pull_start', 'pull_retry', 'pull_start_failed', 'accepted', 'blocked',
    'sign_in', 'session_recovery'})


def note_authority_transition(manager, reason):
    """Caller holds _contract_lock. No identities, paths or raw errors stored."""
    manager._authority_transition_sequence = getattr(manager, '_authority_transition_sequence', 0) + 1
    manager._authority_transition_reason = reason if isinstance(reason, str) and reason in AUTHORITY_REASONS else 'selection'
    manager._authority_transition_generation = manager._v2_context_generation
    manager._authority_transition_auth_generation = manager._auth_context_generation


def sanitize_coordination(value):
    value = value if isinstance(value, dict) else {}
    result = {k: value.get(k) is True for k in ('pulling', 'pull_pending',
        'pull_worker_present', 'pull_worker_current', 'review_preparing')}
    for key in ('transition_sequence', 'transition_generation',
                'transition_auth_generation', 'context_generation',
                'auth_generation', 'write_epoch'):
        number = value.get(key)
        result[key] = number if type(number) is int and number >= 0 else 0
    state = value.get('authority_state')
    result['authority_state'] = state if state in ('contract', 'legacy', 'unknown', 'blocked', 'unset') else 'unrecognized'
    reason = value.get('authority_reason')
    result['authority_reason'] = reason if isinstance(reason, str) and reason in AUTHORITY_REASONS else 'initial'
    return result


def coordination_snapshot(manager):
    """Read under the same lock as entry reservation and authority transitions."""
    with manager._contract_lock:
        coordinator = manager._current_pull_coordinator(create=False) or {}
        worker = manager._v2_pull_worker
        return sanitize_coordination({
            'authority_state': manager._v2_structure_authority if manager._v2_structure_authority is not None else 'unset',
            'authority_reason': getattr(manager, '_authority_transition_reason', 'initial'),
            'transition_sequence': getattr(manager, '_authority_transition_sequence', 0),
            'transition_generation': getattr(manager, '_authority_transition_generation', 0),
            'transition_auth_generation': getattr(manager, '_authority_transition_auth_generation', 0),
            'context_generation': manager._v2_context_generation,
            'auth_generation': manager._auth_context_generation,
            'write_epoch': manager._contract_write_epoch,
            'pulling': bool(coordinator.get('pulling')),
            'pull_pending': bool(coordinator.get('pull_pending')),
            'pull_worker_present': worker is not None,
            'pull_worker_current': worker is not None and manager._v2_pull_worker_identity == manager._v2_pull_identity(),
            'review_preparing': bool(getattr(manager, '_review_execution_busy', False)),
        })


def observe_readiness(manager, expected_key, *, executing=False):
    """Sample each predicate once under its existing lock; detect invalidation."""
    with manager._contract_lock:
        started = datetime.now(timezone.utc).isoformat()
        start_key = manager._contract_context_key()
        start_epoch = manager._contract_write_epoch
        gate_open = bool(manager.contract_path_enabled())
        handshake = bool(manager.contract_handshake_is_fresh())
        authority = manager._contract_authority_observation()
        server_work = int(manager._active_server_syncs)
        coordination = coordination_snapshot(manager)
        conditions = {
            'context_matches': expected_key == start_key,
            'gate_allowed': not gate_open or bool(executing),
            'handshake_fresh': handshake,
            'authority_allowed': authority['allowed'],
            'auth_unblocked': not bool(manager._auth_retry_blocked),
            'not_shutting_down': not bool(manager._shutting_down),
            'document_worker_idle': manager._v2_worker is None,
            'structure_worker_idle': manager._v2_structure_worker is None,
            'server_work_idle': server_work == 0,
            'pull_idle': not coordination['pulling'] and not coordination['pull_worker_current'],
        }
        conditions['observation_current'] = (
            start_key == manager._contract_context_key()
            and start_epoch == manager._contract_write_epoch
        )
        return {'observed_at': started, 'conditions': conditions,
                'all_conditions_met': all(conditions.values()),
                'failed_conditions': [k for k,v in conditions.items() if not v],
                'gate_open': gate_open, 'active_server_work_count': server_work,
                'authority': authority,
                'coordination': coordination,
                'stale': not conditions['observation_current']}


def format_readiness(report):
    observation = report['observation']
    lines = ['현재 준비 상태 조건: ' + ('충족' if observation['all_conditions_met'] else '불충족'),
             '관찰 시각: ' + observation['observed_at']]
    lines.extend(LABELS[k] for k in observation['failed_conditions'])
    if report['already_executed']:
        lines.append('기존 실행 기록: ' + report['execution']['state'] + ' / 재실행 불가')
    else:
        lines.append('기존 실행 기록 없음. 이 조회는 실행 승인이 아닙니다.')
    recovery = report.get('http_zero_recovery')
    if recovery:
        lines.append('HTTP 0 복구 로컬 후보: ' + ('해당' if recovery['local_candidate'] else '해당 없음'))
        if recovery['round']:
            lines.append('별도 복구 회차: ' + recovery['round']['state']
                         + ' / HTTP 시도 ' + str(recovery['round']['http_attempts']))
            lines.append('복구 승인 기록 있음. 새 실행·재시도 승인이 아닙니다.')
        if recovery['receipt_status']:
            lines.append('별도 복구 영수증: ' + recovery['receipt_status'])
        lines.append('복구 실행 승인 없음. 서버 원장 확인은 이 조회에 포함되지 않습니다.')
    if report['stale']:
        lines.append('조회 도중 연결이 변경됐습니다. 오래된 관찰입니다.')
    resume = report.get('post_coordination_resume')
    if resume:
        lines.append('조정 후 추가 1회 로컬 후보: ' + ('해당' if resume['local_candidate'] else '해당 없음'))
        if resume['round']:
            lines.append('조정 후 추가 회차: ' + resume['round']['state']
                         + ' / HTTP 시도 ' + str(resume['round']['http_attempts']))
        lines.append('기존 두 중단 기록은 보존합니다. 이 조회는 추가 실행 승인이 아닙니다.')
    lines.append('송신·관문 변경·새 서버 조회 없음. 과거 중단 원인을 복원하지 않습니다.')
    return '\n'.join(lines)
