"""V2.10.4 regression: source-case geometry scan, multi-snapshot pairing, common-cut H5 rerun."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_geometry_precheck import precheck_case_geometry,write_precheck_report
from af_common_cut_compare import compare_saved_pairs_common_cut, render_common_cut_html
from af_h5_batch import inventory
from af_model_config import load_model_config
from af_render_saved import render_stored_json
from test_adaptive_full_cff import build_synthetic_ald_pair


def test_case_only_geometry_check_works_without_data_file(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    data.unlink()  # deliberate: actual Case precheck must not require saved solution
    output=precheck_case_geometry(case)
    assert output['status']=='geometry_candidates_identified_requires_flux_validation'
    assert output['geometry']['total_plates']==32
    assert output['geometry']['total_channels']==24
    assert output['scanned_wall_faces']>=output['x_normal_candidates']
    assert output['note'].startswith('Case-only')
    artifacts=write_precheck_report(output,tmp_path/'precheck')
    assert '模型几何预检' in Path(artifacts['html_file']).read_text(encoding='utf8')


def test_case_geometry_check_honours_config_and_blocks_mismatch(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    config=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    pre=precheck_case_geometry(case,config=config)
    assert pre['status']=='geometry_candidates_identified_requires_flux_validation'
    assert len(pre['layers'])==2
    assert sum(r['wafer_count'] for layer in pre['layers'] for r in layer['rows'])==32
    config['expected']['wafer_count']=999
    failed=precheck_case_geometry(case,config=config)
    assert failed['status']=='blocked'
    assert 'expected.wafer_count' in failed['blocker']['reason']


def test_geometry_check_cli_is_separate_from_config_format_check(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    config_file=ROOT/'templates/geometry_config_example_staggered.json'
    c=subprocess.run([sys.executable,str(ROOT/'worker/af_h5_offline.py'),
        '--file',str(case),'--config',str(config_file),'--geometry-check',
        '--output-dir',str(tmp_path/'precheck')],capture_output=True,text=True,timeout=45)
    assert c.returncode==0,c.stderr[-500:]
    result=json.loads(c.stdout)
    assert result['status']=='geometry_candidates_identified_requires_flux_validation'
    assert Path(result['artifacts']['json_file']).is_file()
    assert '原生' not in result['note']  # note is an explicit no-flux disclaimer


def test_batch_exposes_two_snapshots_referencing_one_case(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    later=tmp_path/'synthetic-8-00037.dat.h5'
    with h5py.File(data,'r') as source,h5py.File(later,'w') as dest:
        for key in source.keys():source.copy(key,dest)
    inv=inventory(tmp_path)
    assert len(inv['pairs'])==2
    assert {x['snapshot_name'] for x in inv['pairs']}=={data.name,later.name}
    assert all(x['case']['path']==str(case) for x in inv['pairs'])
    assert inv['missing_data']==[]


def test_common_cut_recomputes_not_subtract_cached_reports(tmp_path):
    cfg=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    a,b=tmp_path/'before',tmp_path/'after';a.mkdir();b.mkdir()
    case_a,data_a=build_synthetic_ald_pair(a,nplates=8,staggered=True)
    case_b,data_b=build_synthetic_ald_pair(b,nplates=8,staggered=True)
    out=compare_saved_pairs_common_cut(case_a,case_b,cfg)
    assert out['status']=='recomputed_same_cut_candidate_not_final_qualified'
    assert out['comparison']['same_cut_candidate'] is True
    assert all(v['wafer_metric_difference_allowed'] for v in out['observations_by_layer'].values())
    assert all(v['cv_pct'][0]==pytest.approx(v['cv_pct'][1]) for v in out['observations_by_layer'].values())
    assert len(out['source_results']['before']['layers']['upper']['channels'])>0
    assert '真实数据已重新计算' not in render_common_cut_html(out) or out['source_results']['before']['status']=='adaptive_native_candidate_computed'
    assert '同一物理截面' not in out['limitations'][1]  # independent limitations remain explicit
    html=render_common_cut_html(out)
    assert '<details>' in html and '变化 pp' in html and 'https://' not in html
    jp=tmp_path/'saved.json';jp.write_text(json.dumps(out),encoding='utf8')
    assert '统一截面' in render_stored_json(jp)


def test_common_cut_different_measured_row_positions_prevents_wafer_delta(tmp_path):
    cfg=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    a,b=tmp_path/'before',tmp_path/'after';a.mkdir();b.mkdir()
    c1,d1=build_synthetic_ald_pair(a,nplates=8,staggered=True)
    c2,d2=build_synthetic_ald_pair(b,nplates=8,staggered=False)
    out=compare_saved_pairs_common_cut(c1,c2,cfg)
    assert out['comparison']['same_cut_candidate'] is False
    assert all(not v['wafer_metric_difference_allowed'] for v in out['observations_by_layer'].values())
    assert all(v['fraction_delta_pp'] is None for v in out['comparison']['layers'].values())


def test_common_cut_requires_explicit_physical_y_cuts(tmp_path):
    cfg=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    del cfg['layers'][0]['cut_y_m']
    with pytest.raises(ValueError,match='cut_y_m'):
        compare_saved_pairs_common_cut('not_used.cas.h5','not_used2.cas.h5',cfg)


def test_progress_scope_is_monotone(tmp_path,monkeypatch):
    from af_progress import set_scope,update
    import af_progress
    f=tmp_path/'progress.json'
    monkeypatch.setenv('AUTOFLUENT_PROGRESS_FILE',str(f))
    try:
        set_scope(0,45);update(91,'before',force=True)
        p1=json.loads(f.read_text())['percent']
        set_scope(45,90);update(17,'after',force=True)
        p2=json.loads(f.read_text())['percent']
        assert 0<=p1<=p2<90
    finally:
        set_scope(0,100)
        af_progress._LAST_PERCENT=-1
