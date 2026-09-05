"""Bounded, background recovery of a saved login after transport failure."""
import time

from handshake_lifecycle import ContractDispatchPaused
from runtime_profile import is_forced_offline


class NetworkRecoveryMixin:
    def _session_restore_waiting(self):
        return bool(self.supabase and getattr(
            self.supabase, '_antigravity_restore_pending', False
        ) and not self._auth_retry_blocked)

    def request_session_recovery_async(self):
        with self._contract_lock:
            if (not self._session_restore_waiting() or self._shutting_down
                    or is_forced_offline()):
                return False
            # A successful restore changes the subject from empty to present.
            # All other lifetime fields must stay the same across that exchange.
            key = self._contract_context_key()[:-1]
            if getattr(self, '_session_recovery_ticket', None) is not None:
                return False
            if getattr(self, '_session_recovery_key', None) != key:
                self._session_recovery_key = key
                self._session_recovery_count = 0
                self._session_recovery_after = 0.0
            if time.monotonic() < self._session_recovery_after:
                return False
            ticket = object()
            self._session_recovery_ticket = ticket
            client = self.supabase

        def current():
            return (key == self._contract_context_key()[:-1]
                    and not self._auth_retry_blocked and not self._shutting_down)

        def restore():
            # Use an isolated SDK client: set_session mutates SDK memory before
            # returning. A discarded late reply must never replace the session
            # of the main client, even after an explicit account change.
            with self._session_refresh_lock:
                with self._contract_lock:
                    if not current():
                        raise ContractDispatchPaused()
                restored = self.create_supabase_client(
                    self._cloud_config, restore_session=False
                )
                if restored is None:
                    raise RuntimeError('AUTH_REQUIRED')
                restored._antigravity_auth_callback_guard = current
                try:
                    from security_manager import SecurityManager
                    with self._session_restore_lock:
                        access, refresh = SecurityManager.get_supabase_session()
                    if not (access and refresh):
                        raise RuntimeError('AUTH_REQUIRED')
                    restored._antigravity_refresh_token = refresh
                    with self._session_exchange_in_flight():
                        with self._contract_lock:
                            if not current():
                                raise ContractDispatchPaused()
                        response = restored.auth.set_session(access, refresh)
                        session = self._session_from_response(response)
                        with self._contract_lock:
                            if not current():
                                raise ContractDispatchPaused()
                            if not (getattr(session, 'access_token', '')
                                    and getattr(session, 'refresh_token', '')):
                                raise RuntimeError('AUTH_REQUIRED')
                            if not self._persist_supabase_session(
                                session, expected_previous=refresh,
                                generation=restored._antigravity_session_generation,
                            ):
                                raise RuntimeError('AUTH_REQUIRED')
                            self._remember_client_session(restored, session)
                            restored._antigravity_authenticated = True
                            restored._antigravity_restore_pending = False
                            restored._antigravity_email = getattr(
                                getattr(session, 'user', None), 'email', ''
                            ) or ''
                    return restored
                except Exception:
                    self._close_supabase_client(restored)
                    raise

        def complete(success, result):
            with self._contract_lock:
                if self._session_recovery_ticket is ticket:
                    self._session_recovery_ticket = None
                if not current():
                    if success:
                        self._close_supabase_client(result)
                    return
                if success:
                    result._antigravity_auth_callback_guard = None
                    self.supabase = result
                    self._auth_context_generation += 1
                    self._forget_contract_handshake()
                    self._begin_structure_authority_selection()
                    self._last_sync_error = ''
                    self._last_failure_offline = False
                elif self._transient_handshake_error(result):
                    self._session_recovery_count = min(self._session_recovery_count + 1, 6)
                    self._session_recovery_after = time.monotonic() + min(
                        60, 2 ** self._session_recovery_count
                    )
                else:
                    client._antigravity_restore_pending = False
                    self._mark_auth_required(result)
            self._publish_sync_state()
            if success:
                self._close_supabase_client(client)
                # Both paths remain read-first; no queued write is released
                # until the new project's structure has been validated.
                self.pull_remote_changes_async(reason='baseline')

        try:
            worker = self._start_server_action(restore, complete)
            if worker is None:
                with self._contract_lock:
                    if self._session_recovery_ticket is ticket:
                        self._session_recovery_ticket = None
                return False
            return True
        except Exception:
            with self._contract_lock:
                if self._session_recovery_ticket is ticket:
                    self._session_recovery_ticket = None
            raise
