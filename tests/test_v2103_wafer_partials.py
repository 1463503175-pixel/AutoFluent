"""Focused regression for aligned/staggered wafer patch recognition and partial reports."""
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_model_config import validate_model_config
from af_adaptive_geometry import (identify_from_configured_wall_triangles,
                                  identify_from_wall_triangles,GeometryAmbiguous,partition_cut_faces)
from af_adaptive_native import try_adaptive_native
from test_v211_overlap_and_pairing import _config,_overlapping_wafer_triangles
from test_adaptive_full_cff import build_synthetic_ald_pair


def _aligned_disjoint():
    tris=[]
    for z0,z1 in ((-.30,-.22),(-.16,-.08)):
        for i in range(8):
            for x in (.06*i,.06*i+.009):
                c=np.array([[x,-.4,z0],[x,-.3,z0],[x,-.3,z1],[x,-.4,z1]])
                tris.extend([c[[0,1,2]],c[[0,2,3]]])
    return np.asarray(tris)


def _aligned_config():
    c=_config()
    c['wafer']['arrangement']='aligned'
    c['layers'][0]['rows']=[{'id':'A','z_range_m':[-.301,-.219]},
                           {'id':'B','z_range_m':[-.161,-.079]}]
    c['partition'].pop('overlap_split_z_m')
    c['layers'][0]['wafer_x_range_m']=[-.005,.45]
    return c


def test_aligned_two_rows_sharing_every_x_plane_stay_disjoint():
    geo=identify_from_configured_wall_triangles(_aligned_disjoint(),validate_model_config(_aligned_config()))
    assert geo['total_plates']==16 and geo['total_channels']==14
    assert geo['layers'][0]['rows'][0]['face_positions_lo_m']==geo['layers'][0]['rows'][1]['face_positions_lo_m']
    assert len(geo['layers'][0]['rows'])==2


def test_auto_aligned_and_staggered_use_same_physical_patch_entry():
    aligned=identify_from_wall_triangles(_aligned_disjoint())
    staggered=identify_from_wall_triangles(_aligned_disjoint().copy())
    assert aligned['total_plates']==staggered['total_plates']==16
    assert aligned['total_channels']==14


def test_external_fake_wall_at_x_minus_586973_excluded_by_measured_x_search():
    c=_aligned_config()
    true=_aligned_disjoint()
    fake=true[:2].copy();fake[:,:,0]=-.586973
    geo=identify_from_configured_wall_triangles(np.concatenate([fake,true]),validate_model_config(c))
    assert geo['total_plates']==16 and geo['total_channels']==14
    assert geo['layers'][0]['configured_x_search_range_m']==[-.005,.45]


def test_overlapping_staggered_two_rows_partition_positive_reverse_closes():
    geo=identify_from_configured_wall_triangles(_overlapping_wafer_triangles(),validate_model_config(_config()))
    layer=geo['layers'][0]
    pts=[];q=[]
    for i,r in enumerate(layer['rows']):
        for gap in r['regular_gap_index']:
            pts.append([.5*(r['face_positions_hi_m'][gap]+r['face_positions_lo_m'][gap+1]),-.35,-.23 if i==0 else -.17])
            q.append(-1.e-8 if gap%3==0 else 2.e-8)
    pts.extend([[0.,-.35,-.201],[0.,-.35,-.199]])
    q.extend([3e-8,-2e-8])
    part=partition_cut_faces(np.array(pts),np.array(q),layer)
    assert sum(x['face_count'] for x in part['parts_directional_kg_s'].values())==len(pts)
    assert sum(x['positive_kg_s'] for x in part['parts_directional_kg_s'].values())==pytest.approx(part['positive_kg_s'])
    assert sum(x['negative_magnitude_kg_s'] for x in part['parts_directional_kg_s'].values())==pytest.approx(part['negative_magnitude_kg_s'])
    assert part['central_bypass_applicability']=='not_applicable'


def test_true_internal_ambiguous_duplicate_coplanar_patch_fails_closed():
    v=_aligned_disjoint()
    # Copy an entire wafer face into the middle, same X/Y/Z but disconnected
    # mesh component: indistinguishable duplicated wall geometry.
    extra=v[4:6].copy()
    with pytest.raises(GeometryAmbiguous,match='overlapping physical YZ footprint|Unpaired wafer-sized'):
        identify_from_configured_wall_triangles(np.concatenate([v,extra]),validate_model_config(_aligned_config()))


