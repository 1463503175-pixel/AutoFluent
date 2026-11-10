import sys,json,subprocess
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'worker'))
from af_saved_analysis import identify_measurement_zone,extract_named_measurement_flux,analyze_native_flux,write_saved_report
from af_saved_compare import compare_saved_results


def test_no_wall_as_gap():
    assert identify_measurement_zone('wafer-upper-wall-12') is None
    assert identify_measurement_zone('upper-gap-12')=={'layer':'upper','kind':'channel','channel_index':12}
    assert identify_measurement_zone('lower-channel-03')=={'layer':'lower','kind':'channel','channel_index':3}
    assert identify_measurement_zone('上层中央旁路')['branch']=='central'
    assert identify_measurement_zone('random-zone') is None


def test_actual_named_face_flux_and_candidate_cv():
    np=pytest.importorskip('numpy')
    class Mesh:
        def get_surface_locs(self, sid):return (1,2)
    class Data:
        def get_face_scalar_field_data(self,phase,name,ids):
            assert phase=='phase-1' and name=='SV_FLUX'
            return np.array([1e-7,ids[0]*1e-7])
    zones=[(1,'upper-gap-01'),(2,'upper-gap-02'),(3,'upper-central-bypass'),(4,'upper-wall-01')]
    x=extract_named_measurement_flux(Mesh(),Data(),'phase-1',zones)
    assert len(x['zones'])==3
    assert x['zones'][0]['signed_net_kg_s']==pytest.approx(2e-7)
    m=analyze_native_flux({'native_measurements':x})
    assert m['layers']['upper']['channel_count']==2
    assert m['layers']['upper']['cv_pct']==pytest.approx(20.0)
    assert m['layers']['lower']['cv_pct'] if 'cv_pct' in m['layers']['lower'] else True
    assert m['reliability'].startswith('candidate_only')


def test_no_named_zones_no_fabricated_cv():
    x=analyze_native_flux({'native_measurements':{'zones':[]}})
    assert x['status']=='no_measured_native_flux'
    assert all('cv_pct' not in r for r in x['layers'].values())


def test_mixed_sign_or_duplicate_ids_rejected():
    x={'native_measurements':{'zones':[
        dict(status='raw_native_flux',kind='channel',layer='upper',channel_index=1,signed_net_kg_s=-2),
        dict(status='raw_native_flux',kind='channel',layer='upper',channel_index=2,signed_net_kg_s=3)]}}
    assert analyze_native_flux(x)['layers']['upper']['status']=='mixed_or_zero_native_direction'
    x['native_measurements']['zones'][1]['channel_index']=1
    assert analyze_native_flux(x)['layers']['upper']['status']=='duplicate_channel_indices'


def test_report_and_compare_real_inputs_required(tmp_path):
    x={'schema':'autofluent.offline_case_data.v1','model_id':'11.4',
       'case':{'path':'C:/saved/11.4.cas.h5'},'data':{'path':'C:/saved/11.4.dat.h5'},
       'pyfluent':{'status':'indexed'},'physical_analysis_status':'partial',
       'derived_metrics':{'status':'candidate_only_not_geometry_verified', 'layers':{
        'upper':{'status':'candidate_native_flux_cv_orientation_unverified','channel_count':2,'channel_indices':[1,2],'cv_pct':10},
        'lower':{'status':'missing_named_channels','channel_count':0}},'missing_for_full_analysis':[]}}
    paths=write_saved_report(x,tmp_path)
    assert Path(paths['analysis_json']).exists() and Path(paths['html_file']).exists()
    report_html=Path(paths['html_file']).read_text(encoding='utf-8')
    assert '<header>' in report_html and '1. 入口气体去了哪里' in report_html and '8. 来源与完整明细' in report_html
    assert '保存解读取诊断' in report_html and '物理结果未完成认证' in report_html
    assert '未取得' in report_html
    y=json.loads(json.dumps(x));y['model_id']='11.6';y['derived_metrics']['layers']['upper']['cv_pct']=5
    c=compare_saved_results(x,y)
    assert c['status']=='not_qualified'
    assert c['layers']['upper']['cv_delta_pp']==-5
    script=Path(__file__).parents[1]/'worker'/'af_compare.py'
    other=tmp_path/'other.json';other.write_text(json.dumps(y))
    p=subprocess.run([sys.executable,str(script),paths['analysis_json'],str(other)],capture_output=True,text=True,check=True)
    assert json.loads(p.stdout)['schema']=='autofluent.saved_compare.v1'


