"""8012 -> 8013 and fresh observation acceptance on synthetic stores only."""
import copy
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from tests import test_general_test_gate as fixtures
from tests.test_sync_contract_stage8 import access_token_with_subject
from sync_contract import SyncContractError, read_handshake_compatibility
from sync_v2_store import SyncV2Store
import general_test_gate as gate
import server_checkpoint as checkpoint


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.GeneralTestGateTests()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.manager, self.store, self.client = self.case.manager, self.case.store, self.case.client
        self.key = self.manager._v2_context["local_key"]

    def state(self):
        r = self.store.get_project(self.key)
        return r["project_sync_mode"], r["migration_epoch"], r["contract_path_enabled"]

    def accept(self):
        ticket = gate.begin(self.manager)
        gate.handshake(self.manager, ticket)
        return ticket

    def completed(self):
        operation = self.store.enqueue(self.manager._v2_context, "__antigravity__/tree-order.json", "synthetic control")
        self.store.mark_attempt(operation["operation_id"])
        self.store.mark_success(operation["operation_id"], {"revision":1})
        return operation["operation_id"]

    def tables(self):
        with self.store._reader() as connection:
            names=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                   if r[0] not in {"sync_server_checkpoint_observations", "sync_projects", "sqlite_sequence"}]
            return {name: [tuple(r) for r in connection.execute('SELECT * FROM "'+name+'"')] for name in names}

    def test_success_preserves_completed_request_events_other_project_and_gates(self):
        operation_id = self.completed()
        before = self.tables()
        other = self.store.get_project(self.case.case.fixture.context["local_key"])
        self.accept()
        self.assertEqual(self.state(), ("ID_BASED",1,0))
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.store.get_project(other["local_key"]), other)
        self.assertEqual(self.store.operation(operation_id)["status"], "completed")
        self.assertTrue(all(name=="get_sync_handshake" for name,_ in self.client.calls))
        with self.store._reader() as c:
            rows=c.execute("SELECT source_json FROM sync_server_checkpoint_observations").fetchall()
            self.assertEqual(len(rows), 1)
            source=json.loads(rows[0][0]);self.assertEqual(source["kind"],"fresh_get_sync_handshake")
            self.assertNotIn(str(self.case.root),str(source));self.assertNotIn("header.",str(source))

    def test_same_checkpoint_reobservation_is_idempotent(self):
        self.accept();before=self.tables();self.accept()
        self.assertEqual(self.tables(),before)
        with self.store._reader() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM sync_server_checkpoint_observations").fetchone()[0],1)
        self.assertEqual(self.state(),("ID_BASED",1,0))
        self.assertEqual(len(self.client.calls),2)

    def test_existing_general_api_and_plain_sql_still_refuse_direct_acceptance(self):
        compat=read_handshake_compatibility(self.client.reply)
        with self.assertRaises(SyncContractError):
            self.manager.activate_contract_project(**compat)
        with self.assertRaises(sqlite3.DatabaseError), self.store._transaction() as c:
            c.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(self.key,))
        # A raw connection has no connection-scoped permission either.
        with sqlite3.connect(self.store.db_path) as c:
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(self.key,))
        self.assertEqual(self.state(),("LEGACY",0,0))
        with self.assertRaises(sqlite3.DatabaseError), self.store._transaction() as c:
            c.execute("INSERT INTO sync_checkpoint_permits VALUES (?, ?, ?, ?, ?)",
                      (self.key,self.case.project_id,self.case.name,3,checkpoint.CANONICAL_CONTRACT_SHA256))

    def test_bad_responses_do_not_change_checkpoint(self):
        valid=copy.deepcopy(self.client.reply)
        cases=[{"supported":False}, {"project_id":str(uuid.uuid4())},
               {"project_sync_mode":"ID_BASED","migration_epoch":2},
               {"project_sync_mode":"LEGACY","migration_epoch":0},
               {"canonical_contract_sha256":"0"*64}, {"server_protocol_version":2},
               {"server_capabilities":[]}, {"contract_version":"0.1.0"}]
        for change in cases:
            with self.subTest(change=change):
                self.client.reply={**valid,**change}
                with self.assertRaises((SyncContractError,gate.PreparationError)):
                    self.accept()
                self.assertEqual(self.state(),("LEGACY",0,0))

    def test_authentication_failure_does_not_write(self):
        self.client._antigravity_access_token="invalid"
        with self.assertRaises(gate.PreparationError):self.accept()
        self.assertEqual(self.state(),("LEGACY",0,0));self.assertEqual(self.client.calls,[])

    def test_inflight_binding_account_endpoint_or_generation_change_refused(self):
        changes=[(self.client,"_antigravity_access_token",access_token_with_subject(str(uuid.uuid4()))),
                 (self.client,"supabase_url","https://different.invalid"),
                 (self.manager,"_v2_context_generation",self.manager._v2_context_generation+1)]
        for target,name,value in changes:
            old=getattr(target,name);original=self.client._answer
            def changed():
                reply=original();setattr(target,name,value);return reply
            try:
                with self.subTest(name=name),patch.object(self.client,"_answer",side_effect=changed):
                    with self.assertRaises((gate.PreparationError,SyncContractError)):self.accept()
                    self.assertEqual(self.state(),("LEGACY",0,0))
            finally:setattr(target,name,old)

    def test_open_gate_or_missing_hold_refused(self):
        ticket=gate.begin(self.manager)
        self.store.set_contract_path_enabled(self.key,True)
        with self.assertRaises(gate.PreparationError):gate.handshake(self.manager,ticket)
        self.assertEqual(self.state(),("LEGACY",0,1))
        self.store.set_contract_path_enabled(self.key,False)
        gate._hold_path(self.store).unlink()  # synthetic fault injection only
        with self.assertRaises(gate.PreparationError):gate.handshake(self.manager,ticket)
        self.assertEqual(self.state(),("LEGACY",0,0))

    def test_unfinished_states_refused_and_completed_history_preserved(self):
        operation=self.store.enqueue(self.manager._v2_context,"synthetic.txt","synthetic")
        for action in (lambda:None,lambda:self.store.mark_attempt(operation["operation_id"]),
                       lambda:self.store.mark_retry(operation["operation_id"],"synthetic"),
                       lambda:self.store.mark_blocked(operation["operation_id"],"synthetic")):
            action()
            with self.assertRaises(gate.PreparationError):self.accept()
            self.assertEqual(self.state(),("LEGACY",0,0))

    def test_transaction_entry_race_detected(self):
        ticket=gate.begin(self.manager);original=checkpoint.accept
        def raced(*args,**kwargs):
            self.store.set_contract_path_enabled(self.key,True)
            return original(*args,**kwargs)
        with patch("server_checkpoint.accept",side_effect=raced):
            with self.assertRaises(gate.PreparationError):gate.handshake(self.manager,ticket)
        self.assertEqual(self.state(),("LEGACY",0,1))

    def test_transaction_queue_recheck_sees_uncommitted_pending_work(self):
        self.completed();before=self.tables();ticket=gate.begin(self.manager)
        original=checkpoint._empty_queue
        def pending(store,connection,key):
            store.enqueue(self.manager._v2_context,"synthetic-pending.txt","synthetic")
            return original(store,connection,key)
        with patch("server_checkpoint._empty_queue",side_effect=pending):
            with self.assertRaises(SyncContractError):gate.handshake(self.manager,ticket)
        self.assertEqual(self.state(),("LEGACY",0,0));self.assertEqual(before,self.tables())

    def test_conflict_refuses_acceptance_without_altering_its_record(self):
        op=self.store.enqueue(self.manager._v2_context,"synthetic-conflict.txt","synthetic")
        self.store.mark_attempt(op["operation_id"])
        self.store.mark_conflict(op["operation_id"],1,"synthetic-conflict.txt","remote","merged")
        before=self.tables()
        with self.assertRaises(gate.PreparationError):self.accept()
        self.assertEqual(self.tables(),before);self.assertEqual(self.state(),("LEGACY",0,0))

    def test_inflight_auth_failure_has_no_checkpoint_write(self):
        self.client.reply=SyncContractError("AUTH_REQUIRED")
        with self.assertRaises(SyncContractError):self.accept()
        self.assertEqual(self.state(),("LEGACY",0,0))
        self.assertTrue(gate.writes_held(self.manager))

    def test_changed_local_writing_binding_stops_before_network(self):
        with patch.object(self.manager._v2_wpm,"writing_root_path",str(self.case.workspace)):
            with self.assertRaises(gate.PreparationError):self.accept()
        self.assertEqual(self.client.calls,[]);self.assertEqual(self.state(),("LEGACY",0,0))

    def test_record_insert_failure_rolls_back_checkpoint_and_authority(self):
        ticket=gate.begin(self.manager);before=self.tables()
        with self.store._transaction() as c:
            c.execute("""CREATE TRIGGER synthetic_insert_failure BEFORE INSERT ON sync_server_checkpoint_observations
                       BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):gate.handshake(self.manager,ticket)
        self.assertEqual(self.state(),("LEGACY",0,0));self.assertEqual(before,self.tables())
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertTrue(gate.writes_held(self.manager))
        with self.store._transaction() as c:
            c.execute("DROP TRIGGER synthetic_insert_failure")
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(self.key,))
        self.accept();self.assertEqual(self.state(),("ID_BASED",1,0))
        with self.store._reader() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM sync_checkpoint_permits").fetchone()[0],0)

    def to_8012(self):
        # Reconstruct just the prior schema boundary using the exact old
        # trigger fixture. No production database is opened or copied.
        self.completed()
        with self.store._transaction() as c:
            c.execute("DROP TABLE sync_server_checkpoint_observations")
            c.execute("DROP TRIGGER sync_projects_mode_epoch_transition")
            c.execute((Path(__file__).parent/'fixtures/sync8012-mode-transition.sql').read_text(encoding='utf-8'))
            c.execute("PRAGMA user_version=8012")

    def test_8012_upgrade_preserves_existing_rows_and_reopen_is_idempotent(self):
        self.to_8012();before=self.tables()
        with self.store._reader() as c:
            projects=[tuple(r) for r in c.execute("SELECT * FROM sync_projects")]
        for _ in range(2):
            SyncV2Store(self.store.db_path)
            self.assertEqual(before,self.tables())
            with self.store._reader() as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],8013)
                self.assertEqual(projects,[tuple(r) for r in c.execute("SELECT * FROM sync_projects")])

    def test_upgrade_failure_restores_old_trigger_schema_and_version(self):
        self.to_8012();before=self.tables()
        with self.store._reader() as c:
            old=c.execute("SELECT sql FROM sqlite_master WHERE name='sync_projects_mode_epoch_transition'").fetchone()[0]
        with patch("server_checkpoint.TRIGGER","INVALID SYNTHETIC SQL"):
            with self.assertRaises(sqlite3.DatabaseError):SyncV2Store(self.store.db_path)
        with self.store._reader() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],8012)
            self.assertEqual(c.execute("SELECT sql FROM sqlite_master WHERE name='sync_projects_mode_epoch_transition'").fetchone()[0],old)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='sync_server_checkpoint_observations'").fetchone())
        self.assertEqual(before,self.tables())
        SyncV2Store(self.store.db_path)
        self.assertEqual(before,self.tables())

    def test_regression_and_higher_epoch_remain_refused_after_acceptance(self):
        self.accept()
        for mode,epoch in (("LEGACY",0),("ID_BASED",2),("MIGRATING",2)):
            with self.subTest(mode=mode),self.assertRaises(sqlite3.DatabaseError),self.store._transaction() as c:
                c.execute("UPDATE sync_projects SET project_sync_mode=?,migration_epoch=? WHERE local_key=?",(mode,epoch,self.key))
            self.assertEqual(self.state(),("ID_BASED",1,0))
