"""End-to-end native CLI acceptance on synthetic data; not a Fluent production run."""
import json
import os
import subprocess
import sys
from pathlib import Path

from test_adaptive_full_cff import build_synthetic_ald_pair

ROOT=Path(__file__).resolve().parents[1]


def test_real_cli_path_returns_new_template_not_obsolete_report(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True,two_layers=True)
    out=tmp_path/'data'/'h5_indexes'
    cmd=[sys.executable,str(ROOT/'worker'/'af_h5_offline.py'), '--file',str(case),
         '--output-dir',str(out),'--skip-pyfluent']
    pr=tmp_path/'progress.json'
    run=subprocess.run(cmd,env={**os.environ,'AUTOFLUENT_PROGRESS_FILE':str(pr)},
                       capture_output=True,text=True,timeout=60)
    assert run.returncode==0,(run.stdout[:350],run.stderr[:500])
    result=json.loads(run.stdout)
    assert result['status']=='pair_verified_structure_indexed'
    assert result['physical_analysis_status']=='adaptive_native_candidate_computed'
    html_file=Path(result['html_file'])
    assert html_file.is_file()
    assert html_file.parent==tmp_path/'data'/'native_reports'
    html=html_file.read_text(encoding='utf-8')
    assert '<h1>仿真结果与结构优化</h1>' in html and '<title>' in html
    for heading in ('入口气体去了哪里','晶圆片间哪里流量偏低','挡板开孔与出口',
                    '压力与压降','数据可信度','下一轮仿真怎么安排'):
        assert heading in html
    assert 'Fluent 原生保存解自动分区（候选结果）' not in html
    assert '2.0 加深集气腔方案' not in html
    assert 'https://' not in html and '<script src=' not in html
    assert Path(result['analysis_json']).is_file()
    assert Path(result['native_csv']).is_file()
    progress=json.loads(pr.read_text(encoding='utf-8'))
    assert progress['percent']==100
    assert progress['meaning']=='weighted_completed_stages_not_eta'


def test_failed_config_does_not_forge_percent_near_100(tmp_path):
    # A missing Case/Data pair is an early failure. It cannot claim 99% done.
    progress=tmp_path/'progress.json'
    cmd=[sys.executable,str(ROOT/'worker'/'af_h5_offline.py'), '--file',str(tmp_path/'missing.cas.h5'),
         '--output-dir',str(tmp_path/'report'),'--skip-pyfluent']
    run=subprocess.run(cmd,env={**os.environ,'AUTOFLUENT_PROGRESS_FILE':str(progress)},
                       capture_output=True,text=True,timeout=15)
    assert run.returncode!=0
    assert json.loads(run.stdout)['status']=='error'
    assert int(json.loads(progress.read_text(encoding='utf-8'))['percent'])<20
