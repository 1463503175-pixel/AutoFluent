# -*- coding: utf-8 -*-
"""Mathematical RT0 reconstruction on a linear tetrahedral element.

Input fluxes are signed *outward* integrals for its four faces, indexed by the
opposite vertex. Actual Fluent SV_FLUX orientation must be mapped to these
local outward normals before calling this module. No Fluent/H5 I/O here.

RT0 vector field on each tetrahedron has form q(x) = a + b*x, b scalar.
This reproduces every supplied face integral exactly up to roundoff and is
conservative on arbitrary planar sections. It is NOT an arbitrary CFD solver.
"""
import math


def sub(a, b): return tuple(x-y for x,y in zip(a,b))
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def mul(a,v):return tuple(a*x for x in v)
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def norm(v):return math.sqrt(dot(v,v))


def _vertices(tetra):
    if len(tetra)!=4:raise ValueError('RT0 requires four tetrahedron vertices')
    pts=tuple(tuple(float(x) for x in v) for v in tetra)
    if any(len(p)!=3 or any(not math.isfinite(x) for x in p) for p in pts):
        raise ValueError('Invalid tetrahedron coordinates')
    v=dot(sub(pts[1],pts[0]),cross(sub(pts[2],pts[0]),sub(pts[3],pts[0])))/6
    if abs(v)<1e-30:raise ValueError('Degenerate tetrahedron')
    return pts


def _face(pts, opp):
    idx=[i for i in range(4) if i!=opp]
    a,b,c=(pts[i] for i in idx)
    area=mul(.5,cross(sub(b,a),sub(c,a)))
    centroid=mul(1/3,add(add(a,b),c))
    if dot(area,sub(pts[opp],centroid))>0:area=mul(-1,area)
    return area,centroid


def _solve4(a,b):
    m=[list(map(float,row))+[float(x)] for row,x in zip(a,b)]
    for k in range(4):
        p=max(range(k,4),key=lambda i:abs(m[i][k]))
        if abs(m[p][k])<1e-28:raise ValueError('RT0 face matrix singular')
        m[k],m[p]=m[p],m[k]
        inv=1/m[k][k]
        for j in range(k,5):m[k][j]*=inv
        for i in range(4):
            if i==k:continue
            f=m[i][k]
            for j in range(k,5):m[i][j]-=f*m[k][j]
    return [m[i][4] for i in range(4)]


def reconstruct(tetra, outward_face_fluxes):
    """Return (a-vector, b) for q(x)=a+b*x with prescribed face mass fluxes."""
    pts=_vertices(tetra)
    if len(outward_face_fluxes)!=4:raise ValueError('Exactly four outward fluxes required')
    flux=[float(v) for v in outward_face_fluxes]
    if not all(map(math.isfinite,flux)):raise ValueError('Nonfinite face flux')
    rows=[]
    for i in range(4):
        area,c=_face(pts,i)
        rows.append([*area,dot(area,c)])
    a,b,c,d=_solve4(rows,flux)
    return (a,b,c),d


def face_integrals(tetra, rt0):
    """Re-evaluate the four original face integrals for conservation checks."""
    pts=_vertices(tetra)
    a,b=rt0
    return [dot(add(a,mul(b,c)),area) for i in range(4) for area,c in [_face(pts,i)]]


def cut_polygon(tetra, origin, normal):
    """Intersect tetrahedron with a plane. Returns polygon points, no area guess."""
    pts=_vertices(tetra)
    o=tuple(float(x) for x in origin);n=tuple(float(x) for x in normal)
    if len(o)!=3 or len(n)!=3 or norm(n)==0:raise ValueError('Invalid cutting plane')
    extent=max(norm(sub(p,q)) for p in pts for q in pts)
    eps=max(1e-12,extent*1e-11)
    d=[dot(sub(p,o),n) for p in pts]
    found=[]
    for i in range(4):
        if abs(d[i])<=eps:found.append(pts[i])
    for i in range(4):
        for j in range(i+1,4):
            if d[i]*d[j]<0 and abs(d[i])>eps and abs(d[j])>eps:
                t=d[i]/(d[i]-d[j])
                found.append(add(pts[i],mul(t,sub(pts[j],pts[i]))))
    unique=[]
    for p in found:
        if not any(norm(sub(p,q))<=eps for q in unique):unique.append(p)
    if len(unique)<3:return []
    center=mul(1/len(unique),tuple(sum(p[k] for p in unique) for k in range(3)))
    nn=mul(1/norm(n),n)
    axis=(1.,0.,0.) if abs(nn[0])<.9 else (0.,1.,0.)
    u=cross(nn,axis);u=mul(1/norm(u),u)
    v=cross(nn,u)
    return sorted(unique,key=lambda p:math.atan2(dot(sub(p,center),v),dot(sub(p,center),u)))


def section_flux(tetra, outward_face_fluxes, origin, normal):
    """Signed mass-flux integral through the oriented tetrahedron-plane cut.

    The cut must be interpreted as part of a physical, consistently oriented
    measurement surface. The sign is along `normal`, not an inlet/outlet label.
    """
    polygon=cut_polygon(tetra,origin,normal)
    if len(polygon)<3:return {'net_flux':0.,'area':0.,'vertices':0}
    n=tuple(float(v) for v in normal)
    nunit=mul(1/norm(n),n)
    area_vec=(0.,0.,0.)
    for p,q in zip(polygon,polygon[1:]+polygon[:1]):
        area_vec=add(area_vec,mul(.5,cross(p,q)))
    area=dot(area_vec,nunit)
    if area<0:area=-area
    if area<=1e-28:return {'net_flux':0.,'area':0.,'vertices':0}
    centroid=mul(1/len(polygon),tuple(sum(p[k] for p in polygon) for k in range(3)))
    a,b=reconstruct(tetra,outward_face_fluxes)
    flux=dot(add(a,mul(b,centroid)),nunit)*area
    return {'net_flux':flux,'area':area,'vertices':len(polygon)}
