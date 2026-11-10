# -*- coding: utf-8 -*-
"""Adaptive offline Fluent CFF native-face postprocessor.

* Reads saved .cas.h5 and .dat.h5, never requires a live Solver.
* Reuses Fluent's original oriented SV_FLUX, not velocity interpolation.
* Learns wafer stations from current wall geometry; no historical 11.9 sizes.
* Fail-closed for ambiguous layers, overlapping rows, mixed/polyhedral cells,
  missing native flux, absent cut faces, unsupported CFF block conventions.
* Emits new machine-readable artifacts with distinct candidate provenance.

For memory safety, only a single global CFF mesh/block with triangular faces and
four-face cells is supported at present. A successful calculation is *not* proof
of convergence, nor of validity for arbitrarily rotated/multiphase reactors.
"""
from __future__ import annotations
import csv
import hashlib
import os
import shutil
import tempfile
from html import escape
import json
from pathlib import Path
import re
import time
import uuid

from af_adaptive_geometry import GeometryAmbiguous, identify_from_wall_triangles, partition_cut_faces

# No hard face/wall-count gate: capacities are inferred from H5 metadata and
# configured memory budget before allocating. Signed 32-bit indexing is a
# separate actual representation constraint, never a nominal RAM threshold.
MAX_INDEX_ID=(1 << 31)-1
READ_BLOCK=131072


class UnsupportedCFF(ValueError):
    pass


def _exact_group(root, path, name='1'):
    import h5py
    if path not in root:
        raise UnsupportedCFF('Required CFF field missing: '+path)
    item=root[path]
    if isinstance(item,h5py.Dataset):return item
    if not isinstance(item,h5py.Group) or len(item)!=1:
        raise UnsupportedCFF('Ambiguous or multi-block CFF field: '+path)
    key=next(iter(item))
    ds=item[key]
    if not isinstance(ds,h5py.Dataset):raise UnsupportedCFF('CFF field is not a dataset: '+path)
    if not key.isdecimal():raise UnsupportedCFF('CFF block identity is not numeric: '+path)
    return ds




def _indexed_datasets(parent, *, total=None, allow_partial=False, context='CFF field'):
    """Discover ordered 1-based CFF HDF5 blocks without reading their values.

    Single full-length datasets may omit range attributes. Multi-block and
    partial datasets must declare minId/maxId; any overlap/gap fails closed.
    This function does not assume that the HDF5 group names equal Face IDs.
    """
    import h5py
    import numpy as np
    if isinstance(parent,h5py.Dataset):
        datasets=[parent]
    elif isinstance(parent,h5py.Group):
        datasets=[v for v in parent.values() if isinstance(v,h5py.Dataset)]
        if len(datasets)!=len(parent):
            raise UnsupportedCFF(context+': nested or ambiguous block content')
    else:raise UnsupportedCFF(context+': not a CFF dataset/group')
    if len(datasets)<1 or len(datasets)>512:raise UnsupportedCFF(context+': invalid number of blocks')
    result=[]
    for ds in datasets:
        if ds.ndim<1:raise UnsupportedCFF(context+': scalar block invalid')
        if 'minId' in ds.attrs and 'maxId' in ds.attrs:
            lo=int(np.asarray(ds.attrs['minId']).reshape(-1)[0])-1
            hi=int(np.asarray(ds.attrs['maxId']).reshape(-1)[0])
        elif len(datasets)==1 and total is not None and len(ds)==total:
            lo,hi=0,total
        elif len(datasets)==1 and total is None:
            lo,hi=0,len(ds)
        else:raise UnsupportedCFF(context+': segmented/partial block lacks explicit minId/maxId')
        if lo<0 or hi<=lo or hi-lo!=len(ds) or (total is not None and hi>total):
            raise UnsupportedCFF(context+': ID/length mismatch')
        result.append((lo,hi,ds))
    result.sort(key=lambda x:x[0])
    previous=0 if not allow_partial else result[0][0]
    for lo,hi,ds in result:
        if lo<previous:
            raise UnsupportedCFF(context+': overlapping block Face/Node IDs at %d'%(lo+1))
        if not allow_partial and lo!=previous:
            raise UnsupportedCFF(context+': missing block IDs at %d'%(previous+1))
        previous=hi
    if total is not None and not allow_partial and previous!=total:
        raise UnsupportedCFF(context+': missing trailing block IDs')
    return result


def _face_node_blocks(mesh,nfaces):
    """Return (lo,hi,flat_nodes_ds,nnodes_ds) for unique triangle-face ranges."""
    import h5py
    import numpy as np
    root=mesh.get('faces/nodes')
    if root is None:raise UnsupportedCFF('Missing faces/nodes')
    if 'nnodes' in root and 'nodes' in root:
        parts=[root]
    elif isinstance(root,h5py.Group):
        parts=list(root.values())
    else:raise UnsupportedCFF('Invalid faces/nodes topology')
    if not parts or len(parts)>512:raise UnsupportedCFF('Unsupported number of face/node blocks')
    grouped=[]
    for part in parts:
        if not isinstance(part,h5py.Group) or 'nodes' not in part or 'nnodes' not in part:
            raise UnsupportedCFF('Incomplete face/node block')
        n,ids=part['nnodes'],part['nodes']
        if n.ndim!=1 or ids.ndim!=1 or len(ids)!=len(n)*3:
            raise UnsupportedCFF('Face/node topology not triangular or invalid flat length')
        attrs=n.attrs if 'minId' in n.attrs and 'maxId' in n.attrs else part.attrs
        if 'minId' in attrs and 'maxId' in attrs:
            lo=int(np.asarray(attrs['minId']).reshape(-1)[0])-1
            hi=int(np.asarray(attrs['maxId']).reshape(-1)[0])
        elif len(parts)==1 and len(n)==nfaces:
            lo,hi=0,nfaces
        else:raise UnsupportedCFF('Multi-block faces/nodes requires Face ID metadata')
        if lo<0 or hi>nfaces or hi-lo!=len(n):
            raise UnsupportedCFF('Face/node ID/length mismatch')
        grouped.append((lo,hi,ids,n))
    grouped.sort(key=lambda x:x[0]);end=0
    for lo,hi,_,_ in grouped:
        if lo!=end:raise UnsupportedCFF('Missing/overlapping faces/nodes at face ID %d'%(end+1))
        end=hi
    if end!=nfaces:raise UnsupportedCFF('Incomplete global faces/nodes coverage')
    return grouped


def _read_c1_partial(parent,nfaces, *, chunk_size=131072):
    """Map disjoint interior face ranges into ordered face IDs and cell IDs."""
    import numpy as np
    blocks=_indexed_datasets(parent,total=nfaces,allow_partial=True,context='faces/c1')
    ranges=[];neighbors=[]
    for lo,hi,ds in blocks:
        for a in range(lo,hi,chunk_size):
            b=min(a+chunk_size,hi)
            vals=np.asarray(ds[a-lo:b-lo],dtype=np.int64)
            if np.any(vals>MAX_INDEX_ID):
                raise UnsupportedCFF('Face c1 cell ID exceeds signed 32-bit range')
            valid=vals>0
            if np.any(valid):
                ranges.append(np.arange(a,b,dtype=np.int32)[valid]);neighbors.append(vals[valid].astype(np.int32))
    if not ranges:raise UnsupportedCFF('No positive internal c1 cell owners')
    return np.concatenate(ranges),np.concatenate(neighbors)

def _face_zones(mesh,nfaces):
    import numpy as np
    path='faces/zoneTopology'
    if path not in mesh:raise UnsupportedCFF('Missing zoneTopology')
    z=mesh[path]
    for key in ('name','minId','maxId'):
        if key not in z:raise UnsupportedCFF('Missing zoneTopology/'+key)
    raw=z['name'][0]
    names=(raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)).split(';')
    lo=np.asarray(z['minId'][:],np.int64).reshape(-1)
    hi=np.asarray(z['maxId'][:],np.int64).reshape(-1)
    if len(names)!=len(lo) or len(lo)!=len(hi):
        raise UnsupportedCFF('Face-zone names/range mismatch')
    zones=[]
    for nm,start,end in zip(names,lo,hi):
        if start<1 or end<start or end>nfaces:raise UnsupportedCFF('Invalid zone face range')
        zones.append((str(nm),int(start)-1,int(end)))
    ranges=sorted((a,b) for _,a,b in zones)
    if not ranges or ranges[0][0]!=0 or ranges[-1][1]!=nfaces or any(a!=prev_end for (_,prev_end), (a,_) in zip(ranges,ranges[1:])):
        # Explicitly check no holes/overlaps rather than relying on zone order.
        raise UnsupportedCFF('Face-zone coverage is not a unique global ID partition')
    if len(zones)>10000:raise UnsupportedCFF('Too many face zones')
    return zones


def _subset_field(face_results,key,face_ids,nfaces):
    """Sparse dataset range lookup, face IDs are zero based and sorted."""
    import numpy as np
    import h5py
    if key not in face_results:return None
    data=face_results[key]
    dslist=[]
    if isinstance(data,h5py.Dataset):dslist=[data]
    elif isinstance(data,h5py.Group):
        if len(data)>256:return None
        dslist=[val for val in data.values() if isinstance(val,h5py.Dataset)]
    if len(dslist)==0:return None
    ids=np.asarray(face_ids,dtype=np.int64).reshape(-1)
    if len(ids)==0:return np.array([],dtype=float)
    order=np.argsort(ids)
    sorted_ids=ids[order]
    out=np.full(len(ids),np.nan)
    count=np.zeros(len(ids),dtype=np.uint8)
    for ds in dslist:
        if ds.ndim!=1: return None
        if 'minId' in ds.attrs:
            first=int(ds.attrs['minId'][0])-1
        elif len(ds)==nfaces:first=0
        else:return None
        if 'maxId' in ds.attrs and int(ds.attrs['maxId'][0])-first!=len(ds):return None
        last=first+len(ds)
        a=int(np.searchsorted(sorted_ids,first,side='left'))
        b=int(np.searchsorted(sorted_ids,last,side='left'))
        if a==b:continue
        take=sorted_ids[a:b]-first
        # Data segments may be huge; only materialize the tight requested span.
        low,high=int(take[0]),int(take[-1])+1
        # Sparse measurement faces may straddle an enormous field segment;
        # do not read the entire span just to extract a handful of IDs.
        pos=a
        while pos<b:
            stop=pos+1
            while stop<b and (sorted_ids[stop]-sorted_ids[pos])<65536:
                stop+=1
            block_lo=int(sorted_ids[pos]-first)
            block_hi=int(sorted_ids[stop-1]-first)+1
            part=np.asarray(ds[block_lo:block_hi],dtype=float)
            out[order[pos:stop]]=part[sorted_ids[pos:stop]-first-block_lo]
            count[order[pos:stop]]+=1
            pos=stop
    if not np.all(count==1) or not np.isfinite(out).all():return None
    return out


