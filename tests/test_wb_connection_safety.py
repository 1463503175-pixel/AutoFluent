import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"worker"))
import af_wb_bridge as wb


def test_handshake_uses_auto_mesh_system_inventory_not_fluent_tui():
    class Client:
        def run_script_string(self, code):
            if 'GetAllSystems' in code:
                return '[]'
            assert 'SendCommand' not in code
            return '"AUTOMESH_PROBE_OK"'
    proof=wb._wb_only_handshake(Client())
    assert proof['probe']=='AUTOMESH_PROBE_OK' and proof['system_count']==0


def test_invalid_security_rejected_before_fluent_probe():
    with patch.object(wb, "connect", side_effect=AssertionError("must not connect")):
        try:
            wb.discover_fluent_on_verified_wb(9935,"auto")
        except ValueError:
            pass
        else:
            raise AssertionError("unverified security allowed")


def test_no_fluent_does_not_send_tui_or_start_new_process():
    with patch.object(wb,"connect",return_value=object()), \
         patch.object(wb,"_wb_only_handshake",return_value="AUTOFLUENT_WB_ONLY_OK"), \
         patch.object(wb,"list_project_systems",return_value=[]), \
         patch.object(wb,"safe_probe",side_effect=AssertionError("must not TUI")), \
         patch.object(wb,"bootstrap_using_am",side_effect=AssertionError("must not bootstrap")):
        info=wb.discover_fluent_on_verified_wb(9935,"mtls")
    assert info["status"]=="no_fluent_system"
