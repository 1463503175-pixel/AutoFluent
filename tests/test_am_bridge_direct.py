"""Verify the port attach uses AutoMesh's exact connect signature and fallback."""
from pathlib import Path
import sys
import types
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'worker'))
import am_workbench_bridge as am
import af_wb_bridge as af


def test_am_connect_signature_including_ipv4_and_client_workdir():
    core = types.ModuleType('ansys.workbench.core')
    seen=[]
    core.connect_workbench=lambda **kw: seen.append(kw) or object()
    with patch.dict(sys.modules, {'ansys': types.ModuleType('ansys'),
            'ansys.workbench': types.ModuleType('ansys.workbench'), 'ansys.workbench.core': core}):
        af.connect(9935, 'insecure')
    assert seen and seen[0]['host']=='127.0.0.1'
    assert seen[0]['port']==9935 and seen[0]['security']=='insecure'
    assert seen[0]['client_workdir']==str(Path(am.__file__).resolve().parent)


def test_am_probe_handles_bare_and_encoded_returns():
    class WB:
        def __init__(self, x): self.x=x
        def run_script_string(self, code): return self.x
    for value in ('AUTOMESH_PROBE_OK','"AUTOMESH_PROBE_OK"'):
        assert am.read_only_probe(WB(value))=='AUTOMESH_PROBE_OK'
    assert am.read_only_probe(WB(None))=='AUTOMESH_PROBE_NO_RETURN'


def test_no_return_uses_am_local_receipt(tmp_path):
    # Inject AM's original fallback with temporary writeable output path.
    class WB:
        def __init__(self): self.count=0
        def run_script_string(self, script):
            self.count+=1
            if self.count==1: return None
            # The original AM fallback builds script with a file path. Simulate
            # the Workbench IronPython writing it before returning None.
            import re, json
            match=re.search(r'with open\(u("[^"]+"),\'w\'\)',script)
            assert match is not None
            path=Path(json.loads(match.group(1)))
            path.write_text(json.dumps([{'name':'A','label':'Fluent system','mesh':False}]),encoding='utf8')
            return None
    assert am.list_systems(WB()) == [{'name':'A','label':'Fluent system','mesh':False}]


def test_ambiguous_wb_probe_does_not_block_successful_am_system_list():
    with patch.object(af, 'read_only_probe', side_effect=RuntimeError('None transfer')):
        with patch.object(af, 'am_list_systems',return_value=[]):
            proof=af._wb_only_handshake(object())
    assert proof['system_count']==0 and 'AM_PROBE_UNAVAILABLE' in proof['probe']


def test_am_list_systems_must_succeed_to_report_connected():
    with patch.object(af,'read_only_probe',return_value='AUTOMESH_PROBE_NO_RETURN'):
        with patch.object(af,'am_list_systems',side_effect=RuntimeError('WB list failed')):
            try: af._wb_only_handshake(object())
            except RuntimeError as exc: assert 'WB list failed' in str(exc)
            else: raise AssertionError('Missing WB inventory should block true connected status')
