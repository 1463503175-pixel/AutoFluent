"""Regression guards for native CFF zone identity and signed bypass flux.

These tests use small in-memory geometry or synthetic CFF and are NOT real
Fluent saved-case acceptance evidence.
"""
from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_adaptive_geometry import identify_from_wall_triangles,partition_cut_faces
from af_adaptive_native import _read_cff,UnsupportedCFF
from test_adaptive_geometry import rectangles
from test_adaptive_native import make_pair


def test_reject_falsely_named_internal_wall_faces(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=make_pair(tmp_path)
    with h5py.File(case,'r+') as cf:
        cf['meshes/1/faces/zoneTopology/name'][...]=[b'inlet;outlet;wall;wall-shadow']
    with pytest.raises(UnsupportedCFF,match='internal c1 faces'):
        _read_cff(case,data)


def test_reject_c0_dataset_with_shifted_face_ids(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=make_pair(tmp_path)
    with h5py.File(case,'r+') as cf:
        cf['meshes/1/faces/c0/1'].attrs['minId']=[2]
        cf['meshes/1/faces/c0/1'].attrs['maxId']=[8]
    with pytest.raises(UnsupportedCFF,match='faces/c0: missing block IDs|c0 block face IDs'):
        _read_cff(case,data)


def test_backflow_not_hidden_by_zero_net_bypass():
    geo=identify_from_wall_triangles(rectangles(n=6,layers=1))
    lay=geo['layers'][0]
    row0,row1=lay['rows']
    midz=(row0['z_max_m']+row1['z_min_m'])/2
    # Two opposing fluxes occupy the same central bypass (net zero), so a
    # net-only metric would incorrectly suggest no bypass transport.
    points=[[.12,lay['y_cut_m'],midz],[.14,lay['y_cut_m'],midz]]
    flows=[4e-7,-4e-7]
    for row in lay['rows']:
        for i in row['regular_gap_index']:
            points.append([.5*(row['face_positions_hi_m'][i]+row['face_positions_lo_m'][i+1]),
                           lay['y_cut_m'],.5*(row['z_min_m']+row['z_max_m'])])
            flows.append(1e-7)
    data=partition_cut_faces(points,flows,lay)
    central=data['parts_directional_kg_s']['central']
    assert central['positive_kg_s']==pytest.approx(4e-7)
    assert central['negative_magnitude_kg_s']==pytest.approx(4e-7)
    assert central['net_kg_s']==pytest.approx(0)
    assert data['bypass_directional_kg_s']['central_z_between_rows']['positive_kg_s']==pytest.approx(4e-7)
    assert sum(v['positive_kg_s'] for v in data['parts_directional_kg_s'].values())==pytest.approx(data['positive_kg_s'])
    assert sum(v['negative_magnitude_kg_s'] for v in data['parts_directional_kg_s'].values())==pytest.approx(data['negative_magnitude_kg_s'])
    assert data['parts_directional_kg_s']['wafer']['face_count']==data['channel_count']


def test_model_compare_preserves_backflow_and_missing_layer_warning():
    from af_adaptive_compare import compare_native_saved
    def result(layers,reverse):
        bylayer={}
        for name in layers:
            bylayer[name]={
                'channel_count':12,'cv_pct':2.,'share_inlet_pct':70.,
                'partition_net_kg_s':{'wafer':7e-6,'central':0.,'outer_low_z':2e-6,'outer_high_z':1e-6},
                'partition_directional_kg_s':{
                    'wafer':{'positive_kg_s':7e-6,'negative_magnitude_kg_s':0},
                    'central':{'positive_kg_s':reverse,'negative_magnitude_kg_s':reverse},
                    'outer_low_z':{'positive_kg_s':2e-6,'negative_magnitude_kg_s':0},
                    'outer_high_z':{'positive_kg_s':1e-6,'negative_magnitude_kg_s':0}}}
        return {'schema':'autofluent.adaptive_native.v1',
                'provenance':{'case_path':'saved.cas.h5'},
                'geometry':{'layers':[]},
                'port_flux':{'inlet_kg_s':1e-5,'outlet_kg_s':1e-5,'mass_imbalance_pct':0},
                'layers':bylayer,'pressure':{'status':'unavailable'}}
    a=result(['lower','upper'],1e-6)
    b=result(['lower'],2e-6)
    comp=compare_native_saved(a,b)
    assert comp['layers']['lower']['bypass_fraction_pct']['central']==pytest.approx([0,0])
    assert comp['layers']['lower']['bypass_reverse_fraction_pct']['central']==pytest.approx([10,20])
    assert any('层级集合不同' in r for r in comp['reasons'])


def test_pressure_proxy_refuses_high_mach_instead_of_reporting_false_drop(tmp_path):
    h5py=pytest.importorskip('h5py')
    from af_adaptive_native import _total_pressure
    case,data=make_pair(tmp_path)
    with h5py.File(data,'r+') as h:
        ds=h.create_dataset('results/1/phase-1/faces/SV_T/1',data=np.full(7,300.))
        ds.attrs['minId']=[1];ds.attrs['maxId']=[7]
        h['results/1/phase-1/faces/SV_U/1'][...] = np.full(7,400.)
    cf,df,mesh=_read_cff(case,data)
    try:
        p=_total_pressure(mesh,np.asarray([0]),np.asarray([1]))
        assert p['status']=='unavailable_high_mach_proxy'
        assert p['mach_squared_proxy_n2']>.01
        assert 'pressure_drop_pa' not in p
    finally:
        cf.close();df.close()


def test_pressure_proxy_labels_nitrogen_material_assumption(tmp_path):
    h5py=pytest.importorskip('h5py')
    from af_adaptive_native import _total_pressure
    case,data=make_pair(tmp_path)
    with h5py.File(data,'r+') as h:
        ds=h.create_dataset('results/1/phase-1/faces/SV_T/1',data=np.full(7,300.))
        ds.attrs['minId']=[1];ds.attrs['maxId']=[7]
    cf,df,mesh=_read_cff(case,data)
    try:
        p=_total_pressure(mesh,np.asarray([0]),np.asarray([1]))
        assert p['status']=='computed_pressure_proxy_n2_mach_screened'
        assert p['pressure_drop_pa']==pytest.approx(1)
        assert 'actual material not verified' in p['mach_screening_note']
    finally:
        cf.close();df.close()


def test_spotcheck_rejects_four_faces_without_closed_tetra(tmp_path):
    h5py=pytest.importorskip('h5py')
    from af_adaptive_native import _tetra_connectivity_spotcheck,_cell_centroids
    case,data=make_pair(tmp_path)
    cf,df,mesh=_read_cff(case,data)
    try:
        ok=_tetra_connectivity_spotcheck(mesh)
        assert ok['sampled_cells']==2
        # Keep each cell's DEGREE at four but duplicate one triangle and break
        # the set of 4 tetra faces. Degree-only validation misses this.
        mesh['conn'][6]=mesh['conn'][0]
        with pytest.raises(UnsupportedCFF,match='do not close a tetrahedron|four faces on four distinct vertices'):
            _tetra_connectivity_spotcheck(mesh)
    finally:
        cf.close();df.close()


def test_z_between_rows_outside_array_x_is_outer_end_bypass():
    """Regression for the physically incorrect V2.5 Z-only central region."""
    layer=identify_from_wall_triangles(rectangles(n=8,layers=1))['layers'][0]
    row0,row1=layer['rows']; z=(row0['z_max_m']+row1['z_min_m'])/2
    xmax=max(r['face_positions_hi_m'][-1] for r in layer['rows'])
    points=[[xmax+.12,layer['y_cut_m'],z],
            [xmax+.14,layer['y_cut_m'],z],
            [.5*(max(r['face_positions_lo_m'][0] for r in layer['rows'])+
                 min(r['face_positions_hi_m'][-1] for r in layer['rows'])),layer['y_cut_m'],z]]
    flux=[1e-6,-2e-7,3e-7]
    for r in layer['rows']:
        for i in r['regular_gap_index']:
            points.append([.5*(r['face_positions_hi_m'][i]+r['face_positions_lo_m'][i+1]),
                           layer['y_cut_m'],.5*(r['z_min_m']+r['z_max_m'])])
            flux.append(1e-7)
    result=partition_cut_faces(points,flux,layer)
    assert result['central_z_between_rows_kg_s']==pytest.approx(3e-7)
    assert result['outer_x_end_subset_kg_s']==pytest.approx(8e-7)
    assert result['bypass_directional_kg_s']['outer_x_end_subset']['positive_kg_s']==pytest.approx(1e-6)
    assert result['bypass_directional_kg_s']['outer_x_end_subset']['negative_magnitude_kg_s']==pytest.approx(2e-7)
    assert result['closure_error_kg_s']==pytest.approx(0.)


def test_staggered_z_middle_only_in_overlapping_x_span():
    layer=identify_from_wall_triangles(rectangles(n=6,wide_gap=False,shifted=True,layers=1))['layers'][0]
    a,b=layer['rows'];z=(a['z_max_m']+b['z_min_m'])/2
    common_lo=max(a['face_positions_lo_m'][0],b['face_positions_lo_m'][0]);common_hi=min(a['face_positions_hi_m'][-1],b['face_positions_hi_m'][-1])
    assert common_lo<common_hi
    # Between the rows but outside one row's X span: outer, not central.
    x_outside=common_lo-.002
    assert x_outside>=min(a['face_positions_lo_m'][0],b['face_positions_lo_m'][0])
    points=[[x_outside,layer['y_cut_m'],z],[(common_lo+common_hi)/2,layer['y_cut_m'],z]]
    flows=[4e-7,3e-7]
    for r in layer['rows']:
        for i in r['regular_gap_index']:
            points.append([.5*(r['face_positions_hi_m'][i]+r['face_positions_lo_m'][i+1]),
                           layer['y_cut_m'],.5*(r['z_min_m']+r['z_max_m'])])
            flows.append(1e-7)
    ans=partition_cut_faces(points,flows,layer)
    assert ans['central_z_between_rows_kg_s']==pytest.approx(3e-7)
    assert ans['parts_net_kg_s']['central']==pytest.approx(3e-7)
    assert ans['parts_net_kg_s']['outer_low_z']+ans['parts_net_kg_s']['outer_high_z']==pytest.approx(4e-7)
