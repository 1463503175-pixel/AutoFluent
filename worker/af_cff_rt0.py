# -*- coding: utf-8 -*-
"""Strict HDF5 CFF tetrahedron -> original signed face flux -> RT0 section.

This is the geometry-to-RT0 bridge for verified 1-based Fluent face/cell/node
arrays.  It deliberately does not invent wafer cuts.  A caller must supply a
plane, a corresponding native SV_FLUX array in *global face ID order*, and
explicit confirmation that native flux is positive outward of c0.  Without
that confirmation an integration is refused.  For large production H5 cases
we need partition-aware streaming and geometric wafer-cut construction.
"""
import math
from pathlib import Path

from af_rt0 import section_flux, sub, cross, dot

DEFAULT_MAX_FACES = 800000
DEFAULT_MAX_CELLS = 500000


def _single_dataset(handle, group_path, suffix=None):
    """Require exactly one Fluent block; multi-block topology needs ID offsets."""
    import h5py
    if group_path not in handle:
        raise ValueError('Missing CFF dataset: '+group_path)
    parent=handle[group_path]
    if isinstance(parent,h5py.Dataset) and suffix is None:return parent
    if not isinstance(parent,h5py.Group) or len(parent)!=1:
        raise ValueError('CFF multi-block/ambiguous topology is not yet supported: '+group_path)
    child=parent[next(iter(parent))]
    if suffix is not None:
        if not isinstance(child,h5py.Group) or suffix not in child:
            raise ValueError('Missing '+suffix+' in '+group_path)
        child=child[suffix]
    if not isinstance(child,h5py.Dataset):
        raise ValueError('CFF topology block is not an array: '+group_path)
    return child


def read_tetra_mesh(case_path, *, max_faces=DEFAULT_MAX_FACES, max_cells=DEFAULT_MAX_CELLS):
    """Read a small/medium, unpartitioned Fluent CFF tetra mesh with checks.

    Returns arrays with node indices zero-based, cell ownership **one-based**
    (zero denotes missing c1). No face-zone names or physical cut is assumed.
    """
    import numpy as np
    import h5py
    with h5py.File(str(Path(case_path).resolve()),'r') as h:
        m=h.get('/meshes')
        if m is None or len(m)!=1:raise ValueError('Require one CFF mesh')
        root='/meshes/'+next(iter(m))
        nn_ds=_single_dataset(h,root+'/faces/nodes','nnodes')
        face_count=int(nn_ds.size)
        if face_count==0 or face_count>max_faces:
            raise ValueError('Face count outside memory-bounded RT0 pilot: '+str(face_count))
        nn=np.asarray(nn_ds[...],dtype=np.int64).reshape(-1)
        if len(nn)!=face_count or not np.all(nn==3):
            raise ValueError('RT0 pilot supports triangular tetra faces only')
        raw_nodes=np.asarray(_single_dataset(h,root+'/faces/nodes','nodes')[...],dtype=np.int64).reshape(-1)
        if raw_nodes.size != 3*face_count:raise ValueError('Face/node connectivity length mismatch')
        c0=np.asarray(_single_dataset(h,root+'/faces/c0')[...],dtype=np.int64).reshape(-1)
        c1=np.asarray(_single_dataset(h,root+'/faces/c1')[...],dtype=np.int64).reshape(-1)
        if len(c0)!=face_count or len(c1)!=face_count:
            raise ValueError('Complete c0/c1 arrays required; cannot guess boundary padding')
        if c0.min()<1 or c1.min()<0:raise ValueError('Fluent owner IDs must be 1-based; c1 zero only for boundaries')
        max_cell=int(max(c0.max(),c1.max()))
        if max_cell>max_cells:raise ValueError('Cell count exceeds bounded RT0 pilot: '+str(max_cell))
        if np.any((c1>0)&(c0==c1)):raise ValueError('Face references same cell on both sides')
        coords=np.asarray(_single_dataset(h,root+'/nodes/coords')[...],dtype=np.float64)
        if coords.ndim!=2:raise ValueError('Invalid node coordinate rank')
        if coords.shape[1]==3 and coords.shape[0]!=3: pass
        elif coords.shape[0]==3 and coords.shape[1]!=3:coords=coords.T.copy()
        else:raise ValueError('Ambiguous node coordinate layout')
        if not np.isfinite(coords).all():raise ValueError('Nonfinite mesh coordinates')
        nnode=len(coords)
        if raw_nodes.min()<1 or raw_nodes.max()>nnode:
            raise ValueError('Face vertex ids outside node array')
        return {'vertices':coords,'face_vertices':(raw_nodes-1).reshape(-1,3),
                'c0':c0,'c1':c1,'n_cells':max_cell,'n_faces':face_count}


