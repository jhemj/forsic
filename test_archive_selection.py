"""Offline selection/admission + real synthetic TAR; image/Docker boundary is stubbed."""
from copy import deepcopy
import io
import json
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_efficiency_contracts as efficiency
from test_archive_reader import Node
from forsic_plugin.archive_selection import entry_schema
from forsic_plugin.image_reader import inspect_archive
from forsic_plugin.report_driven.host import source_record


class ArchiveSelectionTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            blocker = patch(target, side_effect=AssertionError('offline archive test forbids network'))
            blocker.start(); self.addCleanup(blocker.stop)
        self.x = efficiency.EfficiencyContracts(); self.x.setUp(); self.addCleanup(self.x.doCleanups)
        self.context = {'status': 'ready', 'goal_id': 'synthetic-goal', 'goal_status': 'active',
            'requested_session_id': 'synthetic-session', 'conversation_id': 'synthetic-session',
            'case_id': self.x.case.config['case_id']}
        binding = patch('forsic_plugin.investigation_context.native_goal_binding', return_value=self.context)
        binding.start(); self.addCleanup(binding.stop)
        # Registration captures the native binding function in each handler.
        from forsic_plugin import register
        ctx = Mock(); ctx.get_config.side_effect = lambda key: str(Path(self.x.temp.name)/'intake') if key == 'intake_root' else None
        register(ctx)
        self.x.handlers = {c.kwargs['name']: c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        self.x.schemas = {c.kwargs['name']: c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        (self.x.case.root/'synthetic.e01').write_bytes(b'synthetic stub, not E01')
        self.archive = Path(self.x.temp.name)/'fixture.tar'
        self.make([('same', b'FIRST', tarfile.REGTYPE), ('same', b'ABCDEFGHIJ', tarfile.REGTYPE)])
        self.node = Node(self.archive)
        self.origin = {'segments': [{'name': 'synthetic.e01', 'metadata': {'st_size': 22}}], 'volume_offset': 8192}
        self.list_args = {'path': 'synthetic.e01', 'action': 'archive_list', 'file_path': '/capture.tar'}
        self.backend = patch.object(self.x.case, 'image_files', side_effect=self.parse)
        self.reader = self.backend.start(); self.addCleanup(self.backend.stop)

    def make(self, rows, mode='w'):
        with tarfile.open(self.archive, mode, format=tarfile.USTAR_FORMAT) as archive:
            for name, body, kind in rows:
                info = tarfile.TarInfo(name); info.type = kind; info.mtime = 1
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = 'same'
                if kind == tarfile.REGTYPE:
                    info.size = len(body)
                archive.addfile(info, io.BytesIO(body))

    def parse(self, args, *, archive_selection=None):
        result = inspect_archive(self.node, args, archive_selection)
        return {**result, 'archive_origin': deepcopy(self.origin), 'volume_offset': self.origin['volume_offset'],
                'path': args['path'], 'file_path': args['file_path']}

    def listing(self, **changes):
        result = self.x.call('forsic_image_files', {**self.list_args, **changes})
        self.assertNotIn('error', result, result)
        self.assertEqual(result['read_tool'], 'forsic_archive_read')
        self.assertEqual(result['source_ref'], {k: source_record(self.x.case.event(result['evidence_id']))[k] for k in ('id', 'version')})
        return result

    def read(self, listing, index=1, **extra):
        return self.x.call('forsic_archive_read', {'listing_ref': listing['source_ref'], 'member_index': index, **extra})

    def rejected(self, args):
        count = self.reader.call_count
        result = self.x.call('forsic_archive_read', args)
        self.assertIn('error', result, result)
        self.assertTrue(result['execution_rejected'], result)
        self.assertFalse(result['backend_started'])
        self.assertEqual(count, self.reader.call_count)
        self.assertNotIn('outcome', result)
        self.assertEqual(self.x.case.event(result['started_id'])['data']['submitted_arguments'], args)
        return result

    def cloned_listing(self, listing, *, start_change=None, result_change=None, session=None, orphan=False):
        old = self.x.case.event(listing['evidence_id'])
        start = deepcopy(self.x.case.event(old['data']['started_id'])['data'])
        if start_change: start_change(start)
        owner = session or old['session']
        sid = self.x.case.record('tool_start', start, owner)
        data = {**deepcopy(old['data']), 'started_id': 'unknown-start' if orphan else sid}
        if result_change: result_change(data)
        rid = self.x.case.record('tool_result', data, owner)
        source = source_record(self.x.case.event(rid))
        return {'source_ref': {key: source[key] for key in ('id', 'version')}}

    def test_selection_continuation_exact_body_c2_and_canonical_ledger(self):
        listing = self.listing()
        self.assertNotIn('presented_spans', listing)  # Headers are not body citations.
        original = deepcopy(listing)
        first = self.read(listing, length=4)
        self.assertEqual(first['text'], 'ABCD'); self.assertEqual(first['byte_end'], 4)
        self.assertEqual(first['tool'], 'forsic_image_files')
        self.assertFalse(first['complete_member'])
        self.assertEqual(listing, original)
        call = first['next_call']
        self.assertEqual(call['arguments']['listing_ref'], listing['source_ref'])
        self.assertEqual(call['arguments']['offset'], 4)
        second = self.x.call(call['tool'], {**call['arguments'], 'length': 6})
        self.assertEqual(second['text'], 'EFGHIJ'); self.assertIsNone(second['next_call'])
        self.assertFalse(second['complete_member']); self.assertFalse(second['coverage_complete'])
        start = self.x.case.event(first['started_id'])
        self.assertEqual(start['session'], 'synthetic-session')
        self.assertEqual(start['data']['submitted_tool'], 'forsic_archive_read')
        effective = start['data']['arguments']
        self.assertEqual(effective['action'], 'archive_read')
        self.assertEqual(effective['file_path'], '/capture.tar')
        self.assertEqual(effective['volume_offset'], 8192)
        self.assertNotIn('listing_ref', effective)
        self.assertNotIn('_archive_selection', effective)
        self.assertNotIn('mission_context', first)
        self.assertEqual(start['data']['argument_resolution']['listing_ref'], listing['source_ref'])
        from forsic_plugin.report_driven.grounded_judgment import selection
        choice = next(c for c in first['presented_spans'] if c['pointer'] == '/text')
        citation = selection(self.x.case, {}, choice['citation_ref'])
        self.assertEqual(citation['literal'], 'ABCD')
        self.assertEqual(citation['source_id'], first['evidence_id'])
        retained = self.x.case.event(first['evidence_id'])['data']
        self.assertIn('base64', retained); self.assertNotIn('next_call', retained)
        coverage = json.loads(source_record(self.x.case.event(first['evidence_id']))['coverage'])
        self.assertEqual(coverage['identity_strength'], 'metadata_checked')
        self.assertFalse(coverage['complete_member'])
        self.assertEqual(self.read(listing, index=0)['text'], 'FIRST')

    def test_strict_contract_no_coercion_or_overrides(self):
        listing = self.listing()
        good = {'listing_ref': listing['source_ref'], 'member_index': 1}
        invalid = [{}, {'member_index': 1}, {'listing_ref': listing['source_ref']}]
        invalid += [{**good, 'listing_ref': v} for v in (None, '', [], {}, {'id': 'x'}, {'id': '', 'version': 'v'},
                    {'id': 'x', 'version': ''}, {'id': 1, 'version': 'x'}, {**listing['source_ref'], 'path': '/x'})]
        invalid += [{**good, 'member_index': v} for v in (True, False, -1, '1', 1.0, None)]
        invalid += [{**good, 'offset': v} for v in (True, -1, '1', 1.0, None)]
        invalid += [{**good, 'length': v} for v in (True, 0, -1, 12001, '4', 4.0, None)]
        invalid += [{**good, key: 'override'} for key in ('path', 'file_path', 'volume_offset', 'action', 'member_name',
                    'mission_id', 'mission_version', 'operation_id', '_archive_selection', 'reason')]
        for args in invalid:
            with self.subTest(args=args):
                result = self.rejected(args)
                self.assertEqual(result['error_code'], 'invalid_arguments')
        self.rejected({**good, 'offset': 11})

    def test_only_listed_global_ordinals_on_partial_pages(self):
        self.make([(str(i), b'x', tarfile.REGTYPE) for i in range(102)])
        first = self.listing(); second = self.listing(offset=100)
        self.assertFalse(first['coverage_complete']); self.assertFalse(second['coverage_complete'])
        self.assertEqual(self.read(second, index=100)['text'], 'x')
        for listing, index in ((first, 100), (second, 0), (second, 102)):
            self.rejected({'listing_ref': listing['source_ref'], 'member_index': index})

    def test_budget_partial_list_keeps_verified_entries_not_unseen_members(self):
        self.make([('small', b'xxxx', tarfile.REGTYPE), ('big', b'x'*100000, tarfile.REGTYPE), ('tail', b'y', tarfile.REGTYPE)])
        listing = self.listing(archive_scan_bytes=20480)
        self.assertEqual(listing['stop_reason'], 'scan_budget')
        self.assertEqual(self.read(listing, index=0, length=4)['text'], 'xxxx')
        self.rejected({'listing_ref': listing['source_ref'], 'member_index': 2})
        stopped = self.read(listing, index=1, offset=90000, length=4)
        self.assertEqual(stopped['stop_reason'], 'scan_budget')
        self.assertNotIn('text', stopped); self.assertIsNone(stopped['next_call'])

    def test_refs_versions_start_pairs_and_failed_pending_records(self):
        listing = self.listing()
        for change in ({'id': 'nonexistent'}, {'version': 'stale'}):
            self.rejected({'listing_ref': {**listing['source_ref'], **change}, 'member_index': 0})
        variants = [self.cloned_listing(listing, orphan=True),
            self.cloned_listing(listing, start_change=lambda d: d.update(execution_rejected=True)),
            self.cloned_listing(listing, start_change=lambda d: d['arguments'].update(action='read')),
            self.cloned_listing(listing, result_change=lambda d: d.update(error='failed')),
            self.cloned_listing(listing, result_change=lambda d: d.update(status='pending')),
            self.cloned_listing(listing, result_change=lambda d: d.update(status='cancelled'))]
        for bad in variants:
            self.rejected({'listing_ref': bad['source_ref'], 'member_index': 0})
        # A reference to a non-listing body, and a start/result with two committed returns.
        body = self.read(listing)
        self.rejected({'listing_ref': body['source_ref'], 'member_index': 0})
        old = self.x.case.event(listing['evidence_id'])
        self.x.case.record('tool_result', old['data'], old['session'])
        self.rejected({'listing_ref': listing['source_ref'], 'member_index': 0})

    def test_legacy_listing_requests_explicit_relist_only(self):
        listing = self.listing()
        legacy = self.cloned_listing(listing, result_change=lambda d: d.pop('archive_identity'))
        result = self.rejected({'listing_ref': legacy['source_ref'], 'member_index': 0})
        self.assertEqual(result['error_code'], 'archive_identity_unavailable')
        self.assertEqual(result['relist_call']['arguments']['file_path'], '/capture.tar')
        self.assertEqual(result['source_call']['arguments']['source_id'], legacy['source_ref']['id'])

    def test_current_binding_pause_scope_and_other_conversation_provenance(self):
        listing = self.listing()
        args = {'listing_ref': listing['source_ref'], 'member_index': 0}
        original = deepcopy(self.context)
        for change in ({'status': 'unavailable'}, {'case_id': 'other-case'}, {'requested_session_id': 'other-session'},
                       {'goal_status': 'paused'}, {'goal_status': 'cancelled'}, {'goal_status': 'unknown'},
                       {'goal_status': None}):
            self.context.update(change); self.rejected(args); self.context.clear(); self.context.update(original)
        previous = self.cloned_listing(listing, session='previous-authorized-conversation')
        out = self.read(previous, index=0)
        self.assertEqual(out['text'], 'FIRST')
        self.assertEqual(out['argument_resolution']['listing_session'], 'previous-authorized-conversation')
        self.assertEqual(self.x.case.event(out['started_id'])['session'], 'synthetic-session')
        with patch.dict(self.x.case.config, selected_files=['a-different-file']): self.rejected(args)
        self.context.update(goal_id='', goal_status=None)  # Bound interactive reads need no autonomous goal.
        self.assertEqual(self.read(listing, index=0)['text'], 'FIRST')

    def test_changed_archive_metadata_and_truncation_reject_before_body(self):
        listing = self.listing()
        self.make([('same', b'CHANGED', tarfile.REGTYPE)])
        out = self.read(listing, index=0)
        self.assertIn('metadata changed', out['error']); self.assertNotIn('text', out)
        self.assertEqual(out['source_call']['arguments']['source_id'], listing['source_ref']['id'])
        self.assertEqual(out['relist_call']['arguments'], {**self.list_args, 'volume_offset': 8192})
        listing = self.listing()
        self.archive.write_bytes(self.archive.read_bytes()[:520])
        out = self.read(listing, index=0)
        self.assertIn('error', out); self.assertNotIn('text', out)

    def test_frozen_metadata_reordered_header_rejects_but_same_header_body_limit_is_explicit(self):
        frozen = self.node.lstat()
        with patch.object(self.node, 'lstat', return_value=frozen):
            listing = self.listing()
            self.make([('different', b'FIRST', tarfile.REGTYPE), ('same', b'ABCDEFGHIJ', tarfile.REGTYPE)])
            failed = self.read(listing, index=0)
            self.assertIn('ordinal header changed', failed['error']); self.assertNotIn('text', failed)
            self.make([('same', b'OTHER', tarfile.REGTYPE), ('same', b'ABCDEFGHIJ', tarfile.REGTYPE)])
            observed = self.read(listing, index=0)
            self.assertEqual(observed['text'], 'OTHER')
            self.assertEqual(observed['identity_strength'], 'metadata_checked')
            self.assertIn('may go undetected', observed['identity_limitation'])

    def test_special_members_not_followed_and_names_never_extracted(self):
        self.make([('../../outside', b'synthetic', tarfile.REGTYPE), ('/absolute', b'', tarfile.REGTYPE),
                   ('sym', b'', tarfile.SYMTYPE), ('hard', b'', tarfile.LNKTYPE),
                   ('dir', b'', tarfile.DIRTYPE), ('device', b'', tarfile.CHRTYPE), ('fifo', b'', tarfile.FIFOTYPE)])
        before = set(self.archive.parent.iterdir())
        listing = self.listing()
        self.assertEqual(self.read(listing, index=0)['text'], 'synthetic')
        for index in range(2, 7):
            self.assertFalse(listing['entries'][index]['readable'])
            self.rejected({'listing_ref': listing['source_ref'], 'member_index': index})
        self.assertEqual(before, set(self.archive.parent.iterdir()))

    def test_end_offset_is_valid_but_not_complete_member(self):
        out = self.read(self.listing(), offset=10)
        self.assertEqual(out['text'], ''); self.assertEqual(out['byte_start'], out['byte_end'])
        self.assertFalse(out['complete_member']); self.assertIsNone(out['next_call'])

    def test_model_surface_sanitizer_and_strict_admission_acceptance(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.investigation_state import input_problems
        schemas = sanitize_tool_schemas([{'type': 'function', 'function': deepcopy(v)} for v in self.x.schemas.values()])
        from hermes_state import SessionDB
        db = SessionDB(db_path=Path(self.x.temp.name)/'native.db')
        try:
            db.create_session('synthetic-pin', 'cli')
            db.update_session_tool_names('synthetic-pin', {'tools': schemas})
            stored = json.loads(db.get_session('synthetic-pin')['tool_names'])
            pin = {v['function']['name']: v['function']['parameters'] for v in stored['tools']}
        finally:
            db.close()
        self.assertEqual(pin['forsic_archive_read'], entry_schema())
        public = pin['forsic_image_files']
        self.assertNotIn('member_index', public['properties'])
        for hidden in ('read', 'read_bytes', 'archive_read'):
            self.assertNotIn(hidden, public['properties']['action']['enum'])
            self.assertNotIn(hidden+'=', public['properties']['action']['description'])
        self.assertNotIn('archive_read', public['properties']['file_path']['description'])
        physical = next(b for b in pin['forsic_mission']['properties']['tool_arguments']['anyOf'] if b['title'] == 'forsic_image_files')
        self.assertIn('archive_read', physical['properties']['action']['enum'])
        good = {'listing_ref': self.listing()['source_ref'], 'member_index': 0}
        for args in (good, {**good, 'offset': '0'}, {**good, 'member_index': True}, {**good, 'file_path': '/override'}):
            errors = input_problems(args, pin['forsic_archive_read'], 'arguments')
            out = self.x.call('forsic_archive_read', args)
            self.assertEqual(bool(errors), out.get('error_code') == 'invalid_arguments')

    def test_tar_budget_deadline_sparse_and_post_read_change(self):
        listing = self.listing()
        selection = {'archive_identity': listing['archive_identity'], 'member': listing['entries'][0]}
        args = {'action': 'archive_read', 'member_index': 0}
        before = self.node.lstat()
        altered = SimpleNamespace(**{k: getattr(before, k) for k in ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mtime', 'st_ctime')})
        altered.st_size += 1
        with patch.object(self.node, 'lstat', side_effect=[before, altered]):
            with self.assertRaisesRegex(ValueError, 'changed during inspection'):
                inspect_archive(self.node, args, selection)
        with patch('forsic_plugin.image_reader.time.monotonic', side_effect=[0, 61]):
            stopped = inspect_archive(self.node, args, selection)
        self.assertEqual(stopped['stop_reason'], 'scan_budget'); self.assertNotIn('text', stopped)
        # GNU sparse extension parsed by the real stdlib TAR parser.
        with tarfile.open(self.archive, 'w', format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo('sparse'); info.size = 4
            info.pax_headers = {'GNU.sparse.map': '0,4', 'GNU.sparse.size': '16'}
            archive.addfile(info, io.BytesIO(b'data'))
        sparse = self.listing()
        self.assertTrue(sparse['entries'][0]['sparse']); self.assertFalse(sparse['entries'][0]['readable'])
        self.rejected({'listing_ref': sparse['source_ref'], 'member_index': 0})
        with self.assertRaisesRegex(ValueError, 'non-sparse'):
            inspect_archive(self.node, args)

    def test_private_descriptor_cannot_be_submitted_through_generic_image_tool(self):
        # Exercise Case.image_files' real Docker command construction with a process stub.
        from forsic_plugin.evidence import Case
        args = {**self.list_args, '_archive_selection': {'forged': True}}
        with patch('forsic_plugin.evidence.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'{}')) as run:
            Case.image_files(self.x.case, args)
        self.assertNotIn('_archive_selection', json.loads(run.call_args.args[0][-1]))


class ImageOriginTests(unittest.TestCase):
    """Real inspect/inspect_archive path; EWF decoding and Dissect filesystem are stubs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root/'synthetic.e01'
        self.segment(self.image, 1)
        self.archive = self.root/'archive.tar'
        with tarfile.open(self.archive, 'w') as archive:
            info = tarfile.TarInfo('synthetic.txt'); info.size = 4
            archive.addfile(info, io.BytesIO(b'body'))
        self.node = Node(self.archive)
        self.volumes = [SimpleNamespace(offset=4096), SimpleNamespace(offset=8192)]
        target = SimpleNamespace(disks=SimpleNamespace(add=lambda disk: None, apply=lambda: None), volumes=self.volumes)
        modules = {'dissect.target': SimpleNamespace(Target=lambda path: target),
                   'dissect.target.containers.ewf': SimpleNamespace(EwfContainer=lambda handles: SimpleNamespace(close=lambda: None)),
                   'dissect.target.filesystem': SimpleNamespace(open=lambda volume: SimpleNamespace(get=lambda name: self.node))}
        stub = patch.dict(sys.modules, modules); stub.start(); self.addCleanup(stub.stop)
        self.args = {'path': self.image.name, 'action': 'archive_list', 'file_path': '/archive.tar', 'volume_offset': 8192}

    @staticmethod
    def segment(path, number):
        path.write_bytes(b'EVF\x09\x0d\x0a\xff\x00'+b'\x00'+number.to_bytes(2, 'little')+b'\x00\x00')

    def read_args(self):
        from forsic_plugin.image_reader import inspect
        listed = inspect(str(self.image), self.args)
        selected = {key: listed[key] for key in ('archive_origin', 'archive_identity')}
        selected['member'] = listed['entries'][0]
        return {**self.args, 'action': 'archive_read', 'member_index': 0, '_archive_selection': selected}

    def test_selected_volume_segment_set_metadata_and_after_read_check(self):
        from forsic_plugin.image_reader import inspect
        args = self.read_args()
        self.assertEqual(inspect(str(self.image), args)['text'], 'body')
        with self.assertRaisesRegex(ValueError, 'selected volume changed'):
            inspect(str(self.image), {**args, 'volume_offset': 4096})
        second = self.root/'synthetic.e02'; self.segment(second, 2)
        with self.assertRaisesRegex(ValueError, 'segments or selected volume changed'):
            inspect(str(self.image), args)
        args = self.read_args()
        second.write_bytes(second.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError, 'segments or selected volume changed'):
            inspect(str(self.image), args)
        args = self.read_args()
        def changing(node, effective, selection):
            out = inspect_archive(node, effective, selection)
            second.unlink()
            return out
        with patch('forsic_plugin.image_reader.inspect_archive', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'segments changed during'):
                inspect(str(self.image), args)

    def test_ambiguous_volume_and_segment_symlink_do_not_read(self):
        from forsic_plugin.image_reader import inspect
        with patch('forsic_plugin.image_reader.inspect_archive') as body:
            with self.assertRaisesRegex(ValueError, 'Select volume_offset'):
                inspect(str(self.image), {k: v for k, v in self.args.items() if k != 'volume_offset'})
            body.assert_not_called()
        args = self.read_args()
        self.image.rename(self.root/'saved.bin'); self.image.symlink_to(self.root/'saved.bin')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            inspect(str(self.image), args)


class ArchiveLoopTests(unittest.TestCase):
    def setUp(self):
        from tools.registry import registry
        from forsic_plugin.loop_comparison import project
        self.names = ('synthetic_archive_selection', 'synthetic_legacy_archive')
        for name, tool in zip(self.names, ('forsic_archive_read', 'forsic_image_files')):
            registry.register(name, 'synthetic', {'name': name, 'parameters': {}}, lambda **kw: '',
                              loop_comparison=lambda args, result, tool=tool: project(tool, args, result))
            self.addCleanup(registry.deregister, name)

    def test_receipt_churn_defaults_target_version_range_and_body_comparison(self):
        from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig, _result_hash, ToolCallSignature
        name = self.names[0]
        args = {'listing_ref': {'id': 'listing', 'version': 'v1'}, 'member_index': 0}
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True, no_progress_block_after=3))
        for i in range(3):
            body = {'tool': 'forsic_image_files', 'evidence_id': str(i), 'started_id': str(i), 'text': 'same body',
                    'source_ref': {'id': str(i), 'version': str(i)}, 'omitted_fields': ['base64'],
                    'raw_bytes_call': {'receipt': str(i)}, 'presented_spans': [{'citation_ref': str(i)}]}
            guard.after_call(name, args, json.dumps(body))
        self.assertTrue(guard.before_call(name, {**args, 'offset': 0, 'length': 4096}).should_halt)
        for different in ({'member_index': 1}, {'offset': 1}, {'listing_ref': {'id': 'listing', 'version': 'v2'}}):
            self.assertFalse(guard.before_call(name, {**args, **different}).should_halt)
            self.assertNotEqual(ToolCallSignature.from_call(name, args), ToolCallSignature.from_call(name, {**args, **different}))
        self.assertNotEqual(_result_hash(json.dumps(body), name, args), _result_hash(json.dumps({**body, 'text': 'new body'}), name, args))

    def test_alias_names_share_failure_budget_without_changing_threshold(self):
        from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True, same_tool_failure_halt_after=3))
        for n, name in enumerate((self.names[0], self.names[1], self.names[0])):
            decision = guard.after_call(name, {'action': 'archive_read', 'offset': n}, '{"error":"bad input"}', failed=True)
            self.assertEqual(decision.should_halt, n == 2)
        self.assertEqual(decision.count, 3)



class ReopenedImageIdentityTests(unittest.TestCase):
    setUp = ImageOriginTests.setUp
    segment = staticmethod(ImageOriginTests.segment)
    read_args = ImageOriginTests.read_args
    def test_reopen_changes_only_runtime_device_identity(self):
        from forsic_plugin.image_reader import inspect
        original = self.node.lstat
        def stat_with_device(device):
            st = original()
            return SimpleNamespace(**{k: device if k == 'st_dev' else getattr(st,k) for k in
                ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns','st_mtime','st_ctime')})
        with patch.object(self.node, 'lstat', side_effect=lambda: stat_with_device(101)):
            args = self.read_args()
        with patch.object(self.node, 'lstat', side_effect=lambda: stat_with_device(202)):
            read = inspect(str(self.image), args)
        self.assertEqual(read['text'], 'body')
        self.assertNotIn('st_dev', read['archive_identity'])
        self.assertIn('st_dev', read['archive_origin']['segments'][0]['metadata'])
        self.assertEqual(read['identity_strength'], 'metadata_checked')
        for field in ('st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode'):
            changed = stat_with_device(303)
            setattr(changed, field, getattr(changed,field)+1)
            with self.subTest(field=field), patch.object(self.node,'lstat',return_value=changed):
                with self.assertRaises(ValueError):
                    inspect(str(self.image),args)
        # Old process-local identities are not silently upgraded or accepted.
        args['_archive_selection']['archive_identity']['st_dev'] = 101
        with self.assertRaisesRegex(ValueError,'re-list'):
            inspect(str(self.image),args)

    def test_host_segment_device_change_is_still_rejected(self):
        from forsic_plugin.image_reader import inspect
        args = self.read_args()
        args['_archive_selection']['archive_origin']['segments'][0]['metadata']['st_dev'] += 1
        with self.assertRaisesRegex(ValueError,'segments or selected volume changed'):
            inspect(str(self.image),args)

if __name__ == '__main__':
    unittest.main()
