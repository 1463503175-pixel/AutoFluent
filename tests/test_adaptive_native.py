import json
from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_adaptive_native import _face_zones,_subset_field,_read_cff,_cell_centroids,_total_pressure,try_adaptive_native,UnsupportedCFF
from af_adaptive_compare import compare_native_saved
from af_adaptive_geometry import identify_from_wall_triangles


def make_pair(folder):
    h5py=pytest.importorskip('h5py')
    folder=Path(folder)
    case=folder/'small.cas.h5'; data=folder/'small.dat.h5'
    xyz=np.array([[0,0,0],[1,0,0],[0,0,1],[0,1,0],[0,-1,0]],float)
    triangles=np.array([[1,2,4],[2,3,4],[1,3,4],
                        [1,2,5],[2,3,5],[1,3,5],[1,2,3]],int)
    with h5py.File(case,'w') as h:
        m=h.require_group('meshes/1')
        m.create_dataset('nodes/coords/4',data=xyz)
        g=m.require_group('faces/nodes/1')
        g.create_dataset('nnodes',data=[3]*7)
        g.create_dataset('nodes',data=triangles.reshape(-1))
        m.create_dataset('faces/c0/1',data=[1,1,1,2,2,2,1])
        c1=m.create_dataset('faces/c1/1',data=[2])
        c1.attrs['minId']=[7];c1.attrs['maxId']=[7]
        m.create_dataset('cells/zoneTopology/maxId',data=[2])
        z=m.require_group('faces/zoneTopology')
        z.create_dataset('name',data=[b'inlet;outlet;wall;interior'])
        z.create_dataset('minId',data=[1,2,3,7]);z.create_dataset('maxId',data=[1,2,6,7])
    with h5py.File(data,'w') as h:
        h.create_dataset('settings/Case File',data=[b'small.cas.h5'])
        ds=h.create_dataset('results/1/phase-1/faces/SV_FLUX/1',data=[-2.,2.,0.,0.,0.,0.,1.])
        ds.attrs['minId']=[1];ds.attrs['maxId']=[7]
        for k,values in {'SV_P':[101,100,1,1,1,1,1],
                         'SV_DENSITY':[1.]*7,'SV_U':[0.]*7,'SV_V':[0.]*7,'SV_W':[0.]*7}.items():
            ds=h.create_dataset('results/1/phase-1/faces/'+k+'/1',data=values)
            ds.attrs['minId']=[1];ds.attrs['maxId']=[7]
    return case,data


def test_read_realistic_cff_tetra_topology_and_native_face_ids(tmp_path):
    case,data=make_pair(tmp_path)
    cf,df,mesh=_read_cff(case,data)
    try:
        assert mesh['nf']==7 and mesh['nc']==2
        np.testing.assert_array_equal(mesh['inside'],[6])
        np.testing.assert_array_equal(mesh['neighbors'],[2])
        np.testing.assert_array_equal(mesh['wall_ids'],[2,3,4,5])
        center=_cell_centroids(mesh)
        assert center.shape==(3,3)
        pressure=_total_pressure(mesh,[0],[1])
        assert pressure['status']=='computed_pressure_proxy_mach_unverified'
        assert pressure['pressure_drop_pa']==pytest.approx(1)
    finally:
        cf.close();df.close()


def test_invalid_region_cover_and_data_identity_rejected(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=make_pair(tmp_path)
    with h5py.File(case,'r+') as h:
        h['meshes/1/faces/zoneTopology/minId'][2]=2
    with pytest.raises(UnsupportedCFF,match='unique global ID partition'):
        _read_cff(case,data)
    with h5py.File(case,'r+') as h:
        h['meshes/1/faces/zoneTopology/minId'][2]=3
    with h5py.File(data,'r+') as h:
        del h['settings/Case File']
        h.create_dataset('settings/Case File',data=[b'other.cas.h5'])
    with pytest.raises(UnsupportedCFF,match='identity'):
        _read_cff(case,data)


def test_tiny_tetra_case_never_pretends_to_find_wafer_channels(tmp_path):
    case,data=make_pair(tmp_path)
    r=try_adaptive_native(case,data,tmp_path/'reports')
    assert r['status']=='partial_physical_metrics'
    assert not r['metrics'].get('layers')  # never invent wafer channels
    assert r['metrics']['port_flux']['inlet_kg_s']==pytest.approx(2.)
    # A genuine port-only/partial result is now deliberately published.
    assert Path(r['html_file']).is_file()
    assert '部分完成' in Path(r['html_file']).read_text(encoding='utf-8')


def test_native_adaptive_compare_different_geometry_counts_provisional():
    def make(n):
        rows=[{'face_positions_lo_m':[round(i*.02,4) for i in range(n)],
               'face_positions_hi_m':[round(i*.02+.001,4) for i in range(n)],
               'z_min_m':0.,'z_max_m':.1}]
        return {'schema':'autofluent.adaptive_native.v1',
                'provenance':{'case_path':str(n)+'.cas.h5'},
                'geometry':{'layers':[{'y_cut_m':.2,'rows':rows}]},
                'port_flux':{'inlet_kg_s':1e-5,'outlet_kg_s':1e-5,'mass_imbalance_pct':0.},
                'layers':{'single':{'channel_count':n-1,'cv_pct':4.+n,
                    'share_inlet_pct':70.,'partition_net_kg_s':{'wafer':7e-6,'central':1e-6,'outer_low_z':1e-6,'outer_high_z':1e-6}}},
                'pressure':{'pressure_drop_pa':.75}}
    result=compare_native_saved(make(8),make(12))
    assert result['status']=='provisional_only_not_qualified'
    assert result['layers']['single']['channel_count']==[7,11]
    assert result['geometry_signature_equal'] is False
    assert any('通道数量不同' in reason for reason in result['reasons'])


def test_offline_cli_adaptive_result_reaches_gui_json(tmp_path,monkeypatch,capsys):
    import af_h5_offline,af_native_profile,af_adaptive_native
    import h5py
    case,data=make_pair(tmp_path)
    monkeypatch.setattr(af_native_profile,'try_reference_native',lambda *a,**kw:{'status':'requires_geometry_calibration','detail':'not same geometry'})
    example={'schema':'autofluent.adaptive_native.v1',
             'layers':{'single':{'channel_count':2,'cv_pct':10.,'share_inlet_pct':60.,'net_kg_s':.5e-5}},
             'port_flux':{'mass_imbalance_pct':.1},'pressure':{'status':'unavailable'}}
    out=tmp_path/'native';out.mkdir()
    for ext in ('json','html','csv'):(out/('test.'+ext)).write_text('{}')
    monkeypatch.setattr(af_adaptive_native,'try_adaptive_native',lambda *a,**kw:{
        'status':'adaptive_native_candidate_computed','metrics':example,
        'json_file':str(out/'test.json'),'html_file':str(out/'test.html'),'csv_file':str(out/'test.csv')})
    monkeypatch.setattr(af_h5_offline,'inspect_case_data',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Full independent H5 analysis not needed after adaptive result')))
    monkeypatch.setattr(sys,'argv',['af_h5_offline.py','--file',str(case),'--output-dir',str(tmp_path/'indexes')])
    af_h5_offline.main()
    record=json.loads(capsys.readouterr().out)
    assert record['physical_analysis_status']=='adaptive_native_candidate_computed'
    assert record['derived_metrics']['layers']['single']['cv_pct']==10.
    assert record['native_csv'].endswith('test.csv')
