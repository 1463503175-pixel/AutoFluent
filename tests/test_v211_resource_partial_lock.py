"""Targeted synthetic counterexamples for the user's reported blocking cases.

Sparse H5 allocation here tests metadata gates ONLY, not real 26m CFD solver.
"""
import json
import sys
import subprocess
from pathlib import Path

import pytest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))


def make_sparse_26m_pair(folder):
    import h5py
    nf=26_011_622
    wall=3_036_000
    case=folder/'FFF.cas.h5';data=folder/'FFF.dat.h5'
    with h5py.File(case,'w') as h:
        mesh=h.require_group('meshes/1')
        mesh.create_dataset('nodes/coords/4',shape=(8_000_000,3),dtype='f8')
        nodes=mesh.require_group('faces/nodes/1')
        nodes.create_dataset('nnodes',shape=(nf,),dtype='i4')
        nodes.create_dataset('nodes',shape=(nf*3,),dtype='i4')
        mesh.create_dataset('faces/c0/1',shape=(nf,),dtype='i4')
        c1=mesh.create_dataset('faces/c1/1',shape=(1,),dtype='i4')
        c1.attrs['minId']=[nf];c1.attrs['maxId']=[nf]
        mesh.create_dataset('cells/zoneTopology/maxId',data=[7_000_000])
        topo=mesh.require_group('faces/zoneTopology')
        topo.create_dataset('name',data=[b'wall;interior'])
        topo.create_dataset('minId',data=[1,wall+1])
        topo.create_dataset('maxId',data=[wall,nf])
    with h5py.File(data,'w') as h:
        h.create_dataset('settings/Case File',data=[b'FFF.cas.h5'])
        ds=h.create_dataset('results/1/phase-1/faces/SV_FLUX/1',shape=(nf,),dtype='f8')
        ds.attrs['minId']=[1];ds.attrs['maxId']=[nf]
    return case,data


def test_26m_faces_reach_budget_gate_not_old_fixed_limit(tmp_path,monkeypatch):
    import af_adaptive_native as native
    case,data=make_sparse_26m_pair(tmp_path)
    with pytest.raises(native.UnsupportedCFF,match='faces=26011622, wall_faces=3036000.*budget=0.50 GiB'):
        native._read_cff(case,data,memory_budget_mb=512)
    # Force termination after the metadata budget phase: prove 6 GiB
    # accepts this face count *without* materializing its fake 26m arrays.
    def stop_after_metadata(*args,**kwargs):
        raise native.UnsupportedCFF('passed_budget_stage_no_full_H5_reads')
    monkeypatch.setattr(native,'_select_face_results',stop_after_metadata)
    with pytest.raises(native.UnsupportedCFF,match='passed_budget_stage_no_full_H5_reads'):
        native._read_cff(case,data,memory_budget_mb=6144)
    assert case.stat().st_size<100_000 and data.stat().st_size<100_000


def test_full_cli_keeps_native_port_metrics_when_wafer_fails(tmp_path):
    from test_adaptive_native import make_pair
    case,data=make_pair(tmp_path)
    run=subprocess.run([sys.executable,str(ROOT/'worker/af_h5_offline.py'),
        '--file',str(data),'--output-dir',str(tmp_path/'index'), '--skip-pyfluent'],
        text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result['physical_analysis_status']=='partial_physical_metrics'
    assert result['derived_metrics']['mass_imbalance_pct']==0
    assert result['derived_metrics']['pressure_drop_pa_low_speed_approx']==pytest.approx(1.)
    assert result['module_results']['wafer_geometry']['status']=='blocked'
    assert result['module_results']['port_flux']['status']=='completed'
    html=Path(result['html_file']).read_text('utf-8')
    assert '首个实际阻塞' in html and '入口原生净流量' in html
    assert '未取得' in html and 'not_yet_computed' not in html


def test_same_target_publish_and_solve_share_durable_lock(tmp_path):
    from af_target_lock import acquire_target,release_confirmed
    app=tmp_path
    lock=acquire_target(app,'C:/demo.wbpj','FFF','Setup','job-1','solve')
    with pytest.raises(FileExistsError,match='same Fluent target|Same Fluent target'):
        acquire_target(app,'C:/demo.wbpj','FFF','Setup','job-2','publication')
    assert not release_confirmed(lock,'job-2')
    assert release_confirmed(lock,'job-1')
    another=acquire_target(app,'C:/demo.wbpj','FFF','Setup','job-2','publication')
    assert release_confirmed(another,'job-2')
