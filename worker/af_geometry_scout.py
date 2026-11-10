# -*- coding: utf-8 -*-
"""Bounded geometric feature scan for *future* non-11.9 ALD profiles.

This is not wafer classification. It finds repeated large, axis-aligned wall
stations that a model-specific geometry profile can later validate. It does
not assign any face flux to wafer/bypass regions or calculate a CV.
"""
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path


def scan_wall_stations(case_path, *, max_wall_sample=250000, chunk_faces=50000, max_nodes=15000000):
    import h5py
    import numpy as np
    result={'schema':'autofluent.geometry_candidates.v1',
            'status':'unavailable','source_case':str(Path(case_path).resolve()),
            'not_physical_result':True,'confidence':'unverified_candidates_only'}
    with h5py.File(case_path,'r') as cf:
        root='meshes/1'
        mandatory=[root+'/nodes/coords/4',root+'/faces/nodes/1']
        if any(key not in cf for key in mandatory):
            result['detail']='CFF node/face arrays missing; no geometry inferred'
            return result
        nodes_dataset=cf[root+'/nodes/coords/4']
        if nodes_dataset.ndim!=2 or nodes_dataset.shape[1]!=3 or len(nodes_dataset)>max_nodes:
            result['detail']='Node geometry exceeds limit or is not 3D; no geometry inferred'
            return result
        xyz=np.asarray(nodes_dataset[:],dtype=np.float64)
        if xyz.size<3 or not np.isfinite(xyz).all():
            result['detail']='Nonfinite or empty coordinate arrays'
            return result
        conn=cf[root+'/faces/nodes/1']
        if 'nodes' not in conn or 'nnodes' not in conn:
            result['detail']='Face node connectivity arrays missing'
            return result
        nnodes=conn['nnodes'];flat=conn['nodes']
        coords_min=xyz.min(axis=0); coords_max=xyz.max(axis=0)
        span=coords_max-coords_min;diag=float(np.linalg.norm(span))
        flat_tol=max(1e-9,diag*1e-8)
        station_tol=max(1e-7,diag*2e-7)
        top=cf.get(root+'/faces/zoneTopology')
        wall_zones=[]
        if top is not None and all(k in top for k in ('name','minId','maxId')):
            raw=top['name'][0]
            names=(raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)).split(';')
            lo=top['minId'][:];hi=top['maxId'][:]
            for i,name in enumerate(names):
                if ('wall' in name.lower() or '壁' in name) and i<len(lo):
                    start,end=int(lo[i])-1,int(hi[i])
                    if 0<=start<end<=len(nnodes):wall_zones.append((start,end,name))
        # For case variants with generic unnamed walls, the original 11.9
        # partition puts all wall faces before the first c1 internal face.
        if not wall_zones and root+'/faces/c1/1' in cf:
            item=cf[root+'/faces/c1/1']
            if 'minId' in item.attrs:
                first=int(item.attrs['minId'][0])-1
                if 0<first<=len(nnodes):wall_zones=[(0,first,'pre-internal-boundary-candidate')]
        if not wall_zones:
            result['detail']='No wall area scope identified from topology'
            return result
        wall_faces=sum(end-start for start,end,_ in wall_zones)
        if wall_faces<3:
            result['detail']='Insufficient wall faces'
            return result
        sample_step=max(1,math.ceil(wall_faces/max_wall_sample))
        groups=[defaultdict(lambda:[0.0,0,0.0,0.0]) for _ in range(3)]
        measured=0;nontriangle=0;out_of_bounds=0
        for start,end,_name in wall_zones:
            for a in range(start,end,chunk_faces):
                b=min(end,a+chunk_faces)
                cnt=np.asarray(nnodes[a:b],dtype=np.int64)
                if not np.all(cnt==3):
                    nontriangle+=int(np.count_nonzero(cnt!=3))
                    # Flat packed-node offsets are not 3*faceID when polygons
                    # occur. Refuse, do not guess.
                    result.update(status='unsupported_mixed_face_nodes',
                                  detail='Non-triangular wall faces need a general offset decoder',
                                  nontriangular_face_count=nontriangle)
                    return result
                ids=np.asarray(flat[3*a:3*b],dtype=np.int64).reshape(-1,3)-1
                take=np.arange(a,b,dtype=np.int64)%sample_step==0
                ids=ids[take]
                if ids.size==0:continue
                if ids.min()<0 or ids.max()>=len(xyz):
                    out_of_bounds+=1
                    result.update(status='node_id_misaligned',detail='Face nodes point outside coordinate rows')
                    return result
                pts=xyz[ids];cent=pts.mean(axis=1)
                raw_area=np.linalg.norm(np.cross(pts[:,1]-pts[:,0],pts[:,2]-pts[:,0]),axis=1)/2
                valid=np.isfinite(raw_area)&(raw_area>0)
                measured+=int(valid.sum())
                for axis in range(3):
                    eligible=valid & (np.ptp(pts[:,:,axis],axis=1)<=flat_tol)
                    if not eligible.any():continue
                    bins=np.rint(cent[eligible,axis]/station_tol).astype(np.int64)
                    uniq,reverse=np.unique(bins,return_inverse=True)
                    weights=raw_area[eligible]*sample_step
                    areas=np.bincount(reverse,weights=weights,minlength=len(uniq))
                    counts=np.bincount(reverse,minlength=len(uniq))
                    for position,key in enumerate(uniq):
                        entry=groups[axis][int(key)]
                        entry[0]+=float(areas[position]);entry[1]+=int(counts[position]);
                        entry[2]+=float(key)*station_tol*float(areas[position]);entry[3]+=float(areas[position])
        axes=[]
        for axis in range(3):
            stations=sorted((dict(position_m=round(key*station_tol,9),
                                  estimated_area_m2=value[0],sample_faces=value[1])
                             for key,value in groups[axis].items()
                             if value[0]>0),key=lambda z:z['estimated_area_m2'],reverse=True)
            if len(stations)>5000:
                stations=stations[:5000]
            if stations:
                median_area=float(np.median([s['estimated_area_m2'] for s in stations]))
                large=[s for s in stations if s['estimated_area_m2']>=min(max(median_area*2,stations[0]['estimated_area_m2']*.12),stations[0]['estimated_area_m2']*.75)]
            else:large=[]
            large.sort(key=lambda s:s['position_m'])
            diffs=np.diff([s['position_m'] for s in large]) if len(large)>1 else np.array([])
            axes.append({'axis':'XYZ'[axis],'station_count':len(stations),
                         'large_wall_station_count':len(large),
                         'top_wall_stations':stations[:40],
                         'large_station_positions_m':[s['position_m'] for s in large[:500]],
                         'large_station_spacing_median_m':float(np.median(diffs)) if len(diffs) else None})
        result.update(status='candidate_wall_station_histograms',
                      coordinate_min_m=coords_min.tolist(),coordinate_max_m=coords_max.tolist(),
                      sample_step=sample_step,sampled_wall_triangles=measured,
                      wall_zone_names=[name for _,_,name in wall_zones],
                      geometry_axes=axes,
                      guidance='Candidates only; group actual wafer patches by Y/Z, pair opposite X faces, then certify partition before producing flux')
        compact=json.dumps({'span':span.tolist(),'axes':axes},sort_keys=True,separators=(',',':'))
        result['feature_signature_not_mesh_hash']=hashlib.sha256(compact.encode()).hexdigest()
        return result


def save_geometry_candidates(case_path,output_dir):
    import time
    import uuid
    result=scan_wall_stations(case_path)
    dest=Path(output_dir).resolve();dest.mkdir(parents=True,exist_ok=True)
    path=dest/('geometry_candidates_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    return str(path),result
