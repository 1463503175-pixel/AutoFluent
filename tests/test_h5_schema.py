import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'worker'))
from af_h5_schema import inspect_structure, save_pair_schema
from test_h5_offline import make_pair


def test_schema_probe_is_small_and_only_samples(tmp_path):
    c,d=make_pair(tmp_path)
    r=inspect_structure(d)
    assert r['status']=='metadata_and_bounded_samples'
    assert r['physical_analysis_status']=='not_computed'
    flux=[s for s in r['samples'] if 'SV_FLUX' in s['path']]
    assert flux and len(flux[0]['first_values'])<=16
    path=save_pair_schema(c,d,tmp_path/'probes')
    obj=json.loads(Path(path).read_text(encoding='utf8'))
    assert obj['schema']=='autofluent.h5_schema_probe.v1'
    assert obj['verification']=='HDF5_METADATA_AND_TINY_SAMPLES_ONLY'
    assert obj['upload_status']=='local_only_no_upload'
    assert Path(path).stat().st_size<100000


def test_schema_metadata_only_for_unknown_compound_dtype(tmp_path):
    h5py=pytest.importorskip('h5py')
    p=tmp_path/'data.h5'
    with h5py.File(p,'w') as h:h.create_dataset('mesh/face/labels',data=[b'one',b'two'])
    d=inspect_structure(p)
    assert d['entries'] and d['samples']==[]
