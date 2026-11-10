import math
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_rt0 import reconstruct,face_integrals,section_flux,_face,add,mul,dot

T=((0,0,0),(1,0,0),(0,1,0),(0,0,1))

def fluxes(q):
    return [dot(q(c),area) for i in range(4) for area,c in [_face(T,i)]]

@pytest.mark.parametrize('vel',[(1.,0.,0.),(0.,0.,1.),(1.,2.,-3.),(-2.,1.,0.)])
def test_uniform_exact_face_and_section(vel):
    f=fluxes(lambda x:vel)
    a,b=reconstruct(T,f)
    assert a==pytest.approx(vel,abs=1e-12)
    assert b==pytest.approx(0,abs=1e-12)
    assert face_integrals(T,(a,b))==pytest.approx(f)
    # Plane x=0.25 has triangle legs of 0.75, area 0.28125.
    r=section_flux(T,f,(.25,0,0),(1,0,0))
    assert r['area']==pytest.approx(.5*.75*.75)
    assert r['net_flux']==pytest.approx(vel[0]*.5*.75*.75)
    reverse=section_flux(T,f,(.25,0,0),(-1,0,0))
    assert reverse['net_flux']==pytest.approx(-r['net_flux'])

def test_divergent_affine_rt0_exact():
    q=lambda x:(2+3*x[0],-1+3*x[1],4+3*x[2])
    f=fluxes(q)
    a,b=reconstruct(T,f)
    assert a==pytest.approx((2.,-1.,4.))
    assert b==pytest.approx(3.)
    assert sum(f)==pytest.approx(9/6)
    res=section_flux(T,f,(.4,0,0),(1,0,0))
    assert res['net_flux']==pytest.approx((2+3*.4)*(.6*.6/2))

def test_intersection_outside_tetra_is_zero():
    r=section_flux(T,fluxes(lambda x:(1,1,1)),(2,0,0),(1,0,0))
    assert r['net_flux']==0 and r['area']==0

def test_degenerate_and_nonfinite_inputs_rejected():
    with pytest.raises(ValueError):reconstruct(((0,0,0),)*4,[1,2,3,4])
    with pytest.raises(ValueError):reconstruct(T,[1,2,float('nan'),4])
