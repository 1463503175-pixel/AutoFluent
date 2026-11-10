# -*- coding: utf-8 -*-
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_cff_rt0 import read_tetra_mesh,integrate_rt0_plane

np=pytest.importorskip('numpy')
h5py=pytest.importorskip('h5py')


def fixture(path,broken=False):
    # Single tetra; nodes oriented outward from cell 1.  q=(1,0,0) has
    # outward face fluxes [+.5,-.5,0,0]. Plane x=.3 section area=.245.
    with h5py.File(path,'w') as f:
        f.create_dataset('meshes/1/nodes/coords/1',data=[[0,0,0],[1,0,0],[0,1,0],[0,0,1]])
        g=f.create_group('meshes/1/faces/nodes/1')
        g.create_dataset('nnodes',data=[3,3,3,3])
        g.create_dataset('nodes',data=[2,3,4,1,4,3,1,2,4,1,3,2])
        f.create_dataset('meshes/1/faces/c0/1',data=[1,1,1,1])
        f.create_dataset('meshes/1/faces/c1/1',data=[0,0,0,0])
    if broken:
        with h5py.File(path,'a') as f:
            del f['meshes/1/faces/nodes/1/nodes']
            f.create_dataset('meshes/1/faces/nodes/1/nodes',data=[2,3,4])


def test_true_cff_mesh_to_rt0_plane(tmp_path):
    p=tmp_path/'s.cas.h5';fixture(p)
    m=read_tetra_mesh(p)
    assert m['n_faces']==4
    r=integrate_rt0_plane(m,[.5,-.5,0,0],(.3,0,0),(1,0,0),source_orientation_verified=True)
    assert r['cut_tetra_count']==1
    assert r['net_flux_kg_s']==pytest.approx(.245,abs=1e-10)
    assert r['section_area_m2']==pytest.approx(.245)
    assert r['positive_kg_s']==pytest.approx(.245)
    r=integrate_rt0_plane(m,[.5,-.5,0,0],(.3,0,0),(-1,0,0),source_orientation_verified=True)
    assert r['net_flux_kg_s']==pytest.approx(-.245,abs=1e-10)


def test_fails_closed_without_face_orientation_proof(tmp_path):
    p=tmp_path/'s.cas.h5';fixture(p);m=read_tetra_mesh(p)
    with pytest.raises(ValueError,match='orientation'):
        integrate_rt0_plane(m,[.5,-.5,0,0],(.3,0,0),(1,0,0))


def test_fails_closed_on_missing_field_or_corrupt_connectivity(tmp_path):
    p=tmp_path/'s.cas.h5';fixture(p);m=read_tetra_mesh(p)
    with pytest.raises(ValueError,match='align'):
        integrate_rt0_plane(m,[.5,-.5,0],(.3,0,0),(1,0,0),source_orientation_verified=True)
    fixture(p,broken=True)
    with pytest.raises(ValueError,match='connectivity'):
        read_tetra_mesh(p)


def test_incorrect_c0_c1_mapping_rejected(tmp_path):
    p=tmp_path/'s.cas.h5';fixture(p)
    with h5py.File(p,'a') as f:
        f['meshes/1/faces/c0/1'][0]=0
    with pytest.raises(ValueError,match='1-based'):
        read_tetra_mesh(p)