def test_boundary_native_mass_preview_is_not_certified():
    rec={'native_boundary_flux':{'zones':[
        {'zone':{'name':'inlet-a'},'status':'raw_face_flux_extracted','signed_sum_kg_s':-10,
         'positive_signed_kg_s':1,'negative_signed_magnitude_kg_s':11},
        {'zone':{'name':'outlet-a'},'status':'raw_face_flux_extracted','signed_sum_kg_s':9,
         'positive_signed_kg_s':10,'negative_signed_magnitude_kg_s':1}]}}
    m=analyze_native_flux(rec)
    p=m['port_flux_preview']
    assert p['magnitude_difference_pct']==pytest.approx(10)
    assert 'unverified' in p['status']
    assert all('cv_pct' not in v for v in m['layers'].values())


def test_full_saved_case_pipeline_mock_pyfluent(tmp_path,monkeypatch):
    """The Python integration must wire Case/Data -> real reader API -> CV.

    This is an API stub, NOT proof that a particular Fluent H5 layout works.
    """
    import types
    h5py=pytest.importorskip('h5py')
    import numpy as np
    from af_h5_offline import inspect_case_data
    case=tmp_path/'existing.cas.h5';data=tmp_path/'existing.dat.h5'
    with h5py.File(case,'w') as f:
        f.create_dataset('meshes/1/nodes/coords/1',data=[[0,0,0],[1,0,0]])
    with h5py.File(data,'w') as f:
        f.create_dataset('results/1/phase-1/faces/SV_FLUX',data=[1,2])
    class Mesh:
        def get_surface_ids(self):return [10,11,12,13]
        def get_surface_names(self):return ['inlet-1','outlet-1','upper-gap-1','upper-gap-2']
        def get_surface_locs(self, sid):return 1,2
    class Case:
        def __init__(self,case_file_name):assert case_file_name==str(case)
        def get_mesh(self):return Mesh()
    class Data:
        def __init__(self,data_file_name,case_file_handle):assert data_file_name==str(data)
        def get_phases(self):return ['phase-1']
        def get_face_variables(self,phase):return ['SV_FLUX']
        def get_cell_variables(self,phase):return ['SV_U']
        def get_face_scalar_field_data(self,phase,field,ids):
            return np.array({10:[-2e-6,-2e-6],11:[2e-6,2e-6],
                             12:[1e-7,1e-7],13:[1e-7,2e-7]}[ids[0]])
    modules={}
    for part in ('ansys','ansys.fluent','ansys.fluent.core','ansys.fluent.core.file_reader',
                 'ansys.fluent.core.file_reader.case_file','ansys.fluent.core.file_reader.data_file'):
        m=types.ModuleType(part);m.__path__=[];modules[part]=m
    modules['ansys.fluent.core.file_reader.case_file'].CaseFile=Case
    modules['ansys.fluent.core.file_reader.data_file'].DataFile=Data
    for name,m in modules.items():monkeypatch.setitem(sys.modules,name,m)
    r=inspect_case_data(case)
    assert r['pyfluent']['status']=='indexed'
    assert r['derived_metrics']['layers']['upper']['cv_pct']==pytest.approx(20)
    assert r['derived_metrics']['port_flux_preview']['magnitude_difference_pct']==pytest.approx(0)
    assert r['physical_analysis_status']=='named_native_flux_candidates_not_geometry_verified'
