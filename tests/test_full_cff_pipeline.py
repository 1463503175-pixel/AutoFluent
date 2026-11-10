# -*- coding: utf-8 -*-
"""Full small-CFF H5 -> PyFluent API stub -> native Face IDs -> closure -> report.

Synthetic API fixtures only, not an Ansys 2024 R2 real-H5 acceptance test.
"""
import sys,json,types
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from test_cff_rt0 import fixture

h5py=pytest.importorskip('h5py')
np=pytest.importorskip('numpy')


def test_full_small_cff_report_pipeline(tmp_path,monkeypatch):
    case=tmp_path/'case.cas.h5';data=tmp_path/'case.dat.h5'
    fixture(case)
    with h5py.File(case,'a') as f:
        z=f.create_group('meshes/1/faces/zoneTopology')
        z.create_dataset('id',data=[21,22])
        z.create_dataset('minId',data=[1,3])
        z.create_dataset('maxId',data=[2,4])
    with h5py.File(data,'w') as f:
        f.create_dataset('results/1/phase-1/faces/SV_FLUX',data=[.5,-.5,0.,0.])
    class Mesh:
        def get_surface_ids(self):return [21,22]
        def get_surface_names(self):return ['inlet-1','outlet-1']
        def get_surface_locs(self,sid):return {21:(1,2),22:(3,4)}[sid]
    class Case:
        def __init__(self,case_file_name):assert case_file_name==str(case)
        def get_mesh(self):return Mesh()
    class Data:
        def __init__(self,data_file_name,case_file_handle):assert data_file_name==str(data)
        def get_phases(self):return ['phase-1']
        def get_face_variables(self,phase):return ['SV_FLUX']
        def get_cell_variables(self,phase):return []
        def get_face_scalar_field_data(self,phase,name,ids):
            return np.array({21:[.5,-.5],22:[0.,0.]}[ids[0]])
    modules={}
    for part in ('ansys','ansys.fluent','ansys.fluent.core','ansys.fluent.core.file_reader',
                 'ansys.fluent.core.file_reader.case_file','ansys.fluent.core.file_reader.data_file'):
        mod=types.ModuleType(part);mod.__path__=[];modules[part]=mod
    modules['ansys.fluent.core.file_reader.case_file'].CaseFile=Case
    modules['ansys.fluent.core.file_reader.data_file'].DataFile=Data
    for name,mod in modules.items():monkeypatch.setitem(sys.modules,name,mod)
    from af_h5_offline import inspect_case_data
    from af_saved_analysis import write_saved_report
    r=inspect_case_data(case)
    assert r['cff_topology']['status']=='geometry_arrays_consistent'
    assert r['pyfluent']['global_native_sv_flux']['mapped_faces']==4
    assert r['pyfluent']['native_cell_flux_balance']['relative_cell_imbalance_p95']==pytest.approx(0)
    assert r['verification']['global_original_sv_flux_face_id_mapping']=='complete'
    assert r['physical_analysis_status']=='global_face_and_cell_balance_candidate_only'
    # Never fabricate channel CV even if the full global flux matches.
    assert r['derived_metrics']['layers']['upper']['status']=='missing_named_channels'
    paths=write_saved_report(r,tmp_path)
    html=Path(paths['html_file']).read_text(encoding='utf-8')
    assert '5. 数据可信度' in html and '原生' in html
    assert '晶圆片间' in html
    parsed=json.loads(Path(paths['analysis_json']).read_text(encoding='utf-8'))
    assert parsed['physical_analysis_status']==r['physical_analysis_status']