def integrate_rt0_plane(mesh, face_sv_flux, origin, normal, *,
                         source_orientation_verified=False,
                         max_candidate_cells=300000):
    """Integrate a specified physical plane on tetrahedra after validating IDs.

    `face_sv_flux` must be indexed exactly by (global Fluent Face ID - 1),
    positive outward of c0. Caller MUST independently verify this convention.
    No 'wafer' or 'bypass' label is attached without real cut partitioning.
    """
    import numpy as np
    if not source_orientation_verified:
        raise ValueError('SV_FLUX -> c0 outward orientation was not verified; no RT0 value emitted')
    points=np.asarray(mesh['vertices'],dtype=float)
    fv=np.asarray(mesh['face_vertices'],dtype=np.int64)
    c0=np.asarray(mesh['c0'],dtype=np.int64).reshape(-1)
    c1=np.asarray(mesh['c1'],dtype=np.int64).reshape(-1)
    flux=np.asarray(face_sv_flux,dtype=np.float64).reshape(-1)
    nf=len(fv)
    if nf==0 or len(c0)!=nf or len(c1)!=nf or len(flux)!=nf:
        raise ValueError('All saved face arrays and SV_FLUX must align by global Face ID')
    if not np.isfinite(flux).all():raise ValueError('Nonfinite saved SV_FLUX')
    if fv.shape!=(nf,3) or fv.min()<0 or fv.max()>=len(points):
        raise ValueError('Unsupported or invalid face connectivity')
    n=np.asarray(normal,dtype=float)
    o=np.asarray(origin,dtype=float)
    if n.shape!=(3,) or o.shape!=(3,) or not np.isfinite(n).all() or not np.isfinite(o).all() or not np.linalg.norm(n)>0:
        raise ValueError('Invalid measurement plane')
    norm=n/np.linalg.norm(n)
    # Filter faces in bounded NumPy chunks; avoid creating face*vertex*3 for all faces.
    candidate=[]
    for begin in range(0,nf,100000):
        end=min(begin+100000,nf)
        d=np.einsum('ijk,k->ij',points[fv[begin:end]]-o,norm)
        hit=(d.min(axis=1)<=1e-11)&(d.max(axis=1)>=-1e-11)
        idx=np.flatnonzero(hit)+begin
        if len(idx):
            candidate.extend(c0[idx].tolist())
            candidate.extend(c1[idx][c1[idx]>0].tolist())
    cell_ids=np.unique(np.asarray(candidate,dtype=np.int64))
    if len(cell_ids)>max_candidate_cells:
        raise ValueError('Too many plane-cut tetra candidates; insufficient memory budget')
    # A compressed, sorted mapping is more memory-efficient than millions of lists.
    mask=c1>0
    owners=np.concatenate((c0,c1[mask]))
    face_ids=np.concatenate((np.arange(nf,dtype=np.int64),np.flatnonzero(mask).astype(np.int64)))
    signed=np.concatenate((np.ones(nf,dtype=np.int8),-np.ones(mask.sum(),dtype=np.int8)))
    order=np.argsort(owners,kind='stable')
    sorted_owners=owners[order]
    sorted_faces=face_ids[order]
    sorted_signs=signed[order]
    total=0.0;forward=0.0;reverse=0.0;area=0.0;used=0
    rejected=0
    for cid in cell_ids:
        begin=np.searchsorted(sorted_owners,cid,side='left')
        end=np.searchsorted(sorted_owners,cid,side='right')
        if end-begin!=4:
            rejected+=1;continue
        faces=sorted_faces[begin:end]
        vertices=np.unique(fv[faces])
        if len(vertices)!=4:
            rejected+=1;continue
        pts=points[vertices]
        outward=[]
        for f,sgn in zip(faces,sorted_signs[begin:end]):
            tris=set(fv[f].tolist())
            opp=[i for i in vertices if i not in tris]
            if len(opp)!=1:break
            outward.append((int(np.flatnonzero(vertices==opp[0])[0]),float(flux[f]*sgn)))
        if len(outward)!=4 or len({x[0] for x in outward})!=4:
            rejected+=1;continue
        vals=[0.0]*4
        for ix,value in outward:vals[ix]=value
        cut=section_flux(pts,vals,o,norm)
        if cut['area']<=0:continue
        total+=cut['net_flux']
        area+=cut['area']
        forward+=max(0.,cut['net_flux'])
        reverse+=max(0.,-cut['net_flux'])
        used+=1
    if rejected:
        # Partial section integration is physically meaningless: never report it.
        return {'status':'rejected_non_tetra_or_incomplete_face_mapping',
                'rejected_candidate_cells':rejected, 'cut_tetra_count':used,
                'net_flux_kg_s':None}
    return {'status':'oriented_rt0_plane_computed_not_wafer_partitioned',
            'cut_tetra_count':used,'section_area_m2':area,
            'net_flux_kg_s':total,'positive_kg_s':forward,
            'negative_magnitude_kg_s':reverse,
            'orientation':'explicit_source_verified_c0_outward__plane_normal='+repr(tuple(map(float,norm))),
            'physical_partition':'none; this is the supplied full plane, not wafer channels'}
