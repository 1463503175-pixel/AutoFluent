# -*- coding: utf-8 -*-
"""Report overview checks. Historical CSV is the user's reference, not a new H5 run."""
import pathlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_report_summary import (reference_summary, adaptive_summary, inspection_summary,
                               insert_summary, create_reference_overview)
from af_native_profile import parse_native_csv


class SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        historical = pathlib.Path('/mnt/data/从零复现验证.csv')
        if historical.exists():
            cls.metrics=parse_native_csv(historical)
        else:
            # Same interface as the 11.9 parser, no synthetic claims about H5.
            cls.metrics={
                'channel_count':376, 'inlet_net_kg_s':1.25e-5,
                'outlet_net_kg_s':1.250029009e-5,
                'mass_imbalance_pct':.00232072,
                'pressure_drop_pa_low_speed_approx':.647632007,
                'baffle_holes':245,
                'layers':{'upper':{'channel_count':188,'net_kg_s':8.798586923e-6,
                    'share_inlet_pct':70.388695384,'cv_pct':3.267592698,
                    'partition_net_kg_s':{'central':1.70482e-6}},
                          'lower':{'channel_count':188,'net_kg_s':8.715046962e-6,
                    'share_inlet_pct':69.720375696,'cv_pct':3.480823629,
                    'partition_net_kg_s':{'central':1.86996e-6}}}
            }

    def test_codex_11_9_reference_values_summary(self):
        t=reference_summary(self.metrics)
        for token in ('376','0.647632','70.389%','69.720%','3.2676%','3.4808%',
                      '0.002321%','中央旁路','低 Z 外围','高 Z 外围','串联'):
            self.assertIn(token,t)
        self.assertNotIn('总片间流量占入口 140',t)
        self.assertNotIn('求解已收敛',t)
        self.assertIn('尚未独立核验',t)

    def test_escaped_untrusted_label_and_no_fabricated_values(self):
        fake={'status':'not_computed','physical_analysis_status':'<script>alert(1)</script>'}
        t=inspection_summary(fake)
        self.assertIn('&lt;script&gt;',t)
        self.assertNotIn('<script>',t)
        self.assertIn('未取得',t)
        self.assertNotIn('376',t)

    def test_candidate_geometry_marked_provisional(self):
        fake={'layers':{'upper':{'channel_count':17,'cv_pct':8.2,
                                'net_kg_s':1.23e-6,'share_inlet_pct':10.2,
                                'partition_net_kg_s':{'central':2.5e-7},
                                'cut_height_sensitivity':{'status':'skipped_resource_budget'}},
                        'lower':{'channel_count':19,'cv_pct':9.1,
                                 'net_kg_s':1.4e-6,'share_inlet_pct':12.3,
                                 'cut_height_sensitivity':{'status':'stable'}}},
              'port_flux':{'inlet_kg_s':1.2e-5,'outlet_kg_s':1.19e-5,'mass_imbalance_pct':.8333},
              'pressure':{'status':'unavailable'}}
        t=adaptive_summary(fake)
        self.assertIn('36',t)
        self.assertIn('候选',t)
        self.assertIn('未取得',t)
        self.assertIn('9.1000%',t)
        self.assertNotIn('0.000000 Pa',t)
        self.assertIn('skipped_resource_budget',t)

    def test_insert_preserves_existing_detail(self):
        html='<!doctype html><html><head><title>a</title></head><body><h1>报告</h1><table><tr><td>original</td></tr></table></body></html>'
        out=insert_summary(html,reference_summary(self.metrics))
        self.assertEqual(out.count('original'),1)
        self.assertLess(out.index('一页看懂'),out.index('报告</h1>'))
        self.assertIn('</html>',out)

    def test_reference_wrapper_keeps_raw_report(self):
        with tempfile.TemporaryDirectory() as d:
            raw=pathlib.Path(d)/'native.html'
            raw.write_text('<!doctype html><h1>原始 11.9 报告</h1><table><tr><td>原文</td></tr></table>','utf8')
            overview=create_reference_overview(raw,self.metrics,provenance_label='历史文件（非新计算）')
            self.assertEqual(raw.read_text('utf8').count('一页看懂'),0)
            content=pathlib.Path(overview).read_text('utf8')
            self.assertIn('一页看懂',content)
            self.assertIn('历史文件（非新计算）',content)
            self.assertIn('原文',content)
            with self.assertRaises(FileExistsError):
                create_reference_overview(raw,self.metrics)

    def test_reference_backend_uses_summary_without_modifying_raw_html(self):
        from unittest.mock import patch
        import shutil
        import af_native_profile
        fixture=ROOT/'tests/fixtures/native_11_9_27steps.csv'
        with tempfile.TemporaryDirectory() as directory:
            folder=pathlib.Path(directory)
            ca=folder/'11.9.cas.h5';da=folder/'11.9.dat.h5'
            ca.write_bytes(b'case-mocked-only');da.write_bytes(b'data-mocked-only')
            def fake_reference_execution(path, run_name):
                import sys
                prefix=pathlib.Path(sys.argv[sys.argv.index('--output')+1])
                shutil.copyfile(fixture,str(prefix)+'.csv')
                pathlib.Path(str(prefix)+'.html').write_text(
                    '<!doctype html><h1>原脚本报告</h1><table><tr><td>原始数据</td></tr></table>',encoding='utf-8')
            with patch.object(af_native_profile,'_preflight',return_value=(True,'')), \
                 patch.object(af_native_profile.runpy,'run_path',side_effect=fake_reference_execution):
                output=af_native_profile.try_reference_native(ca,da,folder/'result')
            self.assertEqual(output['status'],'native_11_9_profile_computed',output)
            self.assertTrue(pathlib.Path(output['detail_html_file']).exists())
            self.assertNotIn('一页看懂',pathlib.Path(output['detail_html_file']).read_text(encoding='utf-8'))
            combined=pathlib.Path(output['html_file']).read_text(encoding='utf-8')
            self.assertIn('仿真结果与结构优化',combined)
            self.assertIn('1. 入口气体去了哪里',combined)
            self.assertIn('11.9 严格专用几何原生统计',combined)
            self.assertIn('原始数据',pathlib.Path(output['detail_html_file']).read_text(encoding='utf-8'))
            self.assertNotIn('原脚本报告',combined)
            self.assertTrue(pathlib.Path(output['csv_file']).is_file())
            self.assertTrue(pathlib.Path(output['json_file']).is_file())

    def test_missing_heading_rejected(self):
        with self.assertRaises(ValueError):
            insert_summary('<p>无标题</p>','x')

if __name__=='__main__':
    unittest.main()
