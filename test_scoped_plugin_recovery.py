"""Offline retained-source recovery through the native plugin's scoped imports."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid


class ScopedPluginRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.namespace = '_forsic_recovery_' + uuid.uuid4().hex
        self.addCleanup(self.clean_imports)
        plugin = Path(__file__).resolve().parent / 'forsic_plugin'
        spec = importlib.util.spec_from_file_location(
            self.namespace, plugin / '__init__.py',
            submodule_search_locations=[str(plugin)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[self.namespace] = package
        spec.loader.exec_module(package)
        self.pages = importlib.import_module(self.namespace + '.report_driven.model_pages')
        self.host = importlib.import_module(self.namespace + '.report_driven.host')
        self.handles = importlib.import_module(self.namespace + '.report_driven.source_handles')
        self.assertIs(self.host.RetainedSourceError, self.pages.RetainedSourceError)
        self.assertTrue(issubclass(self.host.UnknownRetainedSource, self.pages.RetainedSourceError))
        self.assertEqual(package.Case.__module__, self.namespace + '.evidence')

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        evidence = base / 'evidence'
        evidence.mkdir()
        manifest = base / 'case.json'
        manifest.write_text(json.dumps({
            'case_id': 'CASE-synthetic-retained-recovery',
            'evidence_root': str(evidence), 'output_root': str(base / 'results')}))
        self.case = package.Case(manifest)
        self.source_id = self.case.record('tool_result', {
            'tool': 'forsic_read', 'started_id': 'synthetic-start',
            'lines': [{'line': 1, 'text': 'synthetic retained text'}]})
        self.source = self.host.source_record(self.case.event(self.source_id))

    def clean_imports(self):
        for name in list(sys.modules):
            if name == self.namespace or name.startswith(self.namespace + '.'):
                del sys.modules[name]

    def rejected(self, args, code, next_call):
        # All data is preseeded in a temporary case; no backend may start.
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            result = json.loads(self.case.invoke('forsic_reporting', args))
        self.assertEqual(result['error_code'], code)
        self.assertEqual(result['next_call'], next_call)
        self.assertEqual(result['failure_stage'], 'execution')
        self.assertNotIn('result', result)
        event = self.case.event(result['evidence_id'])
        self.assertEqual(event['kind'], 'tool_result')
        self.assertEqual(event['data'], {
            key: value for key, value in result.items() if key != 'evidence_id'})
        return result

    def test_wrong_source_version_preserves_discovery_recovery(self):
        self.rejected({
            'action': 'source', 'source_id': self.source_id,
            'source_version': 'stale', 'pointer': '/lines/0/text'},
            'source_version_mismatch', {'action': 'source', 'source_id': self.source_id})

    def test_missing_pointer_preserves_verified_parent_recovery(self):
        result = self.rejected({
            'action': 'source', 'source_id': self.source_id,
            'source_version': self.source['version'], 'pointer': '/lines/0/absent'},
            'source_pointer_absent', self.pages.source_read_call(
                self.source, '/lines/0', self.handles.source_handle(self.case, self.source)))
        self.assertEqual(result['recovery']['parent_pointer'], '/lines/0')

    def test_unknown_source_preserves_state_discovery_recovery(self):
        coordinates = {'source_id': 'synthetic-unknown', 'source_version': 'stale',
                       'pointer': '/selected/field', 'offset': 12}
        result = self.rejected({'action': 'source', **coordinates},
                               'unknown_retained_source', {'action': 'state'})
        self.assertEqual(result['failed_source'], coordinates)


if __name__ == '__main__':
    unittest.main()
