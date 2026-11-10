import json
import os
from pathlib import Path
import sys
import time

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
import af_wb_conditions as cond

SYSTEMS=[{'name':'FFF','fluent':True,'container':'Setup'}]

class FakeWB:
    def __init__(self, fail=False):self.calls=[];self.fail=fail
    def run_script_string(self, script):
        self.calls.append(script)
        assert 'read-settings' in script
        assert 'initialize-flow' not in script
        if self.fail:raise RuntimeError('transport unknown')
        import re
        m=re.search(r'_af_receipt=u("[^"]+")',script)
        p=Path(json.loads(m.group(1)))
        p.write_text(json.dumps({'status':'read_settings_returned'}),encoding='utf-8')
        return None


def fixture(tmp_path):
    (tmp_path/'settings_exports').mkdir()
    baseline=tmp_path/'settings_exports'/'baseline.set'
    baseline.write_text('some modified-settings-for-inlet\n',encoding='utf-8')
    content=[b'other modified setup, not baseline\n', b'other modified setup, not baseline\n',baseline.read_bytes()]
    def export(wb, systems, system, directory):
        body=content.pop(0)
        path=Path(directory)/('snapshot'+str(time.time_ns())+'.set')
        path.write_bytes(body)
        return {'status':'settings_exported','file':str(path),'sha256':cond.sha(path)}
    return baseline,export


def test_settings_preflight_readback_no_auto_publish(tmp_path):
    src,exp=fixture(tmp_path)
    wb=FakeWB()
    rd=lambda _: {'project_file':'C:/project.wbpj'}
    plan=cond.preview(wb,SYSTEMS,'FFF',str(src),str(tmp_path),'C:/project.wbpj',rd,exp)
    assert plan['status']=='review_required'
    assert not wb.calls
    assert plan['same_raw_settings_file'] is False
    result=cond.apply(wb,SYSTEMS,'FFF',plan['plan_file'],str(tmp_path),'C:/project.wbpj',rd,exp,approved=True)
    assert result['status']=='user_modified_settings_readback_match'
    assert result['full_physics_verified'] is False
    assert result['zone_mapping_independently_verified'] is False
    assert len(wb.calls)==1
    assert not (tmp_path/'settings_jobs'/'active_publication.lock').exists()
    with pytest.raises(RuntimeError,match='already used'):
        cond.apply(wb,SYSTEMS,'FFF',plan['plan_file'],str(tmp_path),'C:/project.wbpj',rd,exp,approved=True)


def test_settings_never_publishes_without_approval(tmp_path):
    src,exp=fixture(tmp_path)
    wb=FakeWB()
    rd=lambda _: {'project_file':'X'}
    plan=cond.preview(wb,SYSTEMS,'FFF',str(src),str(tmp_path),'X',rd,exp)
    with pytest.raises(PermissionError):
        cond.apply(wb,SYSTEMS,'FFF',plan['plan_file'],str(tmp_path),'X',rd,exp,approved=False)
    assert wb.calls==[]


def test_settings_unknown_receipt_keeps_lock(tmp_path):
    src,exp=fixture(tmp_path)
    wb=FakeWB(fail=True)
    rd=lambda _: {'project_file':'X'}
    plan=cond.preview(wb,SYSTEMS,'FFF',str(src),str(tmp_path),'X',rd,exp)
    with pytest.raises(RuntimeError,match='lease retained'):
        cond.apply(wb,SYSTEMS,'FFF',plan['plan_file'],str(tmp_path),'X',rd,exp,approved=True)
    assert (tmp_path/'settings_jobs'/'active_publication.lock').exists()
    assert len(wb.calls)==1


def test_settings_source_must_be_app_export(tmp_path):
    (tmp_path/'settings_exports').mkdir()
    alien=tmp_path/'wrong.set'
    alien.write_text('arbitrary content not from application\n')
    with pytest.raises(ValueError):
        cond.validate_source(str(alien),str(tmp_path))
