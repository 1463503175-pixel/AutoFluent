"""Synthetic-only evidence: never substitutes for a real Fluent 2.0 acceptance run."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_model_config import ConfigurationError, load_model_config,validate_model_config
from af_adaptive_native import (_read_native_full_flux, _select_face_results,
                                UnsupportedCFF, analyze_saved_pair)
from test_adaptive_full_cff import build_synthetic_ald_pair


@pytest.fixture
def sample_config():
    return load_model_config(ROOT/'templates/geometry_config_example_staggered.json')


def test_config_is_prechecked_without_h5_reads(sample_config,tmp_path):
    cmd=[sys.executable,str(ROOT/'worker/af_h5_offline.py'),
         '--config',str(ROOT/'templates/geometry_config_example_staggered.json'),
         '--check']
    ran=subprocess.run(cmd,capture_output=True,text=True,check=True)
    result=json.loads(ran.stdout)
    assert result['status']=='config_validated_before_large_h5_reads'
    assert result['configured_rows']==4


@pytest.mark.parametrize('mutation',[('units','mm'),('schema','other')])
def test_config_rejects_invalid_top_level(sample_config,mutation):
    sample_config[mutation[0]]=mutation[1]
    with pytest.raises(ConfigurationError):validate_model_config(sample_config)


def test_config_rejects_overlap_and_port_conflict(sample_config):
    sample_config['layers'][0]['rows'][1]['z_range_m']=[-0.22,0.10]
    with pytest.raises(ConfigurationError,match='Overlapping Z'):validate_model_config(sample_config)
    sample_config['layers'][0]['rows'][1]['z_range_m']=[-0.021,0.101]
    sample_config['ports']['outlet_zones']=['inlet']
    with pytest.raises(ConfigurationError,match='both inlet and outlet'):validate_model_config(sample_config)


def test_configured_real_staggered_geometry_and_flux(sample_config,tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True,two_layers=True)
    auto=analyze_saved_pair(case,data)
    manual=analyze_saved_pair(case,data,config=sample_config)
    assert manual['provenance']['config_mode']=='explicit_verified_real_geometry'
    assert manual['geometry']['total_plates']==32
    assert manual['geometry']['total_channels']==24
    assert manual['port_flux']['mass_imbalance_pct']==0
    for lname,old in [('lower',auto['layers']['lower']),('upper',auto['layers']['upper'])]:
        new=manual['layers'][lname]
        assert new['channel_count']==old['channel_count']
        assert new['net_kg_s']==pytest.approx(old['net_kg_s'],rel=1e-10)
        assert new['cv_pct']==pytest.approx(old['cv_pct'],rel=1e-10)
        assert len(set(ch['channel_id'] for ch in new['channels']))==len(new['channels'])
        assert all(np.isfinite(ch['x_center_m']) for ch in new['channels'])
    sample_config['expected']['wafer_count']=30
    # Physical module isolation: a count mismatch blocks wafer metrics, not real ports.
    partial=analyze_saved_pair(case,data,config=sample_config)
    assert partial['status']=='partial_physical_metrics'
    assert 'expected.wafer_count' in partial['first_full_analysis_blocker']['reason']
    assert partial['port_flux']['inlet_kg_s']>0


def test_segmented_sv_flux_and_corrupt_ranges(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=False,two_layers=True)
    baseline=analyze_saved_pair(case,data)
    with h5py.File(data,'r+') as f:
        path='results/1/phase-1/faces/SV_FLUX'
        ds=f[path+'/1'];array=ds[:];n=len(array)
        del f[path]
        group=f.require_group(path)
        cut=n//2
        for label,offset,values in [('1',0,array[:cut]),('2',cut,array[cut:])]:
            sub=group.create_dataset(label,data=values)
            sub.attrs['minId']=[offset+1]
            sub.attrs['maxId']=[offset+len(values)]
    segmented=analyze_saved_pair(case,data)
    assert len(segmented['provenance']['native_flux_blocks'])==2
    assert segmented['port_flux']['inlet_kg_s']==pytest.approx(baseline['port_flux']['inlet_kg_s'])
    assert segmented['layers']['upper']['net_kg_s']==pytest.approx(baseline['layers']['upper']['net_kg_s'])
    with h5py.File(data,'r+') as f:
        f['results/1/phase-1/faces/SV_FLUX/2'].attrs['minId']=[cut]
    with pytest.raises(UnsupportedCFF,match='No full original SV_FLUX'):
        analyze_saved_pair(case,data)


def test_multiple_valid_result_groups_are_ambiguous(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=False,two_layers=True)
    with h5py.File(data,'r+') as f:
        f.copy(f['results/1'],f['results'],name='2')
    with pytest.raises(UnsupportedCFF,match='Multiple complete SV_FLUX'):
        analyze_saved_pair(case,data)


def test_explicit_config_never_switches_to_11_9_reference(tmp_path,sample_config):
    folder=tmp_path/'11.9';folder.mkdir()
    case,data=build_synthetic_ald_pair(folder,nplates=8,staggered=True,two_layers=True)
    config_path=tmp_path/'cfg.json';config_path.write_text(json.dumps(sample_config))
    output=tmp_path/'reports';output.mkdir()
    cmd=[sys.executable,str(ROOT/'worker/af_h5_offline.py'), '--file',str(case),
         '--config',str(config_path),'--output-dir',str(output),'--skip-pyfluent']
    completed=subprocess.run(cmd,capture_output=True,text=True,check=True)
    decoded=json.loads(completed.stdout)
    assert decoded['config_mode']=='explicit_only'
    assert decoded['physical_analysis_status']=='adaptive_native_candidate_computed'
    assert decoded['native_direct']['metrics']['geometry']['total_plates']==32
    assert not decoded.get('native_reference_attempt')


def test_report_template_uses_only_structured_real_calculation(sample_config,tmp_path):
    from af_report_v3 import render_native_report
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True,two_layers=True)
    metrics=analyze_saved_pair(case,data,config=sample_config)
    html=render_native_report(metrics,synthetic_demo=True)
    assert '合成数据测试专用' in html
    assert '2.0 加深集气腔' not in html
    assert '94.62%' not in html  # historic template, not current computed metric
    assert 'https://' not in html and '<script' not in html
    assert '下一轮仿真怎么安排' in html
    assert '中央 X/Z 子旁路正反向流量' in html
    assert '真实 X 坐标' in html
    assert '收敛' in html
    metrics['pressure']={'status':'unavailable','reason':'missing SAVED pressure field'}
    html2=render_native_report(metrics)
    assert 'missing SAVED pressure field' in html2
    assert '未取得' in html2


def test_different_geometry_blocks_unqualified_deltas(sample_config,tmp_path):
    from af_adaptive_compare import compare_native_saved
    a_dir=tmp_path/'a';b_dir=tmp_path/'b';a_dir.mkdir();b_dir.mkdir()
    ac,ad=build_synthetic_ald_pair(a_dir,nplates=8,staggered=True,two_layers=True)
    bc,bd=build_synthetic_ald_pair(b_dir,nplates=8,staggered=False,two_layers=True)
    a=analyze_saved_pair(ac,ad,config=sample_config)
    b=analyze_saved_pair(bc,bd)
    comparison=compare_native_saved(a,b)
    assert comparison['geometry_signature_equal'] is False
    assert comparison['same_cut_candidate'] is False
    assert comparison['layers']['lower']['cv_delta_pp'] is None
    assert comparison['layers']['lower']['fraction_delta_pp'] is None
    # Port-pressure comparison is independent of a changed wafer cut.
    assert comparison['pressure_drop_delta_pa']==pytest.approx(0.)
    assert comparison['pressure_comparison_scope']=='provisional_port_only'
