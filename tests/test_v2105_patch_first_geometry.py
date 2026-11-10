"""V2.10.5: patch-first wall matching, outlet-aware baffle, and partial-report gates.

Synthetic geometry and native-flux regression only; NOT a real FFF acceptance.
"""
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_adaptive_geometry import (identify_from_wall_triangles,
    identify_from_configured_wall_triangles,partition_cut_faces,GeometryAmbiguous)
from af_model_config import validate_model_config
from test_adaptive_geometry import rectangles
from test_v211_overlap_and_pairing import _config,_overlapping_wafer_triangles
from test_v2104_failtrace_baffle_export import plate


def _two_tris(x,ylo,yhi,zlo,zhi):
    return np.asarray([[[x,ylo,zlo],[x,yhi,zlo],[x,yhi,zhi]],
                       [[x,ylo,zlo],[x,yhi,zhi],[x,ylo,zhi]]],float)


def test_auto_aligned_rows_discard_big_end_wall_and_tiny_support_without_expanding_z():
    good=rectangles(n=8,layers=1,wide_gap=False,shifted=False)
    end_wall=_two_tris(-.21,-.8,-.1,-.03,.8)
    support=_two_tris(.3,-.36,-.35,.20,.205)
    geo=identify_from_wall_triangles(np.concatenate((good,end_wall,support)))
    assert geo['total_plates']==16 and geo['total_channels']==14
    r0,r1=geo['layers'][0]['rows']
    assert r0['z_max_m']<r1['z_min_m']
    assert r0['z_min_m']>=.12 and r1['z_max_m']<.5
    d=geo['layers'][0]['wafer_pairing_diagnostics']['pipeline']
    assert d['global_patch_screening']['area_rejected_patch_count']>=2
    assert d['connected_patch_count']>=32
    assert d['identified_row_wafer_counts']==[8,8]
    assert d['identified_row_channel_counts']==[7,7]


def test_auto_aligned_rows_exclude_slender_panels_from_row_bounds():
    from af_wafer_patches import extract_patches
    good=rectangles(n=8,layers=1,wide_gap=False,shifted=False)
    patches=extract_patches(good,tol=1e-7,y_range=(-10,10),diagnostics={})
    p=patches[0]
    y0,y1=p['y']
    z0,z1=p['z']
    width=z1-z0
    support=_two_tris(p['x'],y0,y1,z0-width*.65,z0-width*.25)
    geo=identify_from_wall_triangles(np.concatenate((good,support)))
    assert geo['total_plates']==16 and geo['total_channels']==14
    assert geo['layers'][0]['rows'][0]['z_min_m']>=z0-1e-8


def test_shared_x_two_rows_keep_separate_patch_metadata():
    from af_wafer_patches import extract_patches
    good=rectangles(n=7,layers=1,wide_gap=False,shifted=False)
    diag={}
    patch=extract_patches(good,tol=1e-7,y_range=(-1,1),diagnostics=diag)
    assert len(patch)==28
    assert any(n==2 for n in diag['connected_patches_per_x_station'].values())
    assert all(len(p['centroid_yz_m'])==2 for p in patch)
    assert all(p['area']>0 for p in patch)


def test_staggered_overlapping_projection_each_channel_obeys_own_row_boundaries():
    cfg=validate_model_config(_config())
    geo=identify_from_configured_wall_triangles(_overlapping_wafer_triangles(),cfg)
    assert geo['total_plates']==16 and geo['total_channels']==14
    l=geo['layers'][0]
    coords=[];flow=[]
    for index,row in enumerate(l['rows']):
        for k in row['regular_gap_index']:
            coords.append([.5*(row['face_positions_hi_m'][k]+row['face_positions_lo_m'][k+1]),
                           l['y_cut_m'], -.235 if index==0 else -.165])
            flow.append(1e-8*(index+1)*(k+1))
    result=partition_cut_faces(coords,flow,l)
    assert len(result['channels'])==14
    assert [c['net_kg_s'] for c in result['channels']]==pytest.approx(flow)
    assert sum(x['face_count'] for x in result['channels'])==14
    assert result['closure_error_kg_s']==pytest.approx(0)


def test_unpaired_wall_failure_has_first_conflict_coordinate_and_rule():
    good=rectangles(n=8,layers=1,wide_gap=False,shifted=False)
    missing=good[~np.isclose(good[:,:,0].mean(axis=1),.008)]
    with pytest.raises(GeometryAmbiguous) as err:
        identify_from_wall_triangles(missing)
    assert err.value.stage
    diag=err.value.diagnostics
    assert diag.get('connected_patch_count',0)>0
    assert diag.get('paired_wafer_count',0)>=0


def test_two_valid_baffle_candidates_have_area_and_outlet_distance_diagnostics(monkeypatch):
    import af_adaptive_native as n
    tr=np.concatenate([plate(y) for y in (-.1,-.08,-.079,-.07,-.05,-.049)])
    monkeypatch.setattr(n,'_horizontal_wall_triangles',lambda m:tr)
    # One real outlet-boundary mesh face 0.086 m below zero; no internal cut
    xyz=np.asarray([[0,-.086,0],[.01,-.086,0],[0,-.086,.01]])
    mesh={'xyz':xyz,'conn':np.asarray([[0,1,2]]),
          'zones':[('outlet',0,1)],'inside':np.asarray([],int),
          'owner':np.asarray([],int),'neighbors':np.asarray([],int),
          'flux':np.asarray([])}
    r=n._baffle_plenum_candidates(mesh,np.zeros((3,3)),{})
    assert r['status']=='hole_outline_unverified'
    d=r['diagnostics']
    assert d['selection_basis']=='unambiguous_nearest_measured_outlet_boundary'
    assert d['selected_plate']['lower_y_m']==pytest.approx(-.08)
    assert all('area_ratio' in c and 'xz_coverage_overlap_ratio' in c for c in d['plate_candidates'])