def _read_native_full_flux(fields, nfaces, *, chunk_size=131072, validate_only=False):
    """Identify one exact non-overlapping SV_FLUX Face ID partition.

    Multiple CFF HDF5 blocks are supported. Only bounded slices are read;
    incomplete, duplicated, ambiguous or nonfinite ranges fail closed.
    """
    import h5py
    import numpy as np
    if 'SV_FLUX' not in fields:
        raise UnsupportedCFF('Saved results missing native face SV_FLUX')
    obj=fields['SV_FLUX']
    if isinstance(obj,h5py.Dataset):blocks=[obj]
    elif isinstance(obj,h5py.Group):
        blocks=list(obj.values())
    else:raise UnsupportedCFF('Invalid SV_FLUX field object')
    if not blocks or len(blocks)>512 or any(not isinstance(x,h5py.Dataset) for x in blocks):
        raise UnsupportedCFF('SV_FLUX has no valid bounded block list')
    ranges=[]
    for ds in blocks:
        if ds.ndim!=1 or 'minId' not in ds.attrs or 'maxId' not in ds.attrs:
            raise UnsupportedCFF('SV_FLUX block missing explicit 1-based Face ID interval')
        lo=int(np.asarray(ds.attrs['minId']).reshape(-1)[0])-1
        hi=int(np.asarray(ds.attrs['maxId']).reshape(-1)[0])
        if lo<0 or hi>nfaces or hi-lo!=len(ds):
            raise UnsupportedCFF('SV_FLUX block ID/length mismatch')
        ranges.append((lo,hi,ds))
    ranges.sort(key=lambda x:x[0])
    end=0
    for lo,hi,ds in ranges:
        if lo!=end:
            raise UnsupportedCFF('SV_FLUX Face ID coverage has a gap or overlap at %d' %(end+1))
        end=hi
    if end!=nfaces:
        raise UnsupportedCFF('SV_FLUX blocks do not cover all original Face IDs')
    evidence=[{'min_face_id':lo+1,'max_face_id':hi,'count':hi-lo} for lo,hi,_ in ranges]
    if validate_only:
        # Search result groups by dataset metadata only. Read actual values
        # exactly once, after selecting the unique qualifying result/phase.
        return None,evidence
    flux=np.empty(nfaces,dtype=np.float64)
    for lo,hi,ds in ranges:
        for start in range(0,len(ds),chunk_size):
            stop=min(start+chunk_size,len(ds))
            flux[lo+start:lo+stop]=ds[start:stop]
    if not np.isfinite(flux).all():
        raise UnsupportedCFF('Nonfinite original SV_FLUX in one or more blocks')
    return flux,evidence


def _select_face_results(df, nfaces):
    """Resolve the *unique* phase/result group that covers all original faces."""
    if 'results' not in df:
        raise UnsupportedCFF('Missing CFF results group')
    matches=[];reasons=[]
    for result_id,result in df['results'].items():
        for phase_id,phase in result.items():
            if not phase_id.startswith('phase-') or 'faces' not in phase:
                continue
            fields=phase['faces']
            if 'SV_FLUX' not in fields:continue
            try:
                _,evidence=_read_native_full_flux(fields,nfaces,validate_only=True)
                matches.append((str(result_id),str(phase_id),fields,evidence))
            except (UnsupportedCFF,ValueError) as exc:
                reasons.append('%s/%s: %s' %(result_id,phase_id,exc))
    if len(matches)!=1:
        if not matches:
            raise UnsupportedCFF('No full original SV_FLUX result/phase match: '+('; '.join(reasons[:4]) or 'none exposed'))
        raise UnsupportedCFF('Multiple complete SV_FLUX result/phase candidates; choose snapshot explicitly instead of guessing: '+
                             ', '.join('%s/%s' %(r,p) for r,p,_,_ in matches))
    return matches[0]


