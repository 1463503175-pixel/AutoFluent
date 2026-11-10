import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
import af_wb_bridge as wb

class FakeWB:
    def __init__(self, response): self.response=response;self.script=''
    def run_script_string(self, script):self.script=script;return self.response

def test_netstat_loopback_filters_noise():
    data=''' TCP 127.0.0.1:49234 0.0.0.0:0 LISTENING 1234
 TCP 0.0.0.0:55001 0.0.0.0:0 侦听 2345
 TCP 192.168.1.2:40000 0.0.0.0:0 LISTENING 3333
 TCP 127.0.0.1:21232 127.0.0.1:1 ESTABLISHED 4444'''
    assert wb.parse_netstat(data)=={(49234,1234),(55001,2345)}

def test_workbench_list_from_actual_script_result():
    r=[{'name':'FFF','label':'Fluid Flow (Fluent)','fluent':True,'container':'Setup'},
       {'name':'SYS','label':'Static Structural','fluent':False,'container':''}]
    fw=FakeWB(json.dumps(r))
    assert wb.list_fluent_systems(fw)==r[:1]
    assert 'GetAllSystems()' in fw.script
    assert 'GetFluentLauncherSettings' in fw.script

def test_fluent_tui_probe_readonly_and_no_editor_launch():
    obj=[{'name':'FFF','label':'Fluent','fluent':True,'container':'Setup'}]
    fw=FakeWB('AUTOFLUENT_TUI_REPORT_SUBMITTED')
    response=wb.safe_probe(fw,obj)
    assert response['status']=='tui_probe_submitted'
    assert 'SendCommand' in fw.script
    assert '/report/summary' in fw.script
    for action in ('.Edit(','.Import(','.Exit(','.Initialize(','.CreateSystem('):
        assert action not in fw.script

def test_no_ambiguous_fluent_target():
    rows=[{'name':'FFF','fluent':True},{'name':'FFF2','fluent':True}]
    assert wb.safe_probe(FakeWB(''),rows)['status']=='selection_required'

def test_cached_port_not_taken_as_trusted_connection():
    with patch.object(wb,'read_am_connection',return_value=[(23456,'mtls')]),\
         patch.object(wb,'listeners',return_value={(23456,1024)}),\
         patch.object(wb,'process_names',return_value={1024:'runwb2.exe'}):
        assert wb.candidate_ports()==[(23456,'mtls','AutoMesh cached port')]

def test_probe_status_requires_returned_tui_receipt():
    f=FakeWB('"AUTOFLUENT_TUI_REPORT_SUBMITTED"')
    assert wb.safe_probe(f,[{'name':'FFF','fluent':True,'container':'Setup'}])['receipt']=='AUTOFLUENT_TUI_REPORT_SUBMITTED'

class FakeProjectSystem:
    def __init__(self, name, label, setup=False, solution=False, launcher_raises=True):
        self.Name=name
        self.DisplayText=label
        self._containers={}
        if setup:
            self._containers['Setup']=FakeWBContainer(launcher_raises)
        if solution:
            self._containers['Solution']=FakeWBContainer(launcher_raises)

    def GetContainer(self, ComponentName):
        if ComponentName not in self._containers:
            raise RuntimeError('No such container')
        return self._containers[ComponentName]


class FakeWBContainer:
    def __init__(self, fail): self.fail=fail
    def GetFluentLauncherSettings(self):
        if self.fail: raise RuntimeError('Cannot query launcher settings for active editor')
        return object()


class FakeWorkbenchScriptRunner:
    def __init__(self, systems): self.systems=systems
    def run_script_string(self, script):
        namespace={'GetAllSystems':lambda: self.systems}
        exec(script,namespace)
        return namespace.get('wb_script_result')


def test_fluent_label_fallback_when_launcher_api_fails():
    wbclient=FakeWorkbenchScriptRunner([
        FakeProjectSystem('A','Fluid Flow (Fluent)',setup=True,launcher_raises=True),
        FakeProjectSystem('B','Static Structural',setup=True,launcher_raises=True),
    ])
    rows=wb.list_project_systems(wbclient)
    assert len(rows)==2
    assert rows[0]['fluent'] is True
    assert rows[0]['evidence']=='system_label'
    assert rows[1]['fluent'] is False
    assert len(wb.select_fluent_systems(rows))==1


def test_real_empty_workbench_is_not_mislabeled_as_fluent():
    wbclient=FakeWorkbenchScriptRunner([])
    assert wb.list_project_systems(wbclient)==[]
    assert wb.list_fluent_systems(wbclient)==[]


def test_launcher_api_positive_signal_without_explicit_label():
    wbclient=FakeWorkbenchScriptRunner([
        FakeProjectSystem('A','用户自定义系统名称',setup=True,launcher_raises=False)
    ])
    systems=wb.list_fluent_systems(wbclient)
    assert len(systems)==1
    assert systems[0]['evidence']=='launcher_api'


def test_fluent_text_without_setup_solution_is_not_targeted():
    wbclient=FakeWorkbenchScriptRunner([
        FakeProjectSystem('A','Fluent documentation',setup=False,solution=False)
    ])
    assert wb.list_fluent_systems(wbclient)==[]


def test_fluent_discovery_preserves_all_systems_and_separate_tui_validation():
    client=FakeWorkbenchScriptRunner([
        FakeProjectSystem('A','Fluid Flow (Fluent)',setup=True),
        FakeProjectSystem('B','Static Structural',setup=True)
    ])
    with patch.object(wb,'connect',return_value=client), \
         patch.object(wb,'_wb_only_handshake',return_value={'probe':'OK','system_count':2}), \
         patch.object(wb,'read_project_identity',return_value={'project_file':'E:/case/model.wbpj','project_dir':'E:/case'}), \
         patch.object(wb,'inventory_case_data',return_value=[]), \
         patch.object(wb,'safe_probe',return_value={'status':'tui_probe_submitted','receipt':'OK'}) as probe:
        info=wb.discover_fluent_on_verified_wb(9935,'insecure')
    assert info['status']=='fluent_connected'
    assert len(info['all_systems'])==2
    assert info['systems'][0]['name']=='A'
    assert info['project_file']=='E:/case/model.wbpj'
    probe.assert_called_once()


def test_wrong_workbench_system_is_diagnosed_without_tui():
    client=FakeWorkbenchScriptRunner([FakeProjectSystem('A','Static Structural',setup=True)])
    with patch.object(wb,'connect',return_value=client), \
         patch.object(wb,'_wb_only_handshake',return_value={'probe':'OK','system_count':1}), \
         patch.object(wb,'read_project_identity',return_value={'project_file':'E:/other/model.wbpj','project_dir':''}), \
         patch.object(wb,'safe_probe',side_effect=AssertionError('Must never try TUI')):
        info=wb.discover_fluent_on_verified_wb(9935,'insecure')
    assert info['status']=='no_fluent_system'
    assert 'Static Structural' in info['detail']
    assert len(info['all_systems'])==1
