# -*- coding: utf-8 -*-
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_native_flux_map import native_global_face_flux,check_native_cell_closure
from test_cff_rt0 import fixture

np=pytest.importorskip('numpy')

class Mesh:
    def __init__(self,ranges):self.ranges=ranges
    def get_surface_locs(self,i):return self.ranges[i]
class Data:
    def __init__(self,vals):self.vals=vals
    def get_face_scalar_field_data(self,p,v,ids):
        assert p=='phase-1' and v=='SV_FLUX'
        return self.vals[ids[0]]


def test_real_face_id_mapping_and_cell_conservation(tmp_path):
    p=tmp_path/'x.cas.h5';fixture(p)
    flux,e=native_global_face_flux(Mesh({8:(1,2),9:(3,4)}),Data({8:[.5,-.5],9:[0.,0.]}),'phase-1',
                                   [{'id':8,'name':'wall'},{'id':9,'name':'wall2'}],4)
    assert e['status']=='complete_original_sv_flux_global_face_id_map'
    assert flux.tolist()==[.5,-.5,0.,0.]
    checks=check_native_cell_closure(p,flux)
    assert checks['nonempty_cell_count']==1
    assert checks['tetra_face_count_cells']==1
    assert checks['absolute_cell_net_max_kg_s']==pytest.approx(0)
    assert checks['boundary_signed_net_kg_s']==pytest.approx(0)


def test_missing_face_and_overlap_rejected():
    a,b=native_global_face_flux(Mesh({8:(1,2)}),Data({8:[1,2]}),'phase-1',[8],3)
    assert a is None and b['status']=='unmapped_global_face_ids'
    a,b=native_global_face_flux(Mesh({8:(1,2),9:(2,3)}),Data({8:[1,2],9:[3,4]}),'phase-1',[8,9],3)
    assert a is None and b['status']=='overlapping_face_zone_ids'


def test_field_length_and_nonfinite_rejected():
    a,b=native_global_face_flux(Mesh({1:(1,3)}),Data({1:[1,2]}),'phase-1',[1],3)
    assert a is None and b['status']=='native_flux_face_length_mismatch'
    a,b=native_global_face_flux(Mesh({1:(1,3)}),Data({1:[1,float('nan'),3]}),'phase-1',[1],3)
    assert a is None and b['status']=='nonfinite_native_flux'


def test_cell_owner_misalignment_rejected(tmp_path):
    p=tmp_path/'x.cas.h5';fixture(p)
    checks=check_native_cell_closure(p,np.asarray([1.,2.]))
    assert checks['status']=='face_cell_arrays_not_global_id_aligned'