def _read_cff(case,data, *, memory_budget_mb=6144):
    """Read supported tetra CFF geometry with independently indexed H5 blocks.

    Strict ID validation precedes significant allocation. Bounded slices only;
    this is still a NumPy in-memory engine and does not certify arbitrary 32GB
    inputs or mixed/polyhedral meshes.
    """
    import h5py
    import numpy as np
    from af_progress import update as progress,portion
    progress(10,'H5 元数据预检','核对 Case/Data、结果组及几何 Face ID 范围')
    cf=h5py.File(case,'r');df=h5py.File(data,'r')
    try:
        if 'settings/Case File' not in df:raise UnsupportedCFF('Data has no internal Case File reference')
        raw=df['settings/Case File'][0]
        name=(raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)).replace('\\','/').split('/')[-1]
        if name!=Path(case).name:raise UnsupportedCFF('Case/Data pair identity does not match')
        if 'meshes' not in cf or len(cf['meshes'])!=1:
            raise UnsupportedCFF('Need one Fluent mesh')
        mesh=cf['meshes'][next(iter(cf['meshes']))]
        if 'nodes/coords' not in mesh:raise UnsupportedCFF('Missing node coordinates')
        coord_blocks=_indexed_datasets(mesh['nodes/coords'],context='nodes/coords')
        if any(ds.ndim!=2 or ds.shape[1]!=3 for _,_,ds in coord_blocks):
            raise UnsupportedCFF('Unsupported node coordinate shape')
        node_count=coord_blocks[-1][1]
        if node_count>MAX_INDEX_ID:raise UnsupportedCFF('Node IDs exceed signed 32-bit supported representation')
        nf=0
        if 'faces/c0' not in mesh:raise UnsupportedCFF('Missing faces/c0')
        # Discover full c0 Face IDs to establish the authoritative global count.
        c0_obj=mesh['faces/c0']
        c0_blocks=_indexed_datasets(c0_obj,context='faces/c0')
        nf=c0_blocks[-1][1]
        if nf<1 or nf>MAX_INDEX_ID:raise UnsupportedCFF('Face IDs exceed signed 32-bit supported representation')
        # Re-read indexed intervals against authoritative total, rejecting
        # noncontiguous/overlapping ranges before allocating large arrays.
        c0_blocks=_indexed_datasets(c0_obj,total=nf,context='faces/c0')
        node_blocks=_face_node_blocks(mesh,nf)
        if 'faces/c1' not in mesh:raise UnsupportedCFF('Missing faces/c1')
        _indexed_datasets(mesh['faces/c1'],total=nf,allow_partial=True,context='faces/c1')
        if not (512 <= memory_budget_mb <= 16384):
            raise UnsupportedCFF('memory budget must be 512..16384 MiB')
        # Actual array model: 3x float64 node coords; 3x int32 triangle
        # indices; int32 c0/c1 IDs; float64 original flux; per-cell centres,
        # residual/degree; bounded 131072-face read/scratch buffers. A safety
        # multiplier covers temporary masks, wall candidates and H5 caching.
        # This is an estimate, NEVER a measured RSS or an assurance of success.
        reported_cell_max=mesh.get('cells/zoneTopology/maxId')
        nc_hint=int(np.max(reported_cell_max[:])) if reported_cell_max is not None else max(1,(nf//3))
        if nc_hint>MAX_INDEX_ID:raise UnsupportedCFF('Cell IDs exceed signed 32-bit supported representation')
        wall_face_hint=sum(b-a for name,a,b in _face_zones(mesh,nf)
                           if 'wall' in name.lower() or '壁' in name)
        base_bytes=(node_count*3*8 + nf*(3*4+4+8) +
                    min(nf,nc_hint*4)*8 + nc_hint*(3*8+4+8+1))
        # Wall triangles are prefiltered in bounded batches; reserve an upper
        # bound of 80 bytes per wall triangle plus implementation scratch.
        projected_bytes=int(base_bytes*1.65+min(wall_face_hint,nf)*80+256*1024**2)
        if projected_bytes>memory_budget_mb*1024**2:
            raise UnsupportedCFF(
                'Resource preflight blocked at topology stage: faces=%d, wall_faces=%d, nodes=%d, '
                'cells_hint=%d, estimated_peak=%.2f GiB, budget=%.2f GiB; '
                'increase budget only after memory measurement or reduce working set'
                %(nf,wall_face_hint,node_count,nc_hint,projected_bytes/1024**3,memory_budget_mb/1024))
        result_id,phase_id,fields,_=_select_face_results(df,nf)
        progress(17,'网格节点分块读取','验证唯一结果组 %s/%s；读取真实节点坐标'%(result_id,phase_id))
        xyz=np.empty((node_count,3),dtype=np.float64)
        for lo,hi,ds in coord_blocks:
            for start in range(lo,hi,131072):
                stop=min(start+131072,hi)
                xyz[start:stop]=ds[start-lo:stop-lo]
                portion(17,29,stop,node_count,'节点坐标','已读取 %d/%d 个节点'%(stop,node_count))
        if not np.isfinite(xyz).all():raise UnsupportedCFF('Nonfinite node coordinates')
        progress(29,'面拓扑分块读取','逐块验证三角面节点数、节点索引及 Face ID')
        conn=np.empty((nf,3),dtype=np.int32)
        for lo,hi,nds,nnds in node_blocks:
            for start in range(lo,hi,131072):
                stop=min(start+131072,hi)
                if not np.all(nnds[start-lo:stop-lo]==3):
                    raise UnsupportedCFF('Mixed/nontriangular faces need another topology adapter')
                block=np.asarray(nds[3*(start-lo):3*(stop-lo)],dtype=np.int64).reshape(-1,3)-1
                if np.any(block<0) or np.any(block>=node_count):
                    raise UnsupportedCFF('Face/node connection contains invalid node ID at face %d'%(start+1))
                conn[start:stop]=block.astype(np.int32)
                portion(29,42,stop,nf,'三角面连接','已核对 %d/%d 个原生面'%(stop,nf))
        if conn.min()<0 or conn.max()>=len(xyz):raise UnsupportedCFF('Node IDs not aligned')
        progress(42,'原生邻接关系','读取 c0/c1 单元并检查分段区间唯一性')
        owner=np.empty(nf,dtype=np.int32)
        for lo,hi,ds in c0_blocks:
            for start in range(lo,hi,131072):
                stop=min(start+131072,hi)
                block=np.asarray(ds[start-lo:stop-lo],dtype=np.int64)
                if np.any(block<=0) or np.any(block>MAX_INDEX_ID):
                    raise UnsupportedCFF('Face c0 cell ID out of range at face %d'%(start+1))
                owner[start:stop]=block.astype(np.int32)
                portion(42,48,stop,nf,'面单元邻接 c0','已读取 %d/%d 个面'%(stop,nf))
        if owner.min()<1:raise UnsupportedCFF('Global c0 face map invalid')
        inside,neighbors=_read_c1_partial(mesh['faces/c1'],nf)
        nc=int(max(owner.max(),neighbors.max()))
        if nc>MAX_INDEX_ID:raise UnsupportedCFF('Cell IDs exceed signed 32-bit supported representation')
        if np.any(owner[inside]==neighbors):raise UnsupportedCFF('Face has same c0 and c1')
        nc_ds=mesh.get('cells/zoneTopology/maxId')
        if nc_ds is not None and int(np.max(nc_ds[:]))!=nc:
            raise UnsupportedCFF('Cell maximum ID and owner map disagree')
        progress(51,'原生质量通量','校验分段 SV_FLUX 的每段数据、有限值与 Face ID 对齐')
        flux,flux_ranges=_read_native_full_flux(fields,nf)
        progress(59,'边界区与壁面','审核端口、壁面是否误包含内部面')
        zones=_face_zones(mesh,nf)
        walls=[np.arange(a,b,dtype=np.int64) for name,a,b in zones if 'wall' in name.lower() or '壁' in name]
        fallback=False
        if not walls:
            start=int(inside.min())
            if start<1:raise UnsupportedCFF('Cannot infer physical wall face scope')
            walls=[np.arange(0,start,dtype=np.int64)];fallback=True
        wall_ids=np.concatenate(walls)
        internal_flags=np.zeros(nf,dtype=bool)
        internal_flags[inside]=True
        for name,a,b in zones:
            label=name.lower().strip()
            if ('wall' in label or '壁' in label or
                    re.search(r'(^|[-_\s])(inlet|outlet)($|[-_\s\d])',label)):
                if internal_flags[a:b].any():
                    raise UnsupportedCFF('Named wall or inlet/outlet zone contains internal c1 faces: '+name)
        if internal_flags[wall_ids].any():raise UnsupportedCFF('Wafer wall candidate includes internal faces')
        # The H5 resource preflight covers all wall faces. Geometry scanning
        # is chunked and retains X-normal candidates only, not the entire
        # multi-million-triangle physical wall surface.
        return cf,df,{'xyz':xyz,'conn':conn,'owner':owner,'inside':inside,'neighbors':neighbors,
                      'nc':nc,'nf':nf,'flux':flux,'zones':zones,'fields':fields,
                      'result_group':result_id,'phase_group':phase_id,
                      'flux_block_ranges':flux_ranges,'memory_budget_mb':memory_budget_mb,
                      'topology_block_counts':{'nodes':len(coord_blocks),'face_nodes':len(node_blocks),
                                               'c0':len(c0_blocks),'c1':len(_indexed_datasets(mesh['faces/c1'],total=nf,allow_partial=True,context='faces/c1'))},
                      'projected_peak_memory_mb_not_measured':round(projected_bytes/1024**2,2),
                      'wall_ids':wall_ids,'wall_zone_fallback':fallback}
    except BaseException:
        df.close();cf.close();raise


def _wafer_wall_triangles(mesh,config=None, *,chunk_size=READ_BLOCK):
    """Bounded wall scan; retain only measured X-normal triangle candidates.

    The full wall mesh (which may exceed 3 million triangles) is never gathered
    into one temporary. This is still a candidate-based method, not a proof of
    wafer identity; downstream station and geometry checks are mandatory.
    """
    import numpy as np
    xyz=mesh['xyz'];conn=mesh['conn'];wall=mesh['wall_ids']
    scale=float(np.linalg.norm(np.ptp(xyz,axis=0)))
    tol=max(2e-8,scale*2e-7)
    if config:
        bands=[(tuple(layer['y_range_m']),layer.get('wafer_x_range_m'))
               for layer in config['layers']]
    else:
        bands=[]
    candidates=[];retained=0
    for i in range(0,len(wall),chunk_size):
        indices=wall[i:i+chunk_size]
        verts=xyz[conn[indices]]
        select=np.ptp(verts[:,:,0],axis=1)<=tol
        if bands:
            ctr=verts.mean(axis=1)
            spatial=np.zeros(len(ctr),dtype=bool)
            for (lo,hi),xrange in bands:
                choose=(ctr[:,1]>=lo)&(ctr[:,1]<=hi)
                if xrange is not None:
                    choose &= (ctr[:,0]>=xrange[0]-tol)&(ctr[:,0]<=xrange[1]+tol)
                spatial |= choose
            select &= spatial
        if np.any(select):
            block=verts[select].copy()
            candidates.append(block)
            retained+=len(block)
    if retained<50:
        raise GeometryAmbiguous('Wall scan found too few X-normal candidate triangles; verify wafer normal and layer bounds')
    # Candidate count is real measured geometry, never expected count from JSON.
    return np.concatenate(candidates,axis=0)


def _cell_centroids(mesh):
    """Four triangular-face centroid average; no tetra/cell Python objects."""
    import numpy as np
    nf,nc=mesh['nf'],mesh['nc']
    xyz=mesh['xyz'];conn=mesh['conn'];owner=mesh['owner']
    inside=mesh['inside'];neighbor=mesh['neighbors']
    counts=np.zeros(nc+1,dtype=np.uint8)
    # Chunked indexed scatter avoids O(ncells) temporary bincount arrays
    # repeatedly allocated for every 150k-face block.
    center=np.zeros((nc+1,3),dtype=np.float64)
    for a in range(0,nf,READ_BLOCK):
        b=min(a+READ_BLOCK,nf)
        mid=xyz[conn[a:b]].mean(axis=1)
        np.add.at(counts,owner[a:b],1)
        np.add.at(center,owner[a:b],mid)
        lo=int(np.searchsorted(inside,a,side='left'))
        hi=int(np.searchsorted(inside,b,side='left'))
        if hi>lo:
            fids=inside[lo:hi]-a
            np.add.at(counts,neighbor[lo:hi],1)
            np.add.at(center,neighbor[lo:hi],mid[fids])
    if np.any(counts[1:]!=4):
        raise UnsupportedCFF('Cell degree != 4: not an all-tetrahedral mesh')
    center[1:]/=4
    if not np.isfinite(center[1:]).all():raise UnsupportedCFF('Nonfinite CFF tetra centroids')
    return center



def _tetra_connectivity_spotcheck(mesh, *, limit=256):
    """Bounded, deterministic *sample*, not full-mesh tetra certification.

    Four faces per cell is necessary but insufficient. Spot-check node closure
    and signed nonzero tetra volume to catch corrupted c0/c1/face-node maps.
    Streaming face chunks prevents memory use from growing with face count.
    """
    import itertools
    import numpy as np
    nc=mesh['nc']
    selected=np.unique(np.linspace(1,nc,num=min(nc,limit),dtype=np.int64))
    face_sets={int(cid):[] for cid in selected}
    owner=mesh['owner'];internal=mesh['inside'];other=mesh['neighbors']
    nodes=mesh['conn'];xyz=mesh['xyz']
    for start in range(0,mesh['nf'],200000):
        stop=min(start+200000,mesh['nf'])
        lo=np.searchsorted(internal,start)
        hi=np.searchsorted(internal,stop)
        sources=[(np.arange(start,stop,dtype=np.int64),owner[start:stop]),
                 (internal[lo:hi],other[lo:hi])]
        for ids,cids in sources:
            loc=np.searchsorted(selected,cids)
            valid=(loc<len(selected))
            valid[valid]&=(selected[loc[valid]]==cids[valid])
            for fid,cid in zip(ids[valid],cids[valid]):
                face_sets[int(cid)].append(tuple(sorted(int(i) for i in nodes[fid])))
    for cid,faces in face_sets.items():
        verts=sorted(set(itertools.chain.from_iterable(faces)))
        if len(faces)!=4 or len(verts)!=4:
            raise UnsupportedCFF('Sample cell %d does not have four faces on four distinct vertices'%cid)
        expected=set(itertools.combinations(verts,3))
        if set(faces)!=expected:
            raise UnsupportedCFF('Sample cell %d triangular faces do not close a tetrahedron'%cid)
        v=xyz[verts]
        v6=abs(float(np.dot(v[1]-v[0],np.cross(v[2]-v[0],v[3]-v[0]))))
        scale=float(np.max(np.ptp(v,axis=0)))
        if not np.isfinite(v6) or scale<=0 or v6<=max(1e-36,scale**3*1e-12):
            raise UnsupportedCFF('Sample cell %d tetrahedron is degenerate or nearly flat'%cid)
    return {'status':'sampled_tetra_node_and_volume_consistent',
            'sampled_cells':len(selected),'total_cells':nc,
            'limitation':'Deterministic sample; cannot certify every mesh cell'}


def _region_flow(mesh,centroids,layer,entry, *, y_offset=0):
    import numpy as np
    inside=mesh['inside'];c0=mesh['owner'][inside];c1=mesh['neighbors'];flux=mesh['flux']
    ycut=float(layer['y_cut_m'])+y_offset
    lower=centroids[:,1]<ycut
    crossing=lower[c0]!=lower[c1]
    ids=inside[crossing]
    if len(ids)<3:raise GeometryAmbiguous('No native internal faces cross candidate wafer layer plane')
    signed=flux[ids]*np.where(lower[c1[crossing]],1,-1)
    points=np.empty((len(ids),3),dtype=float)
    for a in range(0,len(ids),100000):
        b=min(a+100000,len(ids))
        points[a:b]=mesh['xyz'][mesh['conn'][ids[a:b]]].mean(axis=1)
    result=partition_cut_faces(points,signed,layer,min_faces_per_channel=1)
    result.update(cut_height_m=ycut,cut_face_count=len(ids))
    return result,ids,lower,signed


def _cut_height_sensitivity(mesh, centroids, layer, baseline, inlet_flux,
                            *, offset_m=.001, face_budget=2000000):
    """Test two nearby native staircase cuts WITHOUT treating closure as physics validation.

    Large CFD cases already read several GB of arrays: respect a bounded
    second-pass budget. An unavailable test is explicit, never called stable.
    """
    if mesh['nf']>face_budget:
        return {'status':'skipped_resource_budget','reason':'Rebuilding +/-1 mm cuts would exceed extra-pass face budget',
                'offset_m':offset_m,'face_budget':face_budget}
    y=float(layer['y_cut_m'])
    if not (float(layer['y_min_m'])+offset_m<y<float(layer['y_max_m'])-offset_m):
        return {'status':'unavailable_layer_geometry','reason':'One or both shifted cuts would fall outside recognized wafer layer',
                'offset_m':offset_m}
    records=[]
    for shift in (-offset_m,offset_m):
        try:
            measured,_,_,_=_region_flow(mesh,centroids,layer,None,y_offset=shift)
            if measured['channel_count']!=baseline['channel_count'] or measured['cv_pct'] is None:
                raise GeometryAmbiguous('Shifted cut changed recognized channel count or undefined CV')
        except (GeometryAmbiguous,UnsupportedCFF,ValueError) as exc:
            return {'status':'unable_to_validate_shifted_cut','offset_m':offset_m,
                    'failed_shift_m':shift,'reason':str(exc)[:250]}
        records.append({'offset_m':shift,'cut_face_count':measured['cut_face_count'],
                        'cv_pct':measured['cv_pct'],
                        'wafer_share_inlet_pct':100*measured['parts_net_kg_s']['wafer']/inlet_flux,
                        'central_share_inlet_pct':100*measured['parts_net_kg_s']['central']/inlet_flux,
                        'whole_cut_kg_s':measured['whole_cut_kg_s']})
    base_wafer=100*baseline['parts_net_kg_s']['wafer']/inlet_flux
    base_central=100*baseline['parts_net_kg_s']['central']/inlet_flux
    cv_delta=max(abs(r['cv_pct']-baseline['cv_pct']) for r in records)
    wafer_delta=max(abs(r['wafer_share_inlet_pct']-base_wafer) for r in records)
    central_delta=max(abs(r['central_share_inlet_pct']-base_central) for r in records)
    # These are advisory review limits, not CFD convergence thresholds.
    unstable=cv_delta>1 or wafer_delta>1 or central_delta>1
    return {'status':'sensitive_candidate_needs_review' if unstable else 'stable_within_1pp_advisory_only',
            'offset_m':offset_m,'shifted_cut_results':records,
            'max_cv_delta_pp':cv_delta,'max_wafer_share_delta_pp':wafer_delta,
            'max_central_share_delta_pp':central_delta,
            'warning':'Shifted staircase-cut agreement is not proof of correct geometrical partition or solver convergence'}


def _global_balance(mesh,centroids,lower,ids,signed):
    import numpy as np
    owner=mesh['owner'];inside=mesh['inside'];neighbors=mesh['neighbors'];flux=mesh['flux'];nc=mesh['nc']
    # Sum directly into one residual buffer; avoid two nc-sized bincount
    # outputs and full temporary flux[inside] during large-mesh analysis.
    residual=np.zeros(nc+1,dtype=np.float64)
    for a in range(0,mesh['nf'],READ_BLOCK):
        b=min(a+READ_BLOCK,mesh['nf'])
        np.add.at(residual,owner[a:b],flux[a:b])
        lo=int(np.searchsorted(inside,a));hi=int(np.searchsorted(inside,b))
        if hi>lo:
            np.add.at(residual,neighbors[lo:hi],-flux[inside[lo:hi]])
    is_boundary=np.ones(mesh['nf'],dtype=bool);is_boundary[inside]=False
    outflow=float(flux[is_boundary&lower[owner]].sum(dtype=np.float64))
    inside_residual=float(residual[lower].sum(dtype=np.float64))
    cross=float(signed.sum(dtype=np.float64))
    err=outflow-cross-inside_residual
    if abs(err)>max(3e-12,abs(outflow)*1e-7):
        raise UnsupportedCFF('Control-volume face-flux accounting does not close')
    return {'external_boundary_net_kg_s':outflow,'cut_net_kg_s':cross,
            'cell_residual_below_cut_kg_s':inside_residual,
            'algebraic_closure_error_kg_s':float(err),
            'warning':'Algebraic closure is not an independent convergence test'}


def _connected_boundary_ports(mesh, face_ids, kind, *, max_faces=300000):
    """Split a Fluent inlet/outlet *zone* into physical connected components.

    A single named 'inlet' may contain 3 disconnected actual ports. Shared
    Fluent node identity, not a geometric coordinate tolerance, defines the
    graph. Read-only; sorted by X center after grouping.
    """
    import numpy as np
    faces=np.asarray(face_ids,dtype=np.int64).reshape(-1)
    if len(faces)>max_faces:
        raise UnsupportedCFF('Too many faces in named port component analysis')
    conn=mesh['conn'][faces]
    parent=np.arange(len(faces),dtype=np.int32)
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return int(i)
    by_node={}
    for i,tri in enumerate(conn):
        for node in tri:
            nid=int(node)
            if nid in by_node:
                parent[find(i)]=find(by_node[nid])
            else:by_node[nid]=i
    grouped={}
    for i in range(len(faces)):
        grouped.setdefault(find(i),[]).append(i)
    flux=mesh['flux']
    sign=-1 if kind=='inlet' else 1
    ports=[]
    for indices in grouped.values():
        local=np.asarray(indices,dtype=np.int64)
        ids=faces[local]
        v=sign*flux[ids]
        center=np.mean(mesh['xyz'][conn[local]].mean(axis=1),axis=0)
        ports.append({'face_count':len(local),'center_m':center.tolist(),
                      'net_kg_s':float(v.sum(dtype=np.float64)),
                      'positive_kg_s':float(v[v>0].sum(dtype=np.float64)),
                      'negative_magnitude_kg_s':float(-v[v<0].sum(dtype=np.float64))})
    ports.sort(key=lambda p:tuple(p['center_m']))
    for i,p in enumerate(ports,1):p['physical_port_index']=i
    return ports


def _port_flux(mesh, port_config=None):
    import numpy as np
    zones=mesh['zones'];flux=mesh['flux'];nf=mesh['nf']
    inlet=[];outlet=[];details=[]
    defined_in=set(x.lower() for x in port_config['inlet_zones']) if port_config else set()
    defined_out=set(x.lower() for x in port_config['outlet_zones']) if port_config else set()
    recognized_in=set();recognized_out=set()
    for name,a,b in zones:
        label=name.lower().strip()
        if port_config:
            kind='inlet' if label in defined_in else ('outlet' if label in defined_out else None)
        else:
            kind='inlet' if re.search(r'(^|[-_\s])inlet($|[-_\s\d])',label) else (
                'outlet' if re.search(r'(^|[-_\s])outlet($|[-_\s\d])',label) else None)
        if kind=='inlet':recognized_in.add(label)
        if kind=='outlet':recognized_out.add(label)
        if kind is None:continue
        ids=np.arange(a,b,dtype=np.int64)
        signed=flux[a:b]*(-1 if kind=='inlet' else 1)
        if len(ids)<=300000:
            physical=_connected_boundary_ports(mesh,ids,kind)
        else:
            physical=[]
        details.append({'zone':name,'kind':kind,'face_count':b-a,'physical_ports':physical,
                        'positive_kg_s':float(signed[signed>0].sum()),
                        'negative_magnitude_kg_s':float(-signed[signed<0].sum()),
                        'net_kg_s':float(signed.sum())})
        (inlet if kind=='inlet' else outlet).append(ids)
    if port_config and (recognized_in!=defined_in or recognized_out!=defined_out):
        raise UnsupportedCFF('Configured inlet/outlet zones not found: '+
                             str(sorted((defined_in-recognized_in)|(defined_out-recognized_out))))
    if not inlet or not outlet:raise UnsupportedCFF('No explicit inlet/outlet zone names to establish flux direction')
    inp=np.concatenate(inlet);out=np.concatenate(outlet)
    mass_in=float((-flux[inp]).sum(dtype=np.float64));mass_out=float(flux[out].sum(dtype=np.float64))
    if mass_in<=0 or mass_out<=0:raise UnsupportedCFF('Input/output native flux sign inconsistent')
    # A seemingly excellent inlet/outlet mass balance is meaningless if a
    # pressure vent, unnamed opening or other boundary carries appreciable
    # mass.  Audit EVERY external face, not just the port zones, before
    # reporting a two-boundary mass imbalance. This is distinct from cell
    # residual/algebraic closure; one cannot certify the other.
    is_external=np.ones(nf,dtype=bool)
    is_external[mesh['inside']]=False
    is_named_port=np.zeros(nf,dtype=bool)
    is_named_port[inp]=True
    is_named_port[out]=True
    if np.any(~is_external[is_named_port]):
        raise UnsupportedCFF('Inlet/outlet includes internal CFF faces')
    extra=is_external & ~is_named_port
    extra_flux=flux[extra]
    extra_abs=float(np.abs(extra_flux).sum(dtype=np.float64))
    extra_net=float(extra_flux.sum(dtype=np.float64))
    extra_threshold=max(1e-12,mass_in*1e-4)  # 0.01% inlet: review gate, not convergence criterion
    if extra_abs>extra_threshold:
        raise UnsupportedCFF('Significant non-inlet/outlet boundary flux: '
                             'sum(abs(SV_FLUX))=%.9g kg/s (%.6g%% inlet); '
                             'boundary labels or open vents must be reviewed'
                             %(extra_abs,100*extra_abs/mass_in))
    external_net=float(flux[is_external].sum(dtype=np.float64))
    return {'inlet_kg_s':mass_in,'outlet_kg_s':mass_out,
            'mass_imbalance_pct':100*abs(mass_in-mass_out)/mass_in,
            'external_boundary_signed_sum_kg_s':external_net,
            'other_external_absolute_flux_kg_s':extra_abs,
            'other_external_signed_flux_kg_s':extra_net,
            'other_external_acceptance_threshold_kg_s':extra_threshold,
            'other_external_face_count':int(extra.sum()),
            'external_face_audit':'checked_all_non_inlet_outlet_external_faces',
            'boundary_zones':details},inp,out


def _total_pressure(mesh,inlets,outlets):
    import numpy as np
    fields=mesh['fields'];ids=np.concatenate([inlets,outlets]);nf=mesh['nf']
    vals={key:_subset_field(fields,key,ids,nf) for key in ('SV_P','SV_DENSITY','SV_U','SV_V','SV_W')}
    if any(v is None for v in vals.values()):return {'status':'unavailable','reason':'Pressure/density/velocity face fields not complete'}
    density=vals['SV_DENSITY']; speed_sq=sum(vals[k]**2 for k in ('SV_U','SV_V','SV_W'))
    if np.any(density<=0):return {'status':'unavailable','reason':'Invalid face density'}
    # The p + rho*v²/2 proxy requires a low-Mach assumption. Check it
    # explicitly when saved temperature is present. Nitrogen constants are a
    # *screening assumption*, never a substitute for the actual Case material.
    status='computed_pressure_proxy_mach_unverified'
    mach_note='Temperature or gas properties not independently available; Mach condition unverified'
    temp=_subset_field(fields,'SV_T',ids,nf)
    mach_squared_proxy=None
    if temp is not None and np.all(np.isfinite(temp)) and np.all(temp>0):
        mach_squared_proxy=float(np.max(speed_sq/(1.4*296.8*temp)))
        if mach_squared_proxy>=.01:
            return {'status':'unavailable_high_mach_proxy',
                    'reason':'N2-reference Mach screening exceeds 0.1; do not use incompressible p+rho*v²/2 as stagnation pressure',
                    'mach_squared_proxy_n2':mach_squared_proxy}
        status='computed_pressure_proxy_n2_mach_screened'
        mach_note='Mach²<0.01 assuming N2 gamma=1.4, R=296.8 J/(kg K); actual material not verified'
    total=vals['SV_P']+.5*density*speed_sq
    w=np.abs(mesh['flux'][ids]);n=len(inlets)
    if min(w[:n].sum(),w[n:].sum())<=0:return {'status':'unavailable','reason':'No positive port mass weights'}
    pin=float(np.average(total[:n],weights=w[:n]));pout=float(np.average(total[n:],weights=w[n:]))
    return {'status':status,'inlet_total_pa':pin,
            'outlet_total_pa':pout,'pressure_drop_pa':pin-pout,
            'mach_squared_proxy_n2':mach_squared_proxy,
            'mach_screening_note':mach_note,
            'definition':'Approximate p + 0.5 * local density * (U²+V²+W²), |SV_FLUX|-weighted'}


def _configured_pressure_stations(mesh, centroids, df, config):
    """Cell-centred static pressure sampled at explicitly configured Y bands.

    The sampling is an *arithmetic cell sample*, not a CFD surface flux,
    volume-weighted mean, or a verified total-pressure station. No inferred
    pressure is returned when saved cell data are absent or incomplete.
    """
    import numpy as np
    stations=((config or {}).get('measurement') or {}).get('pressure_stations') or []
    if not stations:return []
    path='results/%s/%s/cells/SV_P' % (mesh['result_group'],mesh['phase_group'])
    if path not in df:
        return [{'id':x['id'],'y_m':x['y_m'],'thickness_m':x['thickness_m'],
                 'status':'not_available','reason':'Saved cell-centred SV_P not present in selected result/phase'}
                for x in stations]
    try:
        field=df[path]
        # Reuse sparse indexed reads; only the cells in configured measurement
        # bands are materialized. Prevents a full-size pressure-field copy.
        results=[]
        for x in stations:
            y=float(x['y_m']);width=float(x['thickness_m'])
            mask=np.flatnonzero(np.abs(centroids[1:,1]-y)<=width/2)+1
            # Face and cell IDs are 1-based; the data offset is zero-based.
            if len(mask)<3:
                results.append({'id':x['id'],'y_m':y,'thickness_m':width,
                    'status':'not_available','cell_count':len(mask),'reason':'Too few cell centres in station band'})
                continue
            values=_subset_field({'SV_P':field},'SV_P',mask-1,mesh['nc'])
            if values is None:
                results.append({'id':x['id'],'y_m':y,'thickness_m':width,
                    'status':'not_available','cell_count':len(mask),
                    'reason':'SV_P segment mapping incomplete or ambiguous for this station'})
                continue
            results.append({'id':x['id'],'y_m':y,'thickness_m':width,
                'status':'computed_arithmetic_cell_sample',
                'cell_count':int(len(values)),'mean_pa':float(np.mean(values)),
                'min_pa':float(np.min(values)),'max_pa':float(np.max(values)),
                'weighting':'unweighted cell-centred SV_P arithmetic mean; not volume or area weighted',
                'actual_y_min_m':float(centroids[mask,1].min()),
                'actual_y_max_m':float(centroids[mask,1].max())})
        return results
    except (UnsupportedCFF,ValueError,IndexError,TypeError,KeyError) as exc:
        return [{'id':x['id'],'y_m':x['y_m'],'thickness_m':x['thickness_m'],
                 'status':'not_available','reason':str(exc)[:190]} for x in stations]


def _module_result_envelopes(modules, ports, pressure, baffle, geometry, layers):
    """Independent physical metric statuses with values, checks and reasons."""
    def envelope(key, values, checks=None):
        item=modules.get(key,{})
        return {'status':item.get('status','not_executed'),
                'values':values,'checks':checks or {},
                'reason':item.get('reason',''), 'source':item.get('source','')}
    stations=pressure.get('stations',[])
    station_ok=sum(x.get('status')=='computed_arithmetic_cell_sample' for x in stations)
    station_status=('not_applicable' if not stations else 'completed' if station_ok==len(stations)
                    else 'partial' if station_ok else 'not_available')
    return {'schema':'autofluent.module_results.v1',
            'case_and_flux':envelope('pair_and_native_flux',{}),
            'port_flux':envelope('port_flux',{
                'inlet_net_kg_s':ports.get('inlet_kg_s'),
                'outlet_net_kg_s':ports.get('outlet_kg_s')},
                {'mass_imbalance_pct':ports.get('mass_imbalance_pct'),
                 'external_face_audit':ports.get('external_face_audit')}),
            'port_pressure':envelope('port_pressure',{
                'pressure_drop_pa':pressure.get('pressure_drop_pa'),
                'definition':pressure.get('definition')},
                {'mach_screening_note':pressure.get('mach_screening_note','not_verified')}),
            'pressure_stations':{'status':station_status,'values':{
                'configured_station_count':len(stations),'computed_station_count':station_ok},
                'checks':{'sample_type':'unweighted cell-centre arithmetic samples'},
                'reason':'one or more configured stations unavailable' if station_ok<len(stations) else ''},
            'baffle':envelope('baffle',{
                'hole_count':baffle.get('hole_count'), 'hole_row_count':baffle.get('hole_row_count'),
                'hole_cv_pct':baffle.get('all_hole_cv_pct'),
                'board_edge_net_kg_s':baffle.get('board_edge_net_kg_s'),
                'whole_cut_net_kg_s':baffle.get('whole_cut_net_kg_s'),
                'opening_identity_status':baffle.get('status')},
                {'cut_closure_error_kg_s':baffle.get('board_cut_closure_error_kg_s'),
                 'expected_hole_check':baffle.get('expected_hole_check','not_configured')}),
            'wafer_geometry':envelope('wafer_geometry',{
                'identified_wafer_count':(geometry or {}).get('total_plates'),
                'identified_channel_count':(geometry or {}).get('total_channels')}),
            'wafer_partition_and_bypass':envelope('wafer_partition_and_bypass',{
                key:{'channel_count':v['channel_count'],'net_kg_s':v['net_kg_s'],
                     'cv_pct':v['cv_pct'],'cut_height_m':v['cut_height_m']}
                for key,v in layers.items()}),
            'convergence':envelope('convergence',{},
                {'source':'saved snapshot alone cannot verify solver convergence'})}


def analyze_saved_pair(case_path,data_path, *, config=None):
    """Compute native cut flux for geometry inferred from current saved Case.

    Does NOT rewrite solver input; caller receives a provenance-rich dictionary.
    """
    import numpy as np
    case=Path(case_path).resolve();data=Path(data_path).resolve()
    # Detect the common race where Fluent overwrites a Case/Data while an
    # offline analysis is running.  This is a change detector, not a proof
    # of paired mesh identity or atomic Fluent snapshot creation.
    def file_identity(path):
        st=path.stat()
        return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    before_case=file_identity(case)
    before_data=file_identity(data)
    budget=config.get('resources',{}).get('memory_budget_mb',6144) if config else 6144
    cf,df,mesh=_read_cff(case,data,memory_budget_mb=budget)
    try:
        from af_progress import update as progress
        progress(61,'共享物理字段','端口、压力和挡板先于晶圆识别独立完成')
        # The sole large topology read is shared. A failed wafer classifier is
        # NOT a reason to discard actual inlet/outlet or baffle results.
        ports,inlets,outlets=_port_flux(mesh,config.get('ports') if config else None)
        pressure={}
        try:
            pressure=_total_pressure(mesh,inlets,outlets)
        except (UnsupportedCFF,ValueError,IndexError,KeyError,TypeError) as exc:
            pressure={'status':'unavailable','reason':str(exc)[:220]}
        progress(67,'四面体单元质心','分块验证真实单元中心，供挡板与截面使用')
        centers=_cell_centroids(mesh)
        tetra_evidence=_tetra_connectivity_spotcheck(mesh)
        progress(72,'独立挡板计算','无须等待晶圆壁面完成配对')
        try:
            baffle=_baffle_plenum_candidates(mesh,centers,df,config=config)
        except (UnsupportedCFF,GeometryAmbiguous,ValueError,IndexError,KeyError,TypeError,MemoryError) as exc:
            baffle={'status':'not_identified','reason':str(exc)[:350]}
        pressure['stations']=_configured_pressure_stations(mesh,centers,df,config)
        if not pressure['stations']:
            diag=baffle.get('diagnostics') or {}
            plate=diag.get('selected_plate') or {}
            pressure['station_reason']='未可靠识别上下两块挡板，可用测站配置指定位置。'
            if baffle.get('status')=='geometry_candidate_holes_native_flux_computed' and plate:
                lo,hi=plate['lower_y_m'],plate['upper_y_m']
                thickness=hi-lo
                horizontal=diag.get('horizontal_wall_stations') or []
                major=[r for r in horizontal if r['wall_area_m2']>=.65*plate['lower_wall_area_m2']]
                mid=(hi+max((r['y_m'] for r in major),default=hi))/2
                top=[(a['y_m'],b['y_m']) for a,b in zip(major,major[1:])
                     if a['y_m']>mid and .8*thickness<=b['y_m']-a['y_m']<=1.2*thickness]
                if len(top)==1 and plate['lower_clearance_m']>1.5*thickness:
                    a,b=top[0]
                    stations=[{'id':name,'y_m':y,'thickness_m':thickness} for name,y in
                              zip(('顶部挡板上游','顶部挡板下游','底部挡板上游','底部挡板下游'),
                                  (b+thickness,a-thickness,hi+thickness,lo-thickness))]
                    pressure['stations']=_configured_pressure_stations(mesh,centers,df,
                        {'measurement':{'pressure_stations':stations}})
                    if all('mean_pa' in s for s in pressure['stations']):
                        pressure['segment_static_drop_pa']=[
                            {'name':name,'drop_pa':u['mean_pa']-d['mean_pa']}
                            for name,u,d in zip(('顶部挡板','晶圆区及层间空间','底部挡板'),
                                                pressure['stations'],pressure['stations'][1:])]
        if config and config.get('expected',{}).get('baffle_hole_count') is not None:
            expected_holes=config['expected']['baffle_hole_count']
            if baffle.get('status')!='geometry_candidate_holes_native_flux_computed':
                baffle['expected_hole_check']='unavailable_baffle_geometry_not_identified'
            elif baffle.get('hole_count')!=expected_holes:
                baffle['expected_hole_check']='mismatch_expected_%d_actual_%d'%(expected_holes,baffle['hole_count'])
                baffle['status']='geometry_candidate_count_mismatch'
            else:baffle['expected_hole_check']='pass'
        modules={
            'pair_and_native_flux':{'status':'completed','source':'CFF Face ID and indexed native SV_FLUX'},
            'port_flux':{'status':'completed','source':'CFF native Face ID'},
            'port_pressure':{'status':'completed' if pressure.get('status','').startswith('computed_') else 'not_available',
                             'reason':pressure.get('reason','')},
            'baffle':{'status':('completed' if baffle.get('status')=='geometry_candidate_holes_native_flux_computed'
                                 else 'partial' if baffle.get('whole_cut_net_kg_s') is not None
                                 else 'not_available'),
                      'reason':baffle.get('reason',baffle.get('expected_hole_check',''))},
            'wafer_geometry':{'status':'not_executed'},
            'wafer_partition_and_bypass':{'status':'not_executed'},
            'convergence':{'status':'not_verified','reason':'Saved snapshot cannot establish iterative convergence'}}
        geometry=None
        geometry_error=None
        layers={}
        stage='wafer_wall_pairing'
        try:
            progress(78,'晶圆识别','分离同 X 壁面、按厚度配对单片，再归属排和通道')
            walls=_wafer_wall_triangles(mesh,config)
            try:
                if config:
                    from af_adaptive_geometry import identify_from_configured_wall_triangles
                    geometry=identify_from_configured_wall_triangles(walls,config)
                else:
                    geometry=identify_from_wall_triangles(walls)
                if not geometry.get('layers'):
                    raise GeometryAmbiguous('No wafer layers identified after wall pairing',
                                            stage='layer_generation',
                                            diagnostics={'identified_layers':0})
            finally:
                del walls
            modules['wafer_geometry']={'status':'completed','source':'measured connected X-normal wall patches',
                                       'actual_wafer_count':geometry['total_plates'],
                                       'actual_channel_count':geometry['total_channels']}
            stage='wafer_partition_and_bypass'
            layers={}
            for layer in geometry['layers']:
                progress(84,'分层分排统计','按真实截面核对层 %s'%layer['id'])
                result,ids,lower,signed=_region_flow(mesh,centers,layer,ports)
                budget=_global_balance(mesh,centers,lower,ids,signed)
                if result['channel_count']!=layer['channels']:
                    raise GeometryAmbiguous('Recognized channel number and measured channel count disagree')
                layer_name= (layer['configured_id'] if config else
                             (('lower' if layer['id']==1 else 'upper') if geometry['total_layers']==2 else 'single'))
                if result['cv_pct'] is None:
                    raise GeometryAmbiguous('Mean channel flow zero; CV is undefined')
                sensitivity=_cut_height_sensitivity(mesh,centers,layer,result,ports['inlet_kg_s'])
                layers[layer_name]={
                    'cut_height_sensitivity':sensitivity,
                    'channel_count':result['channel_count'],'cv_pct':result['cv_pct'],
                    'net_kg_s':result['parts_net_kg_s']['wafer'],
                    'share_inlet_pct':100*result['parts_net_kg_s']['wafer']/ports['inlet_kg_s'],
                    'partition_net_kg_s':result['parts_net_kg_s'],
                    'whole_cut_net_kg_s':result['whole_cut_kg_s'],
                    'partition_directional_kg_s':result['parts_directional_kg_s'],
                    'bypass_directional_kg_s':result['bypass_directional_kg_s'],
                    'channels':result['channels'],
                    'central_x_wide_gap_kg_s':result['central_x_wide_gap_kg_s'],
                    'central_z_between_rows_kg_s':result['central_z_between_rows_kg_s'],
                    'outer_x_end_subset_kg_s':result['outer_x_end_subset_kg_s'],
                    'cut_height_m':result['cut_height_m'],
                    'cut_face_count':result['cut_face_count'],'flux_control_volume':budget,
                    'positive_kg_s':result['positive_kg_s'],
                    'negative_magnitude_kg_s':result['negative_magnitude_kg_s'],
                }
            modules['wafer_partition_and_bypass']={'status':'completed','source':'unique native face ownership and signed flux closure'}
        except (GeometryAmbiguous,UnsupportedCFF,ValueError,KeyError,IndexError,TypeError,RuntimeError,MemoryError) as exc:
            geometry_error={'stage':getattr(exc,'stage',None) or stage,
                            'pipeline_stage':stage,
                            'error_type':type(exc).__name__,'reason':str(exc)[:700],
                            'diagnostics':getattr(exc,'diagnostics',{})}
            modules['wafer_geometry' if stage=='wafer_wall_pairing' else 'wafer_partition_and_bypass']={
                'status':'blocked','stage':geometry_error['stage'],
                'reason':geometry_error['reason'],'diagnostics':geometry_error['diagnostics']}
        if before_case!=file_identity(case) or before_data!=file_identity(data):
            raise UnsupportedCFF('Case/Data was replaced or modified while reading; retry only after Fluent has finished saving')
        if geometry_error is not None:
            progress(90,'部分结果已得到','端口和可用的挡板/压力保留，晶圆待几何核验')
            return {'schema':'autofluent.adaptive_native.v1','status':'partial_physical_metrics',
                    'provenance':{'case_path':str(case),'data_path':str(data),
                                  'result_group':mesh['result_group'],'phase_group':mesh['phase_group'],
                                  'mesh_faces':mesh['nf'],'mesh_tetra_cells':mesh['nc'],
                                  'config_mode':'explicit_geometry_review' if config else 'automatic_geometry_review',
                                  'input_files_unchanged_during_analysis':True},
                    'port_flux':ports,'pressure':pressure,'baffle':baffle,
                    'geometry':geometry,'layers':layers,'modules':modules,
                    'metric_modules':_module_result_envelopes(modules,ports,pressure,baffle,geometry,layers),
                    'first_full_analysis_blocker':geometry_error,
                    'checks':{'global_port_mass_imbalance_pct':ports['mass_imbalance_pct'],
                              'cut_partition_closure':'not_computed' if not layers else 'partial'},
                    'limitations':['Wafer physical partition is NOT verified and no missing CV or bypass values are inferred',
                                   'Port and baffle are independent native flux metrics',
                                   'Saved Case/Data alone cannot verify convergence']}
        progress(91,'全部物理模块计算完成','各分区与端口已独立计算，保留测量口径')
        geometry_signature=hashlib.sha256(json.dumps(geometry,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()
        if before_case!=file_identity(case) or before_data!=file_identity(data):
            raise UnsupportedCFF('Case/Data was replaced or modified while reading: retry only after Fluent has finished saving')
        progress(91,'物理核验','核对进出口质量守恒、互斥分区与数据来源')
        return {'schema':'autofluent.adaptive_native.v1','status':'adaptive_native_candidate_computed',
                'provenance':{'case_path':str(case),'data_path':str(data),
                              'method':'CFF native signed SV_FLUX, cell c0/c1 staircase, centroid region assignment',
                              'wall_zone_fallback':mesh['wall_zone_fallback'],
                              'mesh_faces':mesh['nf'],'mesh_tetra_cells':mesh['nc'],
                              'result_group':mesh['result_group'],'phase_group':mesh['phase_group'],
                              'native_flux_blocks':mesh['flux_block_ranges'],
                              'memory_budget_mb':mesh['memory_budget_mb'],
                              'topology_block_counts':mesh['topology_block_counts'],
                              'projected_peak_memory_mb_not_measured':mesh['projected_peak_memory_mb_not_measured'],
                              'config_mode':'explicit_verified_real_geometry' if config else 'automatic_candidate',
                              'case_bytes':case.stat().st_size,'data_bytes':data.stat().st_size,
                              'case_modified_ns':case.stat().st_mtime_ns,
                              'data_modified_ns':data.stat().st_mtime_ns,
                              'geometry_candidate_signature_not_mesh_hash':geometry_signature,
                              'case_data_identity_evidence':'Data settings/Case File basename + compatible face-index coverage only; not a cryptographic shared-mesh fingerprint',
                              'case_data_pair_independently_verified':False,
                              'input_files_unchanged_during_analysis':True},
                'geometry':geometry, 'port_flux':ports,'layers':layers,
                'modules':modules,
                'metric_modules':_module_result_envelopes(modules,ports,pressure,baffle,geometry,layers),
                'tetra_topology_spotcheck':tetra_evidence,
                'pressure':pressure,'baffle':baffle,
                'checks':{'global_port_mass_imbalance_pct':ports['mass_imbalance_pct'],
                          'cut_partition_closure':'checked on each layer',
                          'all_external_boundary_faces':'audited; significant unclassified flux rejects result',
                          'control_volume_algebraic_closure':'checked on each layer'},
                'limitations':['Axis-aligned wafer faces normal X, vertical gas travel Y, single/two layers',
                               'Segmented indexed node, face, c0/c1 and original SV_FLUX supported; single mesh and tetrahedral cells only',
                               'Geometry inferred automatically, not independently certified',
                               'Saved Case/Data does not establish solver convergence',
                               'Staircase cut uses face-centroid assignment at partition boundaries',
                               'Same named Case/Data cannot prove matching mesh revisions or saved iteration',
                               'Input file metadata unchanged before/after reading; concurrent in-place data edits between checks remain theoretically possible',
                               'Cut sensitivity may be unavailable on large cases due to memory/runtime limits']}
    finally:
        df.close();cf.close()


def try_adaptive_native(case,data,output_dir, *, config=None):
    import traceback
    try:
        result=analyze_saved_pair(case,data,config=config)
    except (GeometryAmbiguous,UnsupportedCFF,ValueError,KeyError,IndexError,TypeError,RuntimeError,OSError,MemoryError) as exc:
        blocked={'stage':'full_wafer_and_baffle_analysis','error_type':type(exc).__name__,
                 'reason':str(exc)[:700]}
        try:
            # Independent smaller branch: never invent wafer results and
            # never discard already computable original boundary fluxes.
            from af_port_only import analyze_ports_only
            partial=analyze_ports_only(case,data,config=config)
            partial['modules']['wafer_geometry']={'status':'blocked','reason':blocked['reason']}
            partial['first_full_analysis_blocker']=blocked
            return {'status':'partial_physical_metrics','detail':blocked['reason'],
                    'reason_type':blocked['error_type'],'metrics':partial,
                    'no_physical_result':False}
        except Exception as port_exc:
            return {'status':'requires_geometry_or_mesh_review','detail':blocked['reason'],
                    'reason_type':blocked['error_type'],'no_physical_result':True,
                    'partial_port_error':str(port_exc)[:450],
                    'first_full_analysis_blocker':blocked}
    out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    ident=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:9]
    base='native_adaptive_'+re.sub(r'[^\w.-]+','_',Path(case).name[:-7])[:35]+'_'+ident
    paths={k:str(out/(base+'.'+ext)) for k,ext in (('json_file','json'),('html_file','html'),('csv_file','csv'))}
    # Stage all outputs in a private sibling directory, publish only when all
    # files are complete. HTML content refers to final paths, not staging paths.
    staging=Path(tempfile.mkdtemp(prefix='.af_partial_',dir=out))
    stage_paths={k:str(staging/Path(v).name) for k,v in paths.items()}
    published={}
    export_error=None
    def publish(key):
        # Exclusive creation preserves existing reports even on Windows folders
        # where renaming a staged file is denied.
        destination=Path(paths[key])
        created=False
        try:
            with open(stage_paths[key],'rb') as src, destination.open('xb') as dst:
                created=True
                shutil.copyfileobj(src,dst)
                dst.flush()
                os.fsync(dst.fileno())
        except OSError:
            if created:
                destination.unlink(missing_ok=True)
            raise
        published[key]=paths[key]
    try:
        row=[]
        for layer_name,layer in result['layers'].items():
            for ch in layer['channels']:
                row.append({'layer':layer_name,'row':ch['row'],'gap_index':ch['gap_index']+1,
                            'channel_id':ch['channel_id'],
                            'x_center_m':ch['x_center_m'],'z_center_m':ch['z_center_m'],
                            'net_kg_s':ch['net_kg_s'],'positive_kg_s':ch['positive_kg_s'],
                            'negative_magnitude_kg_s':ch['negative_magnitude_kg_s']})
        if result['status']=='partial_physical_metrics':
            # Channels are not manufactured. Export independently verified
            # native port and baffle records using the existing CSV columns.
            for item in result['port_flux'].get('boundary_zones',[]):
                row.append({'layer':'port_'+item['kind'],'row':'',
                            'gap_index':'','channel_id':item['zone'],
                            'x_center_m':'','z_center_m':'',
                            'net_kg_s':item.get('net_kg_s'),
                            'positive_kg_s':item.get('positive_kg_s'),
                            'negative_magnitude_kg_s':item.get('negative_magnitude_kg_s')})
            baffle=result.get('baffle') or {}
            if baffle.get('status')=='geometry_candidate_holes_native_flux_computed':
                for i,item in enumerate(baffle.get('holes',[]),1):
                    center=item.get('center_m') or [None,None,None]
                    row.append({'layer':'baffle_hole','row':'','gap_index':i,
                                'channel_id':'H%d'%i,'x_center_m':center[0],
                                'z_center_m':center[2],
                                'net_kg_s':item.get('net_kg_s'),
                                'positive_kg_s':item.get('positive_kg_s'),
                                'negative_magnitude_kg_s':item.get('negative_magnitude_kg_s')})
        with open(stage_paths['csv_file'],'x',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['layer','row','gap_index','channel_id','x_center_m','z_center_m','net_kg_s','positive_kg_s','negative_magnitude_kg_s'])
            writer.writeheader();writer.writerows(row)
        with open(stage_paths['json_file'],'x',encoding='utf-8') as f:
            json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        # Publish the already-calculated JSON and CSV first, independent of
        # HTML rendering. Exclusive creation protects existing reports.
        for key in ('json_file','csv_file'):
            publish(key)
        # No legacy HTML is created: a rendering failure must not silently
        # publish a different design. All figures come from validated metrics.
        from af_progress import update as progress
        progress(96,'结果输出','写入结构化 JSON、CSV 和统一 HTML')
        from af_report_v3 import render_native_report,render_inspection_report
        try:
            if result['status']=='partial_physical_metrics':
                html=render_inspection_report({
                    'schema':'autofluent.offline_case_data.v1',
                    'model_id':Path(case).name[:-7],
                    'physical_analysis_status':'partial_physical_metrics',
                    'partial_native':result,
                    'module_results':result['modules'],
                    'first_full_analysis_blocker':result['first_full_analysis_blocker'],
                    'case':{'path':str(case)},'data':{'path':str(data)},
                    'verification':result.get('checks',{})})
            else:
                html = render_native_report(result)
        except Exception as exc:
            raise RuntimeError('统一报告生成失败；未输出旧版报告：%s: %s' %
                               (type(exc).__name__, str(exc)[:220])) from exc
        with open(stage_paths['html_file'],'x',encoding='utf-8') as f:
            f.write(html)
            f.flush();os.fsync(f.fileno())
        publish('html_file')
        return {'status':result['status'],'metrics':result,'no_physical_result':False,
                'report_save_status':'completed',**published}
    except (OSError,RuntimeError,ValueError,TypeError) as exc:
        # Computation is already complete. Never reclassify the physical
        # result as an invalid CFF or discard successfully published JSON.
        return {'status':'computed_export_failed','computed_status':result['status'],
                'metrics':result,'no_physical_result':False,
                'report_save_status':'failed',
                'export_error':{'stage':'report_publication','error_type':type(exc).__name__,
                                'reason':str(exc)[:650],
                                'retry_cli':'python af_render_saved.py --json <saved-json> --output-dir <folder>'
                                             if 'json_file' in published else None},
                **published}
    finally:
        # Keep any complete outputs. No partially written path is returned.
        # JSON is the source of truth for re-exporting HTML without H5 reload.
        shutil.rmtree(staging,ignore_errors=True)



def _horizontal_wall_triangles(mesh, *, chunk_size=READ_BLOCK):
    """Scan multi-million-face baffle wall in chunks; retain Y-flat faces."""
    import numpy as np
    xyz=mesh['xyz'];conn=mesh['conn'];wall=mesh['wall_ids']
    tol=max(2e-8,float(np.linalg.norm(np.ptp(xyz,axis=0)))*2e-7)
    patches=[]
    for a in range(0,len(wall),chunk_size):
        pts=xyz[conn[wall[a:a+chunk_size]]]
        keep=np.ptp(pts[:,:,1],axis=1)<=tol
        if np.any(keep):patches.append(pts[keep].copy())
    return np.concatenate(patches) if patches else np.empty((0,3,3),dtype=float)


def _plenum_xz_pressure_bins(centroids, pressure, *, x_bins=26, z_bins=14):
    """True cell-centred static-pressure spatial bins; no interpolation/CFD contour claim."""
    import numpy as np
    coords=np.asarray(centroids,dtype=np.float64)
    values=np.asarray(pressure,dtype=np.float64).reshape(-1)
    if len(coords)!=len(values) or len(values)<20 or coords.shape[1]!=3:
        return []
    valid=np.isfinite(coords).all(axis=1)&np.isfinite(values)
    if valid.sum()<20:return []
    c=coords[valid];v=values[valid]
    if np.ptp(c[:,0])<=0 or np.ptp(c[:,2])<=0:return []
    count,xe,ze=np.histogram2d(c[:,0],c[:,2],bins=(x_bins,z_bins))
    amount,_,_=np.histogram2d(c[:,0],c[:,2],bins=(xe,ze),weights=v)
    cells=[]
    for i,j in np.argwhere(count>=3):
        cells.append({'x_lo_m':float(xe[i]),'x_hi_m':float(xe[i+1]),
                      'z_lo_m':float(ze[j]),'z_hi_m':float(ze[j+1]),
                      'mean_pa':float(amount[i,j]/count[i,j]),'cell_count':int(count[i,j])})
    return cells


def _baffle_outlet_y(mesh, config=None):
    """Only an actual outlet boundary zone may be used to rank plates.

    No outlet evidence means no proximity-based auto-pick. Reading a bounded
    sample avoids holding all port triangles in an additional copy.
    """
    import numpy as np
    zones=mesh.get('zones') or []
    conn=mesh.get('conn');xyz=mesh.get('xyz')
    if conn is None or xyz is None:return None
    named={s.lower() for s in ((config or {}).get('ports') or {}).get('outlet_zones',[])}
    values=[]
    for name,a,b in zones:
        label=str(name).lower()
        is_outlet=label in named if named else ('outlet' in label)
        if not is_outlet or b<=a:continue
        indices=np.linspace(a,b-1,min(512,b-a),dtype=np.int64)
        if indices.max()>=len(conn) or np.any(conn[indices]<0):continue
        values.append(float(np.mean(xyz[conn[indices],1])))
    return float(np.mean(values)) if values else None


def _baffle_plenum_candidates(mesh,centroids,df,config=None):
    """Physical plate openings first, native staircase cut pieces second.

    Neither the count of disconnected cut pieces nor expected_hole_count
    certifies the real holes. If wall boundaries cannot prove aperture identity,
    retain the full cut and diagnostics but omit hole count/CV.
    """
    import numpy as np
    from af_baffle_openings import find_planar_openings, match_flux_components
    xyz=mesh['xyz'];tri=_horizontal_wall_triangles(mesh)
    extent=float(np.linalg.norm(np.ptp(xyz,axis=0)))
    tol=max(2e-8,extent*2e-7)
    diagnostics={'horizontal_wall_triangle_count':len(tri)}
    if len(tri)<15:
        return {'status':'not_identified','reason':'Too few Y-planar wall faces','diagnostics':diagnostics}
    centers=tri.mean(axis=1)
    area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)*.5
    from af_adaptive_geometry import _station_groups
    ys,areas=_station_groups(centers[:,1],area,tol)
    outlet_y=_baffle_outlet_y(mesh,config)
    diagnostics['outlet_boundary_y_m']=outlet_y
    diagnostics['horizontal_wall_stations']=[{'y_m':float(y),'wall_area_m2':float(a)} for y,a in zip(ys,areas)]
    if len(ys)<3:
        return {'status':'not_identified','reason':'Fewer than three horizontal wall planes','diagnostics':diagnostics}
    significant=areas>=max(float(np.max(areas))*.08,float(np.median(areas))*.8)
    pos=np.sort(ys[significant]);candidates=[]
    # Store actual planar XZ extents/area, not only Y positions. An unrelated
    # chamber plane may be near a plate but not have matching coverage.
    plane_evidence={}
    for y in pos:
        mask=np.abs(centers[:,1]-y)<=tol*2
        planar=tri[mask]
        if len(planar)==0:continue
        xz=planar[:,:,[0,2]].reshape(-1,2)
        plane_evidence[float(y)]={'area_m2':float(area[mask].sum()),
                                  'bbox_xz_m':[float(xz[:,0].min()),float(xz[:,0].max()),
                                               float(xz[:,1].min()),float(xz[:,1].max())]}
    bound=(config or {}).get('measurement',{}).get('baffle_y_range_m')
    for j in range(1,len(pos)-1):
        floor,low,high=map(float,pos[j-1:j+2])
        gap=low-floor;thick=high-low
        if thick<=0 or gap/thick<3:continue
        selected=(bound is None or (bound[0]<=low and high<=bound[1]))
        pa,pb=plane_evidence[low],plane_evidence[high]
        ax0,ax1,az0,az1=pa['bbox_xz_m'];bx0,bx1,bz0,bz1=pb['bbox_xz_m']
        overlap=max(0,min(ax1,bx1)-max(ax0,bx0))*max(0,min(az1,bz1)-max(az0,bz0))
        amin=min((ax1-ax0)*(az1-az0),(bx1-bx0)*(bz1-bz0))
        cover_ratio=overlap/max(amin,1e-30)
        plane_ratio=min(pa['area_m2'],pb['area_m2'])/max(pa['area_m2'],pb['area_m2'])
        matching=plane_ratio>=.55 and cover_ratio>=.70
        candidates.append({'floor_y_m':floor,'lower_y_m':low,'upper_y_m':high,
                           'thickness_m':thick,'lower_clearance_m':gap,
                           'clearance_thickness_ratio':gap/thick,
                           'lower_wall_area_m2':pa['area_m2'],
                           'upper_wall_area_m2':pb['area_m2'],
                           'area_ratio':float(plane_ratio),
                           'xz_coverage_overlap_ratio':float(cover_ratio),
                           'matching_plate_faces':bool(matching),
                           'outlet_distance_m':abs((low+high)*.5-outlet_y) if outlet_y is not None else None,
                           'selected_by_config':bool(selected)})
    diagnostics['plate_candidates']=candidates
    eligible=[c for c in candidates if c['selected_by_config'] and c['matching_plate_faces']]
    diagnostics['rejected_candidate_count']=len(candidates)-len(eligible)
    if not eligible:
        return {'status':'not_identified','reason':'No plate-floor pair matches geometry constraints',
                'diagnostics':diagnostics}
    if len(eligible)>1:
        # The outlet-side plate is preferred only if the observed outlet is
        # markedly nearer to one candidate. Close distances remain ambiguous.
        ranked=sorted(eligible,key=lambda c:c['outlet_distance_m'] if outlet_y is not None else 0)
        if (outlet_y is not None and ranked[1]['outlet_distance_m']>0 and
                ranked[0]['outlet_distance_m']<.45*ranked[1]['outlet_distance_m']):
            eligible=[ranked[0]]
            diagnostics['selection_basis']='unambiguous_nearest_measured_outlet_boundary'
        else:
            return {'status':'plate_candidate_ambiguous',
                    'reason':'Multiple plausible plate face pairs; outlet proximity insufficient or unknown; narrow measurement.baffle_y_range_m',
                    'diagnostics':diagnostics}
    chosen=eligible[0]
    diagnostics['selected_plate']=dict(chosen)
    diagnostics.setdefault('selection_basis','unique_matching_plate_surfaces')
    floor,low,high=chosen['floor_y_m'],chosen['lower_y_m'],chosen['upper_y_m']
    cut=(low+high)*.5
    face_a=find_planar_openings(tri,low,tol)
    face_b=find_planar_openings(tri,high,tol)
    diagnostics['lower_wall_contours']={k:v for k,v in face_a.items() if k not in ('openings','outers')}
    diagnostics['upper_wall_contours']={k:v for k,v in face_b.items() if k not in ('openings','outers')}
    opening_pair_ok=face_a['status']=='closed_contours' and face_b['status']=='closed_contours'
    opening_reason=''
    if opening_pair_ok:
        lower=face_a['openings'];upper=face_b['openings']
        opening_pair_ok=len(lower)==len(upper)
        if not opening_pair_ok:opening_reason='Upper/lower plate aperture outline counts differ'
        else:
            unused=set(range(len(upper)))
            for hole_a in lower:
                x,z=hole_a['center_xz_m']
                w=hole_a['bbox_xz_m'][1]-hole_a['bbox_xz_m'][0]
                h=hole_a['bbox_xz_m'][3]-hole_a['bbox_xz_m'][2]
                matches=[i for i,hole_b in enumerate(upper)
                         if abs(x-hole_b['center_xz_m'][0])<=max(tol*8,w*.15)
                         and abs(z-hole_b['center_xz_m'][1])<=max(tol*8,h*.15)]
                if len(matches)!=1 or matches[0] not in unused:
                    opening_pair_ok=False;opening_reason='Upper/lower hole centers do not correspond uniquely'
                    diagnostics['first_unmatched_opening_xz_m']=[x,z]
                    break
                unused.remove(matches[0])
    else:
        opening_reason='Plate face aperture outlines unverified: lower=%s, upper=%s'%(face_a['status'],face_b['status'])
    inside=mesh['inside'];owner=mesh['owner'][inside];other=mesh['neighbors']
    below=centroids[:,1]<cut
    across=below[owner]!=below[other]
    faces=inside[across]
    if not len(faces):
        return {'status':'hole_outline_unverified','reason':'No native internal face crossing selected plate',
                'cut_height_m':cut,'diagnostics':diagnostics}
    signed=mesh['flux'][faces]*np.where(below[other[across]],1,-1)
    full_flux=float(signed.sum(dtype=np.float64))
    face_vertices=mesh['conn'][faces]
    # These are *integration fragments* only. They are NOT counted as holes.
    parents=np.arange(len(faces),dtype=np.int32)
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=int(parents[i])
        return int(i)
    shared={}
    for i,nodes in enumerate(face_vertices):
        for node in nodes:
            node=int(node)
            prev=shared.get(node)
            if prev is not None:
                parents[find(i)]=find(prev)
            else:shared[node]=i
    components={}
    for i in range(len(faces)):
        components.setdefault(find(i),[]).append(i)
    fragments=[]
    for ids in components.values():
        ids=np.asarray(ids,dtype=np.int64)
        vertices=xyz[face_vertices[ids]].reshape(-1,3)
        span=np.ptp(vertices,axis=0)
        flux=signed[ids]
        fragments.append({'center_m':np.mean(vertices,axis=0).tolist(),
                          'width_x_m':float(span[0]),'width_z_m':float(span[2]),
                          'face_count':len(ids),'net_kg_s':float(flux.sum()),
                          'positive_kg_s':float(flux[flux>0].sum()),
                          'negative_magnitude_kg_s':float(-flux[flux<0].sum())})
    diagnostics['cut_integration_piece_count']=len(fragments)
    data={'status':'hole_outline_unverified','reason':opening_reason,
          'floor_y_m':floor,'baffle_lower_y_m':low,'baffle_upper_y_m':high,
          'cut_height_m':cut,'whole_cut_net_kg_s':full_flux,
          'candidate_integral_component_count':len(fragments),
          'diagnostics':diagnostics}
    if opening_pair_ok:
        matched=match_flux_components(face_a['openings'],face_a['outers'],fragments,
                                      full_cut_net_kg_s=full_flux,tolerance=tol*4)
        data.update({'mapping':matched,'board_cut_closure_error_kg_s':matched['closure_error_kg_s'],
                     'board_edge_net_kg_s':matched['edge_net_kg_s'],
                     'unassigned_net_kg_s':matched['unassigned_net_kg_s'],
                     'unassigned_components':matched['unassigned_components'],
                     'board_edge_components':matched['board_edge_components']})
        if matched['status']=='verified_openings':
            holes=[]
            for r in matched['holes']:
                x,z=r['center_xz_m'];a,b,c,d=r['bbox_xz_m']
                holes.append({'hole_id':r['hole_id'],'center_m':[x,cut,z],
                              'width_x_m':b-a,'width_z_m':d-c,
                              'face_count':r['face_count'],'integration_component_ids':r['component_ids'],
                              'net_kg_s':r['net_kg_s'],'positive_kg_s':r['positive_kg_s'],
                              'negative_magnitude_kg_s':r['negative_magnitude_kg_s']})
            # Group physical holes by spatial Z, never by fragment count.
            heights=np.asarray([h['width_z_m'] for h in holes]);spacing=float(np.median(heights))
            groups=[]
            for h in sorted(holes,key=lambda x:x['center_m'][2]):
                if not groups or h['center_m'][2]-groups[-1][-1]['center_m'][2]>spacing*.75:
                    groups.append([])
                groups[-1].append(h)
            rows=[]
            for index,group in enumerate(groups,1):
                flow=np.asarray([h['net_kg_s'] for h in group]);mean=float(flow.mean())
                rows.append({'index':index,'hole_count':len(group),'net_kg_s':float(flow.sum()),
                             'cv_pct':float(flow.std()/abs(mean)*100) if abs(mean)>1e-30 else None})
            net=np.asarray([h['net_kg_s'] for h in holes]);mean=float(net.mean())
            data.update({'status':'geometry_candidate_holes_native_flux_computed','reason':'',
                         'hole_count':len(holes),'hole_row_count':len(groups),
                         'holes':holes,'rows':rows,
                         'total_hole_net_kg_s':matched['hole_net_kg_s'],
                         'edge_path_status':'mapped_outside_verified_plate_outline' if matched['board_edge_components'] else 'not_identified',
                         'all_hole_cv_pct':float(net.std()/abs(mean)*100) if abs(mean)>1e-30 else None,
                         'verification':'matched physical closed aperture loops on both plate walls to native flux pieces'})
        else:data['reason']='One or more integral pieces cannot be assigned to exactly one physical aperture; no certified hole CV'
    # A valid pressure sample can be independent of failed aperture matching.
    try:
        cell_data=df.get('results/%s/%s/cells/SV_P'%(mesh['result_group'],mesh['phase_group']))
        if cell_data is not None:
            cell_ids=np.flatnonzero((centroids[1:,1]>floor)&(centroids[1:,1]<low))
            if len(cell_ids)>20:
                vals=_subset_field({'SV_P':cell_data},'SV_P',cell_ids,mesh['nc'])
                if vals is not None:
                    data['lower_plenum_static_pressure_pa']={
                        'cell_count':int(len(vals)),'min':float(vals.min()),
                        'max':float(vals.max()),'range':float(np.ptp(vals)),
                        'unweighted_mean':float(vals.mean()),
                        'sample_type':'unweighted tetra cell-centred static pressure',
                        'xz_bins':_plenum_xz_pressure_bins(centroids[cell_ids+1],vals)}
    except (KeyError,TypeError,ValueError,OSError):pass
    return data
