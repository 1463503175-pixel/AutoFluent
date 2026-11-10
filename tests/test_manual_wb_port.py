import sys
from pathlib import Path
from unittest.mock import patch
import types
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
import af_wb_bridge as wb


def test_manual_mode_rejects_invalid_ports_before_probe():
    for port in (0, 80, 1000, 65536, "3247"):
        with pytest.raises(ValueError):
            wb.manual_connect(port)


def _fake_ansys_modules():
    return {'ansys':types.ModuleType('ansys'),
            'ansys.workbench':types.ModuleType('ansys.workbench'),
            'ansys.workbench.core':types.ModuleType('ansys.workbench.core')}


def test_only_workbench_is_open_and_handshake_succeeds():
    calls=[]
    def conn(port,security):
        calls.append((port,security)); return object()
    with patch.dict(sys.modules,_fake_ansys_modules()), \
         patch.object(wb,'connect',side_effect=conn), \
         patch.object(wb,'_wb_only_handshake',return_value='AUTOFLUENT_WB_ONLY_OK'), \
         patch.object(wb,'list_fluent_systems',side_effect=AssertionError('must not enumerate Fluent yet')), \
         patch.object(wb,'safe_probe',side_effect=AssertionError('must not send TUI')), \
         patch.object(wb,'candidate_ports',side_effect=AssertionError('port discovery forbidden')), \
         patch.object(wb,'bootstrap_using_am',side_effect=AssertionError('StartServer forbidden')):
        r=wb.manual_connect(9935)
    assert r['status']=='workbench_connected'
    assert r['port']==9935 and r['systems']==[]
    assert calls==[(9935,'mtls')]


def test_fluent_is_checked_only_after_explicit_call():
    with patch.object(wb,'connect',return_value=object()), \
         patch.object(wb,'_wb_only_handshake',return_value='AUTOFLUENT_WB_ONLY_OK'), \
         patch.object(wb,'list_project_systems',return_value=[]), \
         patch.object(wb,'safe_probe',side_effect=AssertionError('no Fluent must not send TUI')):
        r=wb.discover_fluent_on_verified_wb(9935,'mtls')
    assert r['status']=='no_fluent_system' and r['port']==9935


def test_fluent_system_listing_exception_preserves_verified_wb():
    with patch.object(wb,'connect',return_value=object()), \
         patch.object(wb,'_wb_only_handshake',return_value='AUTOFLUENT_WB_ONLY_OK'), \
         patch.object(wb,'list_fluent_systems',side_effect=RuntimeError('unsupported API')):
        r=wb.discover_fluent_on_verified_wb(9935,'mtls')
    assert r['status']=='workbench_connected_scan_failed'


def test_multi_fluent_requires_selection_without_probe():
    rows=[{'name':'FFF','fluent':True,'container':'Setup'},
          {'name':'FFF2','fluent':True,'container':'Setup'}]
    with patch.object(wb,'connect',return_value=object()), \
         patch.object(wb,'_wb_only_handshake',return_value='AUTOFLUENT_WB_ONLY_OK'), \
         patch.object(wb,'list_project_systems',return_value=rows), \
         patch.object(wb,'safe_probe',side_effect=AssertionError('ambiguous probe forbidden')):
        r=wb.discover_fluent_on_verified_wb(9935,'mtls')
    assert r['status']=='selection_required' and len(r['systems'])==2


def test_manual_connect_reports_all_transport_errors():
    with patch.dict(sys.modules,_fake_ansys_modules()), \
         patch.object(wb,'connect',side_effect=RuntimeError('wrong security mode')):
        r=wb.manual_connect(9935)
    assert r['status']=='workbench_unavailable'
    assert len(r['attempts'])==3


def test_manual_mode_uses_no_implicit_bootstrap():
    text=Path(wb.__file__).read_text(encoding='utf8')
    assert "choices=('manual','probe','fluent','settings','report')" in text
    ui=(Path(__file__).resolve().parents[1]/'cmd/autofluent/main_windows.go').read_text(encoding='utf8')
    assert 'pPostMessage.Call(hwnd, wmAppStartAuto, 0, 0)' not in ui
    assert 'u.startAutoConnect()' not in ui
    assert 'u.app.manualWorkbench(ctx, port)' in ui


def test_wb_roundtrip_uses_am_probe_and_real_system_list():
    with patch.object(wb, 'read_only_probe', return_value='AUTOMESH_PROBE_NO_RETURN'), \
         patch.object(wb, 'am_list_systems',return_value=[]):
        result=wb._wb_only_handshake(object())
    assert result == {'probe':'AUTOMESH_PROBE_NO_RETURN','system_count':0}


def test_manual_security_mode_isolated_from_other_modes():
    called=[]
    def conn(port, security):
        called.append(security)
        raise RuntimeError('transport mismatch')
    with patch.dict(sys.modules,_fake_ansys_modules()), patch.object(wb,'connect',side_effect=conn):
        result=wb.manual_connect(9935, security='insecure')
    assert result['status']=='workbench_unavailable'
    assert called==['insecure']
    assert len(result['attempts'])==1
    assert 'connect_workbench' in result['attempts'][0]
    assert 'RuntimeError' in result['attempts'][0]


def test_manual_security_failure_stage_is_recorded():
    with patch.dict(sys.modules,_fake_ansys_modules()), \
         patch.object(wb,'connect',return_value=object()), \
         patch.object(wb,'_wb_only_handshake',side_effect=RuntimeError('roundtrip failed')):
        result=wb.manual_connect(9935, security='wnua')
    assert result['status']=='workbench_unavailable'
    assert 'run_script_string' in result['attempts'][0]
    assert 'roundtrip failed' in result['attempts'][0]


def test_local_connection_uses_automesh_forced_ipv4_hostname():
    module=types.ModuleType('ansys.workbench.core')
    captured=[]
    def stub(**kwargs):
        captured.append(kwargs)
        return object()
    module.connect_workbench=stub
    with patch.dict(sys.modules,{'ansys':types.ModuleType('ansys'),'ansys.workbench':types.ModuleType('ansys.workbench'), 'ansys.workbench.core':module}):
        wb.connect(9935,'insecure')
    assert captured[0]['port']==9935
    assert captured[0]['security']=='insecure'
    assert captured[0]['host']=='127.0.0.1'
