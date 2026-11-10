# -*- coding: utf-8 -*-
"""Map saved Fluent SV_FLUX face zones to global CFF Face IDs; check FVM closure.

No face-ID guesses and no interpolation of velocity. A single missing or
repeated face ID invalidates the *entire* native global map. This is read-only
and returns a small summary, not huge field arrays to the Win32 GUI.
"""

import math
from pathlib import Path

MAX_FULL_MAP_FACES = 24000000   # 192 MB output field; approx <= 2 GB temporary RAM
MAX_SINGLE_ZONE_FACES = 24000000


def native_global_face_flux(mesh, data_file, phase, zones, face_count, *, max_faces=MAX_FULL_MAP_FACES):
    import numpy as np
    n=int(face_count)
    if n<1 or n>max_faces:
        return None,{'status':'resource_limit_or_empty_mesh','face_count':n,
                     'max_supported_faces':max_faces}
    field=np.full(n,np.nan,dtype=np.float64)
    success=0;zone_count=0
    for zone in zones:
        sid=int(zone['id']) if isinstance(zone,dict) else int(zone)
        label=str(zone.get('name','')) if isinstance(zone,dict) else str(sid)
        try:
            a,b=map(int,mesh.get_surface_locs(sid))
        except Exception as exc:
            return None,{'status':'face_locs_unavailable','zone':label,'detail':str(exc)[:200]}
        count=b-a+1
        if a<1 or b>n or count<1 or count>MAX_SINGLE_ZONE_FACES:
            return None,{'status':'invalid_face_id_range','zone':label,'min_id':a,'max_id':b,'mesh_faces':n}
        segment=field[a-1:b]
        if np.isfinite(segment).any():
            return None,{'status':'overlapping_face_zone_ids','zone':label,'range':[a,b]}
        try:
            values=np.asarray(data_file.get_face_scalar_field_data(phase,'SV_FLUX',[sid]),dtype=np.float64).reshape(-1)
        except Exception as exc:
            return None,{'status':'native_flux_zone_read_failed','zone':label,'detail':str(exc)[:200]}
        if len(values)!=count:
            return None,{'status':'native_flux_face_length_mismatch','zone':label,
                         'expected':count,'actual':len(values)}
        if not np.isfinite(values).all():
            return None,{'status':'nonfinite_native_flux','zone':label}
        segment[:]=values
        success+=count;zone_count+=1
    if success!=n or not np.isfinite(field).all():
        return None,{'status':'unmapped_global_face_ids','mapped_faces':success,
                     'total_mesh_faces':n,'unmapped_faces':n-success}
    return field,{'status':'complete_original_sv_flux_global_face_id_map',
                  'mapped_faces':success,'face_zone_count':zone_count,
                  'signed_sum_all_faces_kg_s':float(field.sum(dtype=np.float64)),
                  'orientation':'not_yet_certified'}


def _read_integer_blocks(h5,root):
    import numpy as np
    import h5py
    if root not in h5:return None
    group=h5[root]
    if isinstance(group,h5py.Dataset):
        items=[group]
    elif isinstance(group,h5py.Group):
        keys=sorted(group.keys(),key=lambda x:(not x.isdigit(),int(x) if x.isdigit() else x))
        if not keys:return None
        items=[group[k] for k in keys]
    else:return None
    if any(not isinstance(a,h5py.Dataset) or a.ndim!=1 or a.dtype.kind not in 'iu' for a in items):
        return None
    return np.concatenate([np.asarray(a,dtype=np.int64).reshape(-1) for a in items])


def check_native_cell_closure(case_path, native_flux, *, max_cells=15000000):
    """Calculate conservative cell incidence balance if full c0/c1 is present.

    `SV_FLUX` sign is provisionally interpreted as outward of c0 and inward
    of c1. A low residual supports, but does NOT prove, this convention.
    """
    import numpy as np
    import h5py
    field=np.asarray(native_flux,dtype=np.float64).reshape(-1)
    with h5py.File(str(Path(case_path).resolve()),'r') as h:
        meshes=h.get('/meshes')
        if meshes is None or len(meshes)!=1:
            return {'status':'case_mesh_ambiguous'}
        root='/meshes/'+next(iter(meshes))+'/faces'
        c0=_read_integer_blocks(h,root+'/c0')
        c1=_read_integer_blocks(h,root+'/c1')
        if c0 is None or c1 is None or len(c0)!=len(field) or len(c1)!=len(field):
            return {'status':'face_cell_arrays_not_global_id_aligned',
                    'expected_faces':len(field),
                    'actual_c0':len(c0) if c0 is not None else None,
                    'actual_c1':len(c1) if c1 is not None else None}
        if c0.min()<1 or c1.min()<0 or np.any((c1>0)&(c0==c1)):
            return {'status':'invalid_cell_owner_ids'}
        ncell=int(max(c0.max(),c1.max()))
        if ncell>max_cells:
            return {'status':'cell_budget_exceeded','cell_count':ncell}
        # np.bincount uses native optimized vectorized accumulation, avoiding
        # one Python object per tetrahedron on 7M+ cell meshes.
        other=c1>0
        net=(np.bincount(c0,weights=field,minlength=ncell+1) -
             np.bincount(c1[other],weights=field[other],minlength=ncell+1))
        strength=(np.bincount(c0,weights=np.abs(field),minlength=ncell+1) +
                  np.bincount(c1[other],weights=np.abs(field[other]),minlength=ncell+1))
        counts=(np.bincount(c0,minlength=ncell+1)+
                np.bincount(c1[other],minlength=ncell+1))
        seen=counts[1:]>0
        cell_net=net[1:][seen]
        cell_strength=strength[1:][seen]
        # Relative imbalance against the sum of all incident face magnitudes.
        active=cell_strength>0
        relative=np.abs(cell_net[active])/cell_strength[active]
        if not len(relative):
            return {'status':'no_active_cells'}
        boundary_net=float(field[c1==0].sum(dtype=np.float64))
        boundary_abs=float(np.abs(field[c1==0]).sum(dtype=np.float64))
        return {'status':'candidate_fvm_cell_balance_from_original_sv_flux',
                'cell_count':ncell,'nonempty_cell_count':int(np.count_nonzero(seen)),
                'tetra_face_count_cells':int(np.count_nonzero(counts[1:]==4)),
                'active_cells':len(relative),
                'relative_cell_imbalance_p50':float(np.percentile(relative,50)),
                'relative_cell_imbalance_p95':float(np.percentile(relative,95)),
                'relative_cell_imbalance_p99':float(np.percentile(relative,99)),
                'absolute_cell_net_max_kg_s':float(np.max(np.abs(cell_net))),
                'boundary_signed_net_kg_s':boundary_net,
                'boundary_absolute_sum_kg_s':boundary_abs,
                'boundary_global_imbalance_pct':100*abs(boundary_net)/boundary_abs if boundary_abs>0 else None,
                'sign_assumption':'SV_FLUX positive outward of c0, inward of c1 — needs Fluent orientation check',
                'verification':'candidate_cell_incidence_only; source orientation and solution convergence not independently verified'}
