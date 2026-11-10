import json
import re
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_live_capture import build_script, examine_report, capture_live_report

SYSTEMS=[{'name':'FFF','container':'Setup','fluent':True}]
SAMPLE='''Ansys Fluent report of current case\nBoundary Conditions\nVelocity Inlet\nMaterial Properties\nNitrogen\nSolver Controls\npressure-based\nFluid Zone: fluid\nBoundary Conditions: inlet outlet wall\nMaterial Properties: gas nitrogen\nModel Settings: laminar\nConvergence Criteria: 1e-5\n'''


def test_build_script_uses_existing_fluent_no_solver_mutation(tmp_path):
    script=build_script('FFF','Setup',tmp_path/'report.trn')
    for keyword in ('/file/start-transcript','/report/summary','/file/show-configuration','/file/stop-transcript'):
        assert script.count(keyword)==1
    for illegal in ('Edit(', 'Initialize(', 'Iterate(', 'ReadCase(', 'write-case', 'read-case', 'launch_workbench', 'run-calculation'):
        assert illegal not in script
    assert 'if not os.path.isfile(_af_path):' in script
    assert 'finally:' in script


def test_invalid_system_path_rejected(tmp_path):
    with pytest.raises(ValueError): build_script('bad\nexecfile','Setup',tmp_path/'f.trn')
    with pytest.raises(ValueError): build_script('FFF','Mesh',tmp_path/'f.trn')
    with pytest.raises(ValueError): build_script('FFF','Setup','relative.trn')


def test_report_file_marker_is_required(tmp_path):
    p=tmp_path/'sample.trn';p.write_text(SAMPLE,encoding='utf-8')
    r=examine_report(p,'FFF','D:/test.wbpj')
    assert r['status']=='report_captured' and len(r['sha256'])==64
    p.write_text('transcript started\n'+('not a fluent summary\n'*20),encoding='utf-8')
    assert examine_report(p,'FFF','')['status']=='report_unverified'


def test_wb_dispatch_exactly_once_with_file_receipt(tmp_path):
    class FakeWB:
        def __init__(self):self.commands=[]
        def run_script_string(self,script):
            self.commands.append(script)
            path_match=re.search(r'_af_path=u(".*")',script)
            assert path_match
            path=Path(json.loads(path_match.group(1)))
            path.write_text(SAMPLE,encoding='utf-8')
            return None  # Workbench may not pass the return value
    wb=FakeWB()
    output=capture_live_report(wb,SYSTEMS,'FFF',str(tmp_path))
    assert output['status']=='report_captured'
    assert output['bytes']==len(SAMPLE.encode())
    assert len(wb.commands)==1


def test_no_dispatch_if_wb_project_changed(tmp_path):
    wb=Mock()
    with pytest.raises(RuntimeError,match='project changed'):
        capture_live_report(wb,SYSTEMS,'FFF',str(tmp_path),expected_project='D:/a.wbpj',
                            project_reader=lambda wb:{'project_file':'D:/b.wbpj'})
    wb.run_script_string.assert_not_called()


def test_failure_is_not_retried(tmp_path):
    class Fake:
        count=0
        def run_script_string(self,script):
            self.count+=1
            raise ConnectionError('transport lost')
    wb=Fake()
    r=capture_live_report(wb,SYSTEMS,'FFF',str(tmp_path))
    assert r['status']=='report_dispatch_uncertain'
    assert wb.count==1


def test_missing_target_rejected(tmp_path):
    wb=Mock()
    with pytest.raises(ValueError):capture_live_report(wb,SYSTEMS,'NOT_FFF',str(tmp_path))
    wb.run_script_string.assert_not_called()
