"""Actual synthetic H5 and geometry counterexamples; no 2.0 acceptance claim."""
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_pairing import resolve_saved_pair
from af_model_config import validate_model_config,ConfigurationError
from af_adaptive_geometry import identify_from_configured_wall_triangles,partition_cut_faces,GeometryAmbiguous


def _ref_data(path, ref):
    h5py=pytest.importorskip('h5py')
    with h5py.File(path,'w') as f:
        f.create_dataset('settings/Case File',data=np.asarray([ref.encode('utf-8')]))


def test_data_reference_with_iteration_suffix_and_case_snapshot_choice(tmp_path):
    case=tmp_path/'FFF-8.cas.h5'
    case.write_bytes(b'case')
    first=tmp_path/'FFF-8-00037.dat.h5'
    _ref_data(first,r'E:\run\FFF-8.cas.h5')
    assert resolve_saved_pair(first)==(case,first)
    assert resolve_saved_pair(case)==(case,first)
    second=tmp_path/'FFF-8-00038.dat.h5'
    _ref_data(second,'FFF-8.cas.h5')
    with pytest.raises(ValueError,match='Multiple Data snapshots'):
        resolve_saved_pair(case)
    assert resolve_saved_pair(case,explicit_data=second)==(case,second)
    wrong=tmp_path/'another.cas.h5';wrong.write_bytes(b'case')
    with pytest.raises(ValueError,match='disagrees'):
        resolve_saved_pair(first,explicit_case=wrong)


def _overlapping_wafer_triangles():
    tris=[]
    for row,(zlo,zhi,xoffset) in enumerate([(-.300,-.180,0.),(-.220,-.100,.030)]):
        for i in range(8):
            x=.06*i+xoffset
            for plane in (x,x+.009):
                a=np.array([plane,-.4,zlo]);b=np.array([plane,-.30,zlo])
                c=np.array([plane,-.30,zhi]);d=np.array([plane,-.4,zhi])
                tris.extend([[a,b,c],[a,c,d]])
    return np.asarray(tris)


def _config():
    return {'schema':'autofluent.geometry_config.v1','units':'m',
            'axis':{'wafer_normal':'x','flow':'y','row':'z','flow_sign':'negative'},
            'ports':{'inlet_zones':['inlet'],'outlet_zones':['outlet']},
            'wafer':{'arrangement':'staggered','thickness_range_m':[.008,.010],
                     'regular_gap_range_m':[.045,.055]},
            'layers':[{'id':'L','y_range_m':[-.42,-.28],'cut_y_m':-.35,
                       'rows':[{'id':'A','z_range_m':[-.301,-.179]},
                               {'id':'B','z_range_m':[-.221,-.099]}]}],
            'partition':{'central_bypass':'absent','overlap_split_z_m':-.20},
            'expected':{'wafer_count':16,'channel_count':14},
            'resources':{'memory_budget_mb':6144}}


def test_overlapping_staggered_geometry_owned_before_split_and_partition_closes():
    cfg=validate_model_config(_config())
    geometry=identify_from_configured_wall_triangles(_overlapping_wafer_triangles(),cfg)
    assert geometry['total_plates']==16 and geometry['total_channels']==14
    layer=geometry['layers'][0]
    assert layer['overlap_split_z_m']==pytest.approx(-.2)
    assert layer['rows'][0]['z_max_m']>layer['rows'][1]['z_min_m']
    pts=[]
    for i,row in enumerate(layer['rows']):
        z=-.23 if i==0 else -.17
        for gap in row['regular_gap_index']:
            x=(row['face_positions_hi_m'][gap]+row['face_positions_lo_m'][gap+1])/2
            pts.append([x,-.35,z])
    # Borderline faces assign to only one side of the configured split.
    pts.extend([[.1,-.35,-.2000001],[.1,-.35,-.1999999]])
    vals=np.ones(len(pts))*1e-8
    r=partition_cut_faces(pts,vals,layer)
    assert r['channel_count']==14
    assert r['central_bypass_applicability']=='not_applicable'
    assert r['parts_net_kg_s']['central']==0
    assert sum(r['parts_net_kg_s'].values())==pytest.approx(r['whole_cut_kg_s'])
    assert sum(v['face_count'] for v in r['parts_directional_kg_s'].values())==len(pts)


def test_overlapping_without_split_is_rejected():
    c=_config();c['partition'].pop('overlap_split_z_m')
    with pytest.raises(ConfigurationError,match='Overlapping Z'):
        validate_model_config(c)


def test_coincident_x_stations_from_two_rows_rejected_not_double_counted():
    c=_config()
    # Two aligned row sets cannot be attributed with X station ownership.
    v=_overlapping_wafer_triangles();v[32:,:,0]-=.03
    with pytest.raises(GeometryAmbiguous,match='both overlapping rows'):
        identify_from_configured_wall_triangles(v,validate_model_config(c))
