import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/'worker'))
from af_h5_batch import discover_pairs, inventory, save_inventory
from test_h5_offline import make_pair


def test_batch_inventory_finds_pairs_not_fake_metrics(tmp_path):
    a=tmp_path/'model_a';a.mkdir()
    case,data=make_pair(a)
    (tmp_path/'other.cas.h5').write_bytes(b'\x89HDF\r\n\x1a\n'+b'x'*200)
    r=inventory(tmp_path)
    assert len(r['pairs'])==1
    assert len(r['missing_data'])==1
    assert r['pairs'][0]['case']['path']==str(case)
    assert r['pairs'][0]['physical_metrics']=='not_computed'
    assert r['pairs'][0]['hdf5_headers_ok']
    assert r['status']=='paired_candidates_found'
    saved=save_inventory(r,tmp_path/'reports')
    assert saved['physical_analysis_status']=='not_computed'
    assert Path(saved['json_file']).is_file()
    assert '未进行场数据计算' in Path(saved['html_file']).read_text(encoding='utf8')
    assert 'physical_metrics' in Path(saved['json_file']).read_text(encoding='utf8')


def test_batch_no_pairs_refuses_to_invent(tmp_path):
    report=inventory(tmp_path)
    assert report['status']=='no_pairs' and report['pairs']==[]


def test_batch_only_same_stem_paired(tmp_path):
    p=tmp_path/'different';p.mkdir()
    (p/'one.cas.h5').write_bytes(b'x'*200)
    (p/'two.dat.h5').write_bytes(b'x'*200)
    r=inventory(p)
    assert not r['pairs'] and len(r['missing_data'])==1


def test_batch_case_insensitive_windows_filenames(tmp_path):
    p=tmp_path/'case';p.mkdir()
    (p/'ABC.CAS.H5').write_bytes(b'\x89HDF\r\n\x1a\n'+b'x'*128)
    (p/'Abc.Dat.H5').write_bytes(b'\x89HDF\r\n\x1a\n'+b'y'*128)
    r=inventory(p)
    assert len(r['pairs'])==1
    assert r['pairs'][0]['hdf5_headers_ok']
