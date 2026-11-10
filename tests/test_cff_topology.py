# -*- coding: utf-8 -*-
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_cff_topology import inspect_case_topology

h5py=pytest.importorskip('h5py')
np=pytest.importorskip('numpy')


def make_tetra_fixture(p,missing_zone=False,corrupt_nodes=False):
    with h5py.File(p,'w') as f:
        c=f.create_group('meshes/1/nodes/coords')
        pts=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],dtype=float)
        if corrupt_nodes:pts[0,0]=float('nan')
        c.create_dataset('1',data=pts)
        g=f.create_group('meshes/1/faces/nodes/1')
        g.create_dataset('nnodes',data=[3]*4)
        g.create_dataset('nodes',data=[2,3,4,1,4,3,1,2,4,1,3,2])
        cc=f.create_group('meshes/1/faces/c0');cc.create_dataset('1',data=[1,1,1,1])
        cc=f.create_group('meshes/1/faces/c1');cc.create_dataset('1',data=[0,0,0,0])
        z=f.create_group('meshes/1/faces/zoneTopology')
        z.create_dataset('id',data=[11]);z.create_dataset('minId',data=[1]);z.create_dataset('maxId',data=[3 if missing_zone else 4])


def test_case_cff_topology_evidence(tmp_path):
    p=tmp_path/'test.cas.h5';make_tetra_fixture(p)
    r=inspect_case_topology(p)
    assert r['status']=='geometry_arrays_consistent'
    assert r['checks']['all_faces_triangular'] is True
    assert r['coordinates']['bbox_max_m']==[1.,1.,1.]
    assert r['checks']['zone_face_coverage_verified'] is True
    assert r['flux_orientation_verified'] is False
    assert not r['wafer_cut_sections_computed']


def test_reject_incomplete_zone_ranges(tmp_path):
    p=tmp_path/'test.cas.h5';make_tetra_fixture(p,missing_zone=True)
    r=inspect_case_topology(p)
    assert r['status']=='geometry_requires_mapping_review'
    assert r['checks']['zone_face_coverage_verified'] is False


def test_reject_corrupted_coordinates(tmp_path):
    p=tmp_path/'test.cas.h5';make_tetra_fixture(p,corrupt_nodes=True)
    r=inspect_case_topology(p)
    assert r['coordinates']['complete'] is False
    assert r['status']=='geometry_requires_mapping_review'


def test_reject_wrong_mesh_container(tmp_path):
    p=tmp_path/'test.cas.h5'
    with h5py.File(p,'w') as f:f.create_group('something_else')
    r=inspect_case_topology(p)
    assert r['status']=='unsupported_mesh_count'


def test_reject_face_count_mismatch(tmp_path):
    p=tmp_path/'test.cas.h5';make_tetra_fixture(p)
    with h5py.File(p,'a') as f:
        del f['meshes/1/faces/c0/1']
        f['meshes/1/faces/c0'].create_dataset('1',data=[1,1])
    r=inspect_case_topology(p)
    assert r['status']=='geometry_requires_mapping_review'


def test_multiblock_case_is_not_mistaken_for_global_face_id_order(tmp_path):
    p=tmp_path/'multi.cas.h5';make_tetra_fixture(p)
    with h5py.File(p,'a') as f:
        for location in ('faces/nodes','faces/c0','faces/c1'):
            base=f['meshes/1/'+location]
            if location=='faces/nodes':
                blk=base.create_group('2')
                blk.create_dataset('nnodes',data=[3,3,3,3])
                blk.create_dataset('nodes',data=[2,3,4,1,4,3,1,2,4,1,3,2])
            else:
                base.create_dataset('2',data=[1,1,1,1] if location.endswith('c0') else [0,0,0,0])
        f['meshes/1/faces/zoneTopology/maxId'][0]=8
    r=inspect_case_topology(p)
    assert r['checks']['global_block_order_unambiguous'] is False
    assert r['status']=='geometry_requires_mapping_review'
