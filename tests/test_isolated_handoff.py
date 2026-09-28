"""New offline handoff impact cases; prior test methods are never selected."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import isolated_handoff as h
from tests.test_isolated_target_bootstrap import BootstrapTests, PROTECTED, MAIN

c=h.c;b=h.b
_setup=BootstrapTests.setUp
_run=BootstrapTests.run_bootstrap


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    path=BootstrapTests.path
    handler=BootstrapTests.handler
    directory=BootstrapTests.directory

    async def asyncSetUp(self):
        _setup(self)
        result=await _run(self)
        self.assertEqual(result['status'],'candidate-prepared')
        self.files=h.snapshot(self.directory)
        self.pin=c.digest(self.files['baseline-candidate.json'])
        self.dest=self.base/'handoff'

    def connect(self):return h.connect(self.files,self.scope['run_id'],self.pin)
    def change(self,name,callback):
        value=c.parse(self.files[name]);callback(value);self.files[name]=c.encoded(value)
    def reseal(self):
        name=next(n for n in self.files if n.endswith('-candidate-prepared.json'))
        seal=c.parse(self.files[name])
        seal['files']={n:c.digest(self.files[n]) for n in seal['files']}
        seal['sha256']=c.digest(self.files['baseline-candidate.json'])
        self.files[name]=c.encoded(seal);self.pin=seal['sha256']

    async def test_source_raw_preserved_and_self_contained_inspection(self):
        index=h.export(self.directory,self.dest,candidate_sha256=self.pin)
        inspected=h.inspect(self.dest,expected_handoff_sha256=c.digest(c.encoded(index)))
        self.assertEqual(index,inspected)
        self.assertEqual(h.snapshot(self.directory),self.files)
        for name,raw in self.files.items():self.assertEqual((self.dest/'source'/name).read_bytes(),raw)
        self.assertFalse(index['schema_finalized']);self.assertTrue(index['blocked_reasons'])
        self.assertTrue(all(v is False for v in index['authority'].values()))

    async def test_target_members_references_and_context_not_editable(self):
        index,target=self.connect()
        members={x['entity_id'] for x in target['members']}
        self.assertEqual(members,{c.ROOT_CANDIDATE,b.BODY_ID,b.EMPTY_ID,b.ORDER_ID})
        refs={x['entity_id'] for x in target['references']}
        self.assertTrue({MAIN,c.PARENT,PROTECTED,b.PARENT_ORDER}<=refs)
        for section in ('members','references','context_only'):
            for row in target[section]:
                self.assertFalse(row.get('allowed_actions',[]))
                for ref in row['source_refs']:
                    source=h.resolve({n:c.parse(v) for n,v in self.files.items()},ref)
                    self.assertIn(row['entity_id'],source.values())

    async def test_missing_times_headers_not_fabricated_from_scope(self):
        index,_=self.connect()
        for number in range(1,17):
            for field in ('started_at','received_at'):
                e=index['evidence'][f'Q{number}.{field}']
                self.assertEqual((e['state'],e['value'],e['source_ref']),('unavailable',None,None))
        self.assertEqual(index['evidence']['Q14.row_count']['state'],'derived_from_raw')
        self.assertEqual(index['evidence']['Q14.content_range']['state'],'unavailable')
        self.assertEqual(index['evidence']['Q8.row_count']['state'],'not_applicable')
        self.assertFalse(index['raw_count_independently_verified'])

    async def test_request_roles_postcreate_numbers_and_operations(self):
        index,_=self.connect()
        self.assertEqual([x['request_index'] for x in index['observations'] if x['phase']=='postcreate'],list(range(12,17)))
        for item in index['creation']:
            req=h.resolve({n:c.parse(v) for n,v in self.files.items()},item['request_ref'])
            self.assertEqual(item['batch_id'],req['batch']['batch_id'])
            self.assertEqual(item['operation_ids'],[v['operation_id'] for v in req['ordered_intents']])
        self.assertTrue(all(x['independent']['state']=='unverified' for x in index['checks']))

    async def test_wrong_candidate_pin_blocks_before_output(self):
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_CANDIDATE_PIN'):
            h.export(self.directory,self.dest,candidate_sha256='0'*64)
        self.assertFalse(self.dest.exists())

    async def test_raw_byte_change_rejected(self):
        self.files['Q14.body']+=b' '
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_SOURCE_HASH'):self.connect()

    async def test_missing_raw_not_reconstructed_from_candidate(self):
        del self.files['Q14.body']
        with self.assertRaises(c.ReadStopped):self.connect()

    async def test_unfinished_terminal_rejected_even_with_candidate(self):
        name=next(n for n in self.files if n.endswith('-terminal.json'))
        self.change(name,lambda x:x.update(status='stopped'))
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_SOURCE_NOT_FINISHED'):self.connect()

    async def test_missing_parent_not_coerced_to_null(self):
        self.change('Q15.body',lambda rows:rows[0].pop('parent_folder_id'))
        self.reseal()
        with self.assertRaises(c.ReadStopped):self.connect()

    async def test_revision_bool_rejected(self):
        for name in ('Q15.body','baseline-candidate.json'):
            if name.startswith('Q'):
                self.change(name,lambda rows:next(x for x in rows if x['folder_id']==c.ROOT_CANDIDATE).update(revision=True))
            else:self.change(name,lambda x:x['candidate']['root'].update(revision=True))
        self.reseal()
        with self.assertRaises(c.ReadStopped):self.connect()

    async def test_integer_safe_range_and_no_coercion(self):
        for v in (True,1.0,'1',None,2**53,-1):
            with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_INTEGER'):h.integer(v)
        self.assertEqual(h.integer(2**53-1),2**53-1)

    async def test_authority_forgery_blocks_resealed_source(self):
        self.change('baseline-candidate.json',lambda x:x.update(execution_allowed=True));self.reseal()
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_AUTHORITY'):self.connect()

    async def test_candidate_body_mismatch_not_silently_repaired(self):
        self.change('baseline-candidate.json',lambda x:x['candidate']['documents'][0].update(content='different'))
        self.reseal()
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_CANDIDATE_ROWS'):self.connect()

    async def test_request_reservation_role_swap_rejected(self):
        name=next(n for n in self.files if n.endswith('-reserved.json') and c.parse(self.files[n])['request']==12)
        self.change(name,lambda x:x.update(path='/rest/v1/documents'));self.reseal()
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_REQUEST_BINDING'):self.connect()

    async def test_duplicate_response_event_rejected(self):
        name=next(n for n in self.files if n.endswith('-response.json') and c.parse(self.files[n])['request']==16)
        self.change(name,lambda x:x.update(request=15));self.reseal()
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_DUPLICATE_EVENT'):self.connect()

    async def test_raw_hash_and_decoded_body_hash_remain_separate(self):
        index,target=self.connect()
        doc=next(x for x in target['members'] if x['entity_id']==b.BODY_ID)
        self.assertEqual(doc['body']['sha256'],c.digest(self.body))
        artifact=next(x for x in index['artifacts'] if x['artifact_id']=='Q14.body')
        self.assertNotEqual(artifact['sha256'],doc['body']['sha256'])
        empty=next(x for x in target['members'] if x['entity_id']==b.EMPTY_ID)
        self.assertEqual((empty['body']['utf8_bytes'],empty['body']['ends_lf']),(0,False))

    async def test_paths_and_pointer_escaping(self):
        for name in ('../x','/x','C:/x','a\\b','a//b','a/./b','a/../b'):
            with self.assertRaises(c.ReadStopped):h.safe_relative(name)
        self.assertEqual(h.resolve({'raw':{'a/b':{'~':['ok']}}},h.reference('raw','/a~1b/~0/0')),'ok')
        for pointer in ('/a~2b','a','/a~1b/~0/00','/a~1b/~0/9'):
            with self.assertRaises(c.ReadStopped):h.resolve({'raw':{'a/b':{'~':['ok']}}},h.reference('raw',pointer))

    async def test_existing_output_never_overwritten(self):
        self.dest.mkdir();(self.dest/'keep').write_bytes(b'original')
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_OUTPUT_EXISTS'):
            h.export(self.directory,self.dest,candidate_sha256=self.pin)
        self.assertEqual((self.dest/'keep').read_bytes(),b'original')

    async def test_source_output_overlap_rejected(self):
        for dest in (self.directory,self.directory/'nested',self.directory.parent):
            with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_OUTPUT_OVERLAP'):
                h.export(self.directory,dest,candidate_sha256=self.pin)

    async def test_partial_output_never_marked_complete(self):
        original=c.new_file
        def fail(path,raw):
            if path.name=='target.json':raise OSError('synthetic storage failure')
            return original(path,raw)
        with patch.object(c,'new_file',fail),self.assertRaises(OSError):
            h.export(self.directory,self.dest,candidate_sha256=self.pin)
        self.assertTrue(self.dest.exists());self.assertFalse((self.dest/'completed.json').exists())
        self.assertEqual(h.snapshot(self.directory),self.files)

    async def test_target_and_index_mutations_rejected_on_inspection(self):
        index=h.export(self.directory,self.dest,candidate_sha256=self.pin)
        (self.dest/'target.json').write_bytes(b'{}')
        with self.assertRaisesRegex(c.ReadStopped,'HANDOFF_LINKS_CHANGED'):
            h.inspect(self.dest,expected_handoff_sha256=c.digest(c.encoded(index)))

    async def test_apply_block_cannot_be_lifted_by_supplied_flags(self):
        with self.assertRaisesRegex(c.ReadStopped,'REAL_CONTRACT_UNRESOLVED'):
            h.require_apply_input(dict(schema_finalized=True,execution_allowed=True,blocked_reasons=[]))


del BootstrapTests