def test_baffle_ambiguous_without_outlet_evidence_remains_unverified(monkeypatch):
    import af_adaptive_native as n
    tr=np.concatenate([plate(y) for y in (-.1,-.08,-.079,-.07,-.05,-.049)])
    monkeypatch.setattr(n,'_horizontal_wall_triangles',lambda m:tr)
    r=n._baffle_plenum_candidates({'xyz':np.asarray([[0,-.1,0],[.08,-.049,.08]])},None,None)
    assert r['status']=='plate_candidate_ambiguous'
    assert len(r['diagnostics']['plate_candidates'])>=2


def test_partial_wafer_failure_has_readable_report_without_invented_cv(tmp_path):
    from test_adaptive_full_cff import build_synthetic_ald_pair
    import af_adaptive_native as n
    from af_report_v3 import render_inspection_report
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    config=_config()
    config['expected']['wafer_count']=999
    from af_model_config import validate_model_config
    # Config's ranges do not match the synthetic H5: geometry blocks, but ports
    # remain measured. The emitted HTML and JSON must retain port values.
    r=n.try_adaptive_native(case,data,tmp_path/'reports',config=validate_model_config(config))
    assert r['metrics']['status']=='partial_physical_metrics'
    assert r['metrics']['port_flux']['inlet_kg_s']!=0
    assert r['metrics']['first_full_analysis_blocker']['reason']
    assert Path(r['html_file']).exists()
    html=Path(r['html_file']).read_text(encoding='utf8')
    assert '部分完成 · 晶圆几何待复核' in html
    assert '首个实际阻塞' in html


def test_tiny_coplanar_boundary_fragment_rejoined_but_disjoint_rows_not_merged():
    from af_wafer_patches import extract_patches
    original=rectangles(n=7,layers=1,wide_gap=False)
    mask=np.isclose(original[:,:,0].mean(axis=1),0)&(original[:,:,2].mean(axis=1)<.23)
    # Replace first X-side panel by a large panel and narrow disconnected
    # seam fragment, separated by only 2 mesh-coordinate tolerances.
    old=original[~mask]
    panel=np.concatenate([_two_tris(0,.04,.1399998,.12,.22),
                          _two_tris(0,.14,.15,.12,.22)])
    diag={}
    patches=extract_patches(np.concatenate([old,panel]),tol=1e-7,y_range=(0,.2),diagnostics=diag)
    assert diag['merged_boundary_fragment_count']==1
    assert len(patches)==28
    at_x0=[p for p in patches if abs(p['x'])<1e-7]
    assert len(at_x0)==2   # Not joined across the physical row separation
    assert sum(p['area'] for p in at_x0)==pytest.approx(.022,rel=1e-4)


def test_uncertain_fragment_not_merged_between_two_complete_rows():
    from af_wafer_patches import extract_patches
    good=rectangles(n=7,layers=1,wide_gap=False)
    diag={}
    patches=extract_patches(good,tol=1e-7,y_range=(-1,1),diagnostics=diag)
    assert len(patches)==28
    assert diag['merged_boundary_fragment_count']==0


def test_cli_records_file_geometry_and_export_separate_statuses(tmp_path):
    import json,subprocess
    from test_adaptive_full_cff import build_synthetic_ald_pair
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True,two_layers=True)
    config=json.loads((ROOT/'templates/geometry_config_example_staggered.json').read_text('utf-8'))
    config['expected']['wafer_count']=999
    cf=tmp_path/'config.json';cf.write_text(json.dumps(config),encoding='utf-8')
    result=subprocess.run([sys.executable,str(ROOT/'worker/af_h5_offline.py'),
                           '--file',str(data),'--config',str(cf),
                           '--output-dir',str(tmp_path/'index'),'--skip-pyfluent'],
                          capture_output=True,text=True,check=True)
    parsed=json.loads(result.stdout)
    stages=parsed['stage_statuses']
    assert stages['file_pairing']['status']=='completed'
    assert stages['wafer_geometry']['status']=='blocked'
    assert stages['report_publication']['status']=='completed'
    assert parsed['physical_analysis_status']=='partial_physical_metrics'
    saved=json.loads(Path(parsed['index_file']).read_text('utf-8'))
    assert saved['stage_statuses']['wafer_geometry']['status']=='blocked'


def test_partial_projection_support_not_a_valid_front_back_wall_pair():
    from af_wafer_patches import _footprint_similarity
    face={'area':.01,'y':(.04,.14),'z':(.12,.22)}
    # Large enough that simple area cutoff would not filter it, but projected
    # bounds differ materially; matching by overlap/smaller-length is unsafe.
    support={'area':.006,'y':(.04,.14),'z':(.16,.22)}
    overlap,ratio=_footprint_similarity(face,support)
    assert ratio==pytest.approx(.6)
    assert overlap==pytest.approx(.6)
    assert overlap<.8
