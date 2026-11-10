from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from af_codex_import import import_saved_report
from af_metrics import flux_metrics
from af_compare import compare_records
import af_wb_bridge as wb


def demo_html(n=3, missing=False):
    rows = [('<tr><th>区域</th><th>面数</th><th>位置</th><th>正向</th><th>反向</th><th>净流量或绝对误差</th><th>说明</th></tr>')]
    def row(name, fwd, rev=0, net=None):
        if net is None: net=fwd-rev
        rows.append(f'<tr><td>{name}</td><td>40</td><td></td><td>{fwd}</td><td>{rev}</td><td>{net}</td><td></td></tr>')
    row('入口1', 1e-5)
    row('入口合计',1e-5)
    row('出口1',1.001e-5)
    row('出口合计',1.001e-5)
    for layer in ('上层','下层'):
        for i in range(1,n+1):
            if missing and i==2: continue
            row(layer+'晶圆通道 '+str(i),1e-6 + (i-1)*1e-7)
        row(layer+'中央旁路',1e-6,2e-7,8e-7)
        row(layer+'左外围旁路（低 Z）',5e-7)
        row(layer+'右外围旁路（高 Z）',6e-7)
        row(layer+'原生面全截面',4.2e-6)
    return '<!doctype html><html><h1>Fluent 当前解统计</h1><p>来源为 11.9 的 FFF-Setup-Output Case/Data</p><table>'+''.join(rows)+'</table></html>'


def test_historical_codex_html_import_and_cv(tmp_path):
    p=tmp_path/'当前解只读统计.html';p.write_text(demo_html(),encoding='utf-8')
    r=import_saved_report(p)
    assert r['provenance']['model_id']=='11.9'
    assert len(r['layers']['upper'])==3 and len(r['layers']['lower'])==3
    assert r['layers']['upper'][0]==1e-6
    assert r['bypass']['upper']['上层中央旁路']['net_kg_s']==8e-7
    m=flux_metrics(r)
    assert m['layers']['upper']['channels']==3
    assert m['layers']['upper']['cv_pct']>0
    assert m['verification']=='external_report_not_independently_verified'
    assert m['mass_imbalance_pct'] == pytest.approx(.1)
    assert m['bypass']['upper']['上层中央旁路']['fraction_of_inlet_pct']==pytest.approx(8)
    assert m['provenance_status']=='incomplete'  # no verified case template hash


def test_missing_channel_no_fabrication(tmp_path):
    p=tmp_path/'bad.html';p.write_text(demo_html(missing=True),encoding='utf-8')
    with pytest.raises(ValueError, match='通道编号缺失'):
        import_saved_report(p)


def test_side_by_side_unverified_is_visible_not_ranked(tmp_path):
    a=tmp_path/'a.html'; b=tmp_path/'b.html'
    a.write_text(demo_html(),encoding='utf-8');b.write_text(demo_html(),encoding='utf-8')
    report=compare_records(import_saved_report(a),import_saved_report(b))
    assert report['status']=='not_qualified'
    assert 'upper' in report['indicators']
    assert '上层中央旁路' in report['bypass_comparison_pct']['upper']


def test_single_tui_send_only():
    class CountingWB:
        count=0
        def run_script_string(self,script):
            self.count+=1
            return 'AUTOFLUENT_TUI_REPORT_SUBMITTED'
    instance=CountingWB()
    result=wb.safe_probe(instance,[dict(name='FFF',fluent=True,container='Setup')])
    assert result['status']=='tui_probe_submitted'
    assert instance.count==1


def test_case_inventory_only_matching_suffix_and_sorted(tmp_path):
    data=tmp_path/'dp0'/'FFF'/'Fluent';data.mkdir(parents=True)
    (data/'FFF-Setup-Output.cas.h5').write_bytes(b'a'*200)
    (data/'FFF-Setup-Output.dat.h5').write_bytes(b'b'*220)
    (data/'case_not_real.tmp').write_bytes(b'x'*200)
    (data/'short.cas.h5').write_bytes(b'x')
    rows=wb.inventory_case_data(tmp_path)
    assert len(rows)==2 and all(x['bytes']>=200 for x in rows)
    assert sorted(x['kind'] for x in rows)==['case','data']


def test_wb_identity_uses_running_project_not_guess(tmp_path):
    class Session:
        def run_script_string(self,cmd):
            assert 'GetProjectFile()' in cmd and 'GetProjectDirectory()' in cmd
            return '{"project_file":"E:/ALD.wbpj","project_dir":"E:/ALD_files"}'
    assert wb.read_project_identity(Session())['project_file']=='E:/ALD.wbpj'


def test_wb_export_modified_settings_once(tmp_path):
    class WB:
        calls=0
        def run_script_string(self,src):
            self.calls += 1
            assert 'SendCommand' in src and '/file/write-settings' in src
            assert '/file/read-settings' not in src
            assert 'iterate' not in src and 'initialization' not in src
            import re
            match=re.search(r'fluent_settings_\d{8}_\d{6}_[a-f0-9]{10}\.set',src)
            assert match is not None
            (tmp_path/match.group(0)).write_text('settings from running Fluent:'+'\nphysics yes'*10)
            return None  # WB may not return script values; written settings are proof
    client=WB()
    report=wb.export_existing_fluent_settings(client,[dict(name='FFF',fluent=True,container='Setup')], 'FFF',tmp_path)
    assert client.calls == 1
    assert report['status']=='settings_exported'
    assert report['bytes']>32 and len(report['sha256'])==64


def test_wb_export_invalid_selection_never_sends(tmp_path):
    class WB:
        def run_script_string(self,src): raise AssertionError('must not dispatch')
    with pytest.raises(RuntimeError):
        wb.export_existing_fluent_settings(WB(),[dict(name='FFF',fluent=True,container='Setup')],'WRONG',tmp_path)
