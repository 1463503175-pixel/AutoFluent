import unittest
from types import SimpleNamespace
from unittest.mock import patch

import af_worker


class AutoDraftTests(unittest.TestCase):
    def test_scan_generates_unapproved_template_without_publishing(self):
        snapshot = {'schema': 'autofluent.snapshot.v1', 'fields': [], 'boundaries': [], 'warnings': []}
        template = {'schema': 'autofluent.conditions.v1', 'verified_with_real_case': False,
                    'fields': [], 'zones': []}
        with patch.object(af_worker, 'connect', return_value=SimpleNamespace(settings=object())), \
             patch.object(af_worker, 'scan', return_value=snapshot), \
             patch.object(af_worker, 'export_template', return_value=template) as ex:
            result = af_worker.execute({'action': 'scan', 'server_info': 'fake-file'})
        self.assertIs(result['draft_template'], template)
        self.assertFalse(result['draft_template']['verified_with_real_case'])
        ex.assert_called_once()

    def test_scan_still_succeeds_if_template_export_fails(self):
        snapshot = {'schema': 'autofluent.snapshot.v1', 'fields': [], 'boundaries': [], 'warnings': []}
        with patch.object(af_worker, 'connect', return_value=SimpleNamespace(settings=object())), \
             patch.object(af_worker, 'scan', return_value=snapshot), \
             patch.object(af_worker, 'export_template', side_effect=RuntimeError('incompatible field')):
            result = af_worker.execute({'action': 'scan', 'server_info': 'fake-file'})
        self.assertEqual(result['schema'], 'autofluent.snapshot.v1')
        self.assertIn('无法生成工况草稿', result['warnings'][0])

    def test_scan_auto_previews_previously_approved_template(self):
        snap = {'schema': 'autofluent.snapshot.v1', 'fields': [], 'boundaries': [], 'warnings': []}
        approved = {'schema': 'autofluent.conditions.v1', 'verified_with_real_case': True,
                    'fields': [], 'zones': []}
        plan = {'ok': False, 'blocker_count': 1}
        with patch.object(af_worker, 'connect', return_value=SimpleNamespace(settings=object())), \
             patch.object(af_worker, 'scan', return_value=snap), \
             patch.object(af_worker, 'export_template', return_value={}), \
             patch.object(af_worker, 'make_plan', return_value=plan) as preview:
            result = af_worker.execute({'action': 'scan', 'server_info': 'fake-file',
                                        'template': approved})
        self.assertIs(result['auto_preview'], plan)
        preview.assert_called_once()


if __name__ == '__main__':
    unittest.main()
