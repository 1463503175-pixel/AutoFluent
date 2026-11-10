"""Regression for genuinely segmented *topology* (not just SV_FLUX).
Synthetic data does not substitute for a real large Fluent saving.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_adaptive_native import analyze_saved_pair, UnsupportedCFF
from test_adaptive_full_cff import build_synthetic_ald_pair


def _split_dataset(parent,name,split_at,*,unit='face'):
    """Split one HDF5 dataset into two blocks with explicit global ID maps."""
    ds=parent[name]
    data=ds[:]
    del parent[name]
    for k,(start,end) in enumerate(((0,split_at),(split_at,len(data))),1):
        sub=parent.create_dataset(str(k),data=data[start:end])
        sub.attrs['minId']=[start+1]
        sub.attrs['maxId']=[end]


def _split_case(case):
    h5py=pytest.importorskip('h5py')
    with h5py.File(case,'r+') as f:
        base=f['meshes/1']
        nodes=base['nodes/coords']
        coords=nodes['4'][:];del nodes['4']
        split=max(1,len(coords)//3)
        for k,(start,end) in enumerate(((0,split),(split,len(coords))),1):
            ds=nodes.create_dataset(str(k),data=coords[start:end]);ds.attrs['minId']=[start+1];ds.attrs['maxId']=[end]
        c0=base['faces/c0'];_split_dataset(c0,'1',len(c0['1'])//2)
        c1=base['faces/c1']
        before=c1['1']; values=before[:];off=int(before.attrs['minId'][0])-1;del c1['1']
        split=max(1,len(values)//2)
        for k,(start,end) in enumerate(((0,split),(split,len(values))),1):
            ds=c1.create_dataset(str(k),data=values[start:end]);ds.attrs['minId']=[off+start+1];ds.attrs['maxId']=[off+end]
        fn=base['faces/nodes']
        old=fn['1'];counts=old['nnodes'][:];flat=old['nodes'][:];del fn['1']
        split=max(1,len(counts)//2)
        for k,(start,end) in enumerate(((0,split),(split,len(counts))),1):
            group=fn.create_group(str(k))
            group.attrs['minId']=[start+1];group.attrs['maxId']=[end]
            group.create_dataset('nnodes',data=counts[start:end])
            group.create_dataset('nodes',data=flat[3*start:3*end])


def test_full_topology_split_keeps_native_per_channel_values(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    previous=analyze_saved_pair(case,data)
    _split_case(case)
    result=analyze_saved_pair(case,data)
    assert result['provenance']['topology_block_counts']=={'nodes':2,'face_nodes':2,'c0':2,'c1':2}
    assert result['port_flux']['inlet_kg_s']==pytest.approx(previous['port_flux']['inlet_kg_s'])
    assert result['geometry']['total_plates']==previous['geometry']['total_plates']
    for layer in ('upper','lower'):
        assert result['layers'][layer]['net_kg_s']==pytest.approx(previous['layers'][layer]['net_kg_s'],rel=1e-12)
        assert result['layers'][layer]['cv_pct']==pytest.approx(previous['layers'][layer]['cv_pct'],rel=1e-12)
        assert [x['net_kg_s'] for x in result['layers'][layer]['channels']]==pytest.approx(
            [x['net_kg_s'] for x in previous['layers'][layer]['channels']],rel=1e-12)


def test_c0_overlap_rejected_not_double_counted(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    _split_case(case)
    with h5py.File(case,'r+') as f:
        f['meshes/1/faces/c0/2'].attrs['minId']=[int(f['meshes/1/faces/c0/2'].attrs['minId'][0])-1]
    with pytest.raises(UnsupportedCFF,match='overlap|ID/length'):
        analyze_saved_pair(case,data)


def test_missing_pressure_does_not_erase_valid_native_mass_flux(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    with h5py.File(data,'r+') as f:
        del f['results/1/phase-1/faces/SV_P']
    metrics=analyze_saved_pair(case,data)
    assert metrics['port_flux']['inlet_kg_s']>0
    assert metrics['layers']['upper']['channel_count']>0
    assert not metrics['pressure']['status'].startswith('computed')


def test_progress_atomic_file_monotone_and_no_fake_eta(monkeypatch,tmp_path):
    import af_progress
    path=tmp_path/'task.json';monkeypatch.setenv('AUTOFLUENT_PROGRESS_FILE',str(path))
    af_progress.update(10,'预检','Case/Data核验',force=True)
    p=json.loads(path.read_text())
    assert p['percent']==10 and p['meaning']=='weighted_completed_stages_not_eta'
    af_progress.portion(30,70,5,10,'块','5/10')
    p=json.loads(path.read_text())
    assert p['percent']==50 and p['completed_units']==5 and p['total_units']==10
    assert list(tmp_path.glob('*.tmp'))==[]
