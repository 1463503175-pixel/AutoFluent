import sys
from pathlib import Path
import subprocess
import json
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'worker'))
from af_h5_offline import resolve_pair, inspect_case_data, safe_save, H5_SIGNATURE


def make_pair(tmp_path):
    h5py=pytest.importorskip('h5py')
    case=tmp_path/'A-00012.cas.h5';data=tmp_path/'A-00012.dat.h5'
    with h5py.File(case,'w') as h:
        h.create_dataset('meshes/1/nodes/coords/1', data=[[0.,0.,0.],[1.,0.,0.]])
        h.create_dataset('meshes/1/faces/zoneTopology/id', data=[1,2])
    with h5py.File(data,'w') as h:
        h.create_dataset('results/1/phase-1/faces/SV_FLUX',data=[1e-7,2e-7])
    return case,data


def test_pair_case_to_data(tmp_path):
    case,dat=make_pair(tmp_path)
    assert resolve_pair(case)==(case,dat)
    assert resolve_pair(dat)==(case,dat)


def test_h5_signature_and_index_no_physics_claim(tmp_path):
    case,dat=make_pair(tmp_path)
    r=inspect_case_data(case,use_pyfluent=False)
    assert r['case']['hdf5_signature']
    assert r['data']['hdf5_signature']
    assert any('FLUX' in x['path'] for x in r['h5_data']['datasets'])
    assert r['verification']['native_face_flux_integrals']=='not_computed'
    assert r['physical_analysis_status']=='not_yet_computed'
    assert r['reader_mode']=='saved_files_only_no_workbench_no_fluent'


def test_missing_match_does_not_guess(tmp_path):
    case,dat=make_pair(tmp_path);dat.unlink()
    with pytest.raises(FileNotFoundError):resolve_pair(case)


def test_fake_h5_rejected(tmp_path):
    c=tmp_path/'bad.cas.h5';d=tmp_path/'bad.dat.h5'
    c.write_bytes(b'abc'*100); d.write_bytes(b'def'*100)
    with pytest.raises(ValueError,match='HDF5'):inspect_case_data(c,use_pyfluent=False)


def test_no_override_results(tmp_path):
    case,dat=make_pair(tmp_path)
    r=inspect_case_data(case,use_pyfluent=False)
    out=safe_save(r,tmp_path/'results')
    assert out.exists() and out!=case and out!=dat
    assert json.loads(out.read_text(encoding='utf-8'))['schema']=='autofluent.offline_case_data.v1'


def test_cli_without_fluent_works(tmp_path):
    case,dat=make_pair(tmp_path)
    cli=Path(__file__).parents[1]/'worker'/'af_h5_offline.py'
    proc=subprocess.run([sys.executable,str(cli),'--file',str(case),'--skip-pyfluent',
                         '--output-dir',str(tmp_path/'output')],capture_output=True,text=True,check=True)
    data=json.loads(proc.stdout)
    assert data['model_id']=='A-00012'
    assert Path(data['index_file']).exists()


def test_native_flux_zones_mock_reader():
    import numpy as np
    from af_h5_offline import extract_native_boundary_flux
    class Mesh:
        def get_surface_locs(self, sid): return 1,2
    class Data:
        def get_face_scalar_field_data(self, phase, field, ids):
            assert (phase,field,ids)==('phase-1','SV_FLUX',[7])
            return np.array([1e-6,-2e-7])
    x=extract_native_boundary_flux(Mesh(),Data(),['phase-1'],{'phase-1':{'face_variables':['SV_FLUX']}},[{'id':7,'name':'mass-flow-inlet'},{'id':11,'name':'wall'}])
    assert len(x['zones'])==1
    assert x['zones'][0]['status']=='raw_face_flux_extracted'
    assert x['zones'][0]['signed_sum_kg_s']==pytest.approx(8e-7)
