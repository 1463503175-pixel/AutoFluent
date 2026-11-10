import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
import af_wb_solver as solver


def test_residual_parser_only_accepts_real_header():
    text='1234 7 9\niter continuity x-velocity y-velocity z-velocity energy time/it\n 1 1e-3 2e-3 3e-3 4e-3 5e-3 0:03 22\n 2 1D-4 2e-4 3e-4 4e-4 5e-4 0:03 21\n'
    result=solver.parse_residuals(text)
    assert result['row_count']==2
    assert result['latest']['iteration']==2
    assert result['latest']['residuals']['continuity']==1e-4
    assert result['convergence_certified'] if 'convergence_certified' in result else not result['residual_convergence_certified']
    assert solver.parse_residuals('iter 2 1e-3\n')['row_count']==0


def test_solve_script_is_one_shot_and_no_init(tmp_path):
    script=solver.make_wb_script('FFF','Setup',tmp_path/'solve.trn',tmp_path/'receipt.json',10)
    assert script.count('SendCommand(Command="/solve/iterate 10")')==1
    assert 'initialize-flow' not in script
    assert 'StartServer' not in script
    assert script.count('/file/start-transcript')==1
    assert script.count('/file/stop-transcript')==1
    with pytest.raises(ValueError):
        solver.make_wb_script('FFF','Setup',tmp_path/'s',tmp_path/'receipt',0)


class FakeWB:
    def __init__(self, fail=False):self.calls=0;self.fail=fail
    def run_script_string(self, script):
        self.calls+=1
        assert self.calls==1
        if self.fail:raise RuntimeError('network lost after dispatch')
        def get(key):
            m=re.search(r'^'+key+r'=u("[^"]+")',script,re.M)
            assert m,key
            return Path(json.loads(m.group(1)))
        trn=get('_af_trn');receipt=get('_af_receipt')
        trn.write_text('iter continuity x-velocity y-velocity z-velocity\n 18 1e-4 2e-4 3e-4 4e-4\n',encoding='utf-8')
        receipt.write_text(json.dumps({'state':'iterate_returned'}),encoding='utf-8')
        return None


def test_single_workbench_solve_and_durable_lease(tmp_path):
    wb=FakeWB()
    result=solver.execute(wb,[{'name':'FFF','fluent':True,'container':'Setup'}], 'FFF',10,str(tmp_path))
    assert wb.calls==1
    assert result['status']=='residuals_observed_not_convergence_certified'
    assert result['residual_rows']==1
    assert result['convergence_certified'] is False
    assert not (tmp_path/'active_solve.lock').exists()
    assert Path(result['residual_file']).is_file()


def test_unknown_remote_keeps_lock_and_blocks_resubmit(tmp_path):
    wb=FakeWB(fail=True)
    with pytest.raises(RuntimeError,match='lease retained'):
        solver.execute(wb,[{'name':'FFF','fluent':True,'container':'Setup'}], 'FFF',10,str(tmp_path))
    assert (tmp_path/'active_solve.lock').exists()
    with pytest.raises(FileExistsError):
        solver.execute(FakeWB(),[{'name':'FFF','fluent':True,'container':'Setup'}], 'FFF',10,str(tmp_path))


def test_no_remote_command_on_bad_identity(tmp_path):
    wb=FakeWB()
    with pytest.raises(RuntimeError,match='project identity changed'):
        solver.execute(wb,[{'name':'FFF','fluent':True,'container':'Setup'}], 'FFF',10,str(tmp_path),expected_project='wrong',project_reader=lambda _: {'project_file':'right'})
    assert wb.calls==0
    assert not (tmp_path/'active_solve.lock').exists()
