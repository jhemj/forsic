"""A locally downloadable evidence archive never expands remote delivery scope."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from forsic_plugin.report_driven.finalization import HTML_REPORT_NAMES, report_files
from forsic_plugin.report_driven.host import digest
from forsic_plugin.report_driven.views import artifact, bundle
from test_report_driven import fixture, worked


class EvidenceDeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)
        self.state = worked(self.case)

    def test_archive_download_and_report_delivery_have_distinct_scopes(self):
        result = bundle(self.case, self.state)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual({x['name'] for x in manifest['files']}, set(HTML_REPORT_NAMES) | {'selected-evidence.zip'})
        _, delivered = report_files(self.case.output, result['bundle_id'], self.case.config['case_id'])
        self.assertEqual([x['name'] for x in delivered], list(HTML_REPORT_NAMES))
        self.assertEqual({Path(x).name for x in result['files']}, set(HTML_REPORT_NAMES))
        archive = artifact(self.case, result['bundle_id'], 'selected-evidence.zip')
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                         next(x['sha256'] for x in manifest['files'] if x['name'] == archive.name))

    def test_masked_report_has_no_original_archive(self):
        result = bundle(self.case, self.state, redact=['maintenance'])
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual({x['name'] for x in manifest['files']}, set(HTML_REPORT_NAMES))
        self.assertEqual(manifest['evidence_export']['status'], 'not_exported_redacted')
        self.assertFalse((Path(result['manifest']).parent / 'selected-evidence.zip').exists())
        with self.assertRaises(ValueError):
            artifact(self.case, result['bundle_id'], 'selected-evidence.zip')

    def test_changed_archive_is_detected_before_delivery_or_download(self):
        result = bundle(self.case, self.state)
        archive = Path(result['manifest']).parent / 'selected-evidence.zip'
        archive.write_bytes(archive.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            report_files(self.case.output, result['bundle_id'], self.case.config['case_id'])
        with self.assertRaises(ValueError):
            artifact(self.case, result['bundle_id'], 'executive.html')

    def test_even_rehashed_manifest_cannot_add_arbitrary_downloads(self):
        result = bundle(self.case, self.state)
        root = Path(result['manifest']).parent
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['files'][-1]['name'] = '../private.json'
        manifest['bundle_id'] = digest({k:v for k,v in manifest.items() if k != 'bundle_id'})
        renamed = root.with_name('report-bundle-' + manifest['bundle_id'])
        root.rename(renamed)
        (renamed / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            report_files(self.case.output, manifest['bundle_id'], self.case.config['case_id'])


if __name__ == '__main__':
    unittest.main()