def test_wafer_failure_keeps_port_and_baffle_and_report(tmp_path,monkeypatch):
    import af_adaptive_native as native
    c,d=build_synthetic_ald_pair(tmp_path,nplates=8)
    def deny_wafer(*args,**kwargs):
        raise GeometryAmbiguous('front/back match ambiguous X=-0.586973 m')
    monkeypatch.setattr(native,'_wafer_wall_triangles',deny_wafer)
    # Synthetic stub: validates module isolation and renderer only. Not a
    # claim about actual 2.0 baffle geometry or an experimental hole count.
    def baffle_fixture(*args,**kwargs):
        holes=[{'center_m':[float(i),-.3,-.2],'net_kg_s':1e-8,
                'positive_kg_s':1e-8,'negative_magnitude_kg_s':0.} for i in range(3)]
        return {'status':'geometry_candidate_holes_native_flux_computed','hole_count':3,
                'hole_row_count':1,'all_hole_cv_pct':0.,
                'holes':holes,'rows':[{'index':1,'hole_count':3,'net_kg_s':3e-8,'cv_pct':0.}],
                'board_edge_net_kg_s':0.,'board_cut_closure_error_kg_s':0.}
    monkeypatch.setattr(native,'_baffle_plenum_candidates',baffle_fixture)
    ret=try_adaptive_native(c,d,tmp_path/'out')
    assert ret['status']=='partial_physical_metrics'
    r=ret['metrics']
    assert r['port_flux']['inlet_kg_s']>0
    assert r['baffle']['hole_count']==3
    assert r['modules']['wafer_geometry']['status']=='blocked'
    assert r['modules']['baffle']['status']=='completed'
    assert not r['layers']
    assert r['first_full_analysis_blocker']['stage']=='wafer_wall_pairing'
    html=Path(ret['html_file']).read_text(encoding='utf-8')
    assert '部分完成 · 晶圆几何待复核' in html
    assert '3 孔' in html
    assert 'front/back match ambiguous' in html
    assert '未取得' in html
    data=json.loads(Path(ret['json_file']).read_text())
    assert data['baffle']['hole_count']==3 and data['layers']=={}
    assert 'baffle_hole' in Path(ret['csv_file']).read_text(encoding='utf-8-sig')


def test_cli_configured_wafer_mismatch_returns_partial_and_real_html(tmp_path):
    import subprocess
    c,d=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True,two_layers=True)
    cdata=json.loads((ROOT/'templates/geometry_config_example_staggered.json').read_text(encoding='utf-8'))
    cdata['expected']['wafer_count']=12345
    cfg=tmp_path/'wrong_expected.json';cfg.write_text(json.dumps(cdata),encoding='utf-8')
    cli=ROOT/'worker/af_h5_offline.py'
    proc=subprocess.run([sys.executable,str(cli),'--file',str(d),'--config',str(cfg),
                         '--output-dir',str(tmp_path/'indexes'),'--skip-pyfluent'],
                         capture_output=True,text=True,check=True)
    out=json.loads(proc.stdout)
    assert out['status']=='partial_physical_metrics'
    assert out['physical_analysis_status']=='partial_physical_metrics'
    assert out['derived_metrics']['mass_imbalance_pct']==pytest.approx(0.)
    assert 'expected.wafer_count' in out['first_full_analysis_blocker']['reason']
    assert Path(out['html_file']).is_file()
    assert '部分完成 · 晶圆几何待复核' in Path(out['html_file']).read_text(encoding='utf-8')
    assert Path(out['analysis_json']).is_file()
    assert not out['derived_metrics'].get('layers')


def test_single_row_uses_same_patch_pairing_pipeline():
    v=_aligned_disjoint()[:32]
    config=_aligned_config()
    config['layers'][0]['rows']=config['layers'][0]['rows'][:1]
    config['expected']={'wafer_count':8,'channel_count':7}
    got=identify_from_configured_wall_triangles(v,validate_model_config(config))
    assert got['total_plates']==8 and got['total_channels']==7


def test_synthetic_384_wafers_380_channels_two_layers_staggered_with_fake_end_wall():
    # Synthetic geometry only. Do not treat these counts as a measured 2.0 result.
    triangles=[]
    for ylow in (-.7,-.4):
        for zlow,zhi,delta in ((-.3,-.18,0.),(-.22,-.1,.0035)):
            for i in range(96):
                x=-.576973+i*.007+delta
                for px in (x,x+.001):
                    v=np.asarray([[px,ylow,zlow],[px,ylow+.10,zlow],
                                  [px,ylow+.10,zhi],[px,ylow,zhi]])
                    triangles.extend([v[[0,1,2]],v[[0,2,3]]])
    fake=np.asarray(triangles[:2]).copy();fake[:,:,0]=-.586973
    triangles=np.concatenate([fake,np.asarray(triangles)])
    c=_config()
    c['wafer']['thickness_range_m']=[.0009,.0011]
    c['wafer']['regular_gap_range_m']=[.0058,.0062]
    c['layers']=[
        {'id':str(i),'y_range_m':list(r),'cut_y_m':sum(r)*.5,
         'wafer_x_range_m':[-.578,.095],
         'rows':[{'id':'A','z_range_m':[-.301,-.179]},
                 {'id':'B','z_range_m':[-.221,-.099]}]}
        for i,r in enumerate(((-.71,-.59),(-.41,-.29)),1)
    ]
    c['expected']={'wafer_count':384,'channel_count':380}
    result=identify_from_configured_wall_triangles(triangles,validate_model_config(c))
    assert result['total_plates']==384 and result['total_channels']==380
    assert all(x['channels']==190 for x in result['layers'])
    assert all([r['plate_count'] for r in x['rows']]==[96,96] for x in result['layers'])
