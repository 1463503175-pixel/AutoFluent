# -*- coding: utf-8 -*-
"""Read-only, memory-bounded inspection of Fluent 2024 R2 CFF mesh topology.

Do NOT confuse presence of these datasets with a validated SV_FLUX map.  This
module handles documented meshes/1/{nodes,faces} data and checks the precise
array/block layout before any RT0 or wafer geometry algorithm may consume it.
No guessed coordinates, face IDs, or old-model geometry are substituted.
"""
from __future__ import annotations

import math
from pathlib import Path

MAX_BLOCKS = 256
CHUNK_ROWS = 131072


def _blocks(h5, path, suffix=None):
    """Enumerate CFF numbered blocks; do not concatenate unrelated datasets."""
    import h5py
    if path not in h5:
        return []
    obj = h5[path]
    if isinstance(obj, h5py.Dataset):
        return [(path, obj)] if suffix is None else []
    keys = list(obj.keys())
    if len(keys) > MAX_BLOCKS:
        raise ValueError('Too many mesh blocks: %s' % path)
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicated mesh blocks')
    order = sorted(keys, key=lambda key: (not key.isdecimal(), int(key) if key.isdecimal() else key))
    out = []
    for key in order:
        item = obj[key]
        if suffix:
            if not isinstance(item, h5py.Group) or suffix not in item:
                raise ValueError('Missing %s for mesh block %s/%s' % (suffix, path, key))
            item = item[suffix]
            dest = '%s/%s/%s' % (path, key, suffix)
        else:
            dest = '%s/%s' % (path, key)
        if not isinstance(item, h5py.Dataset):
            raise ValueError('Expected a dataset at %s' % dest)
        out.append((dest, item))
    return out


def _shape_of_blocks(blocks, *, dim=None):
    total = 0
    out = []
    for path, ds in blocks:
        if len(ds.shape) != 1:
            raise ValueError('Expected 1D array at ' + path)
        if ds.dtype.kind not in 'iu':
            raise ValueError('Expected integer array at ' + path)
        count = int(ds.shape[0])
        out.append({'path': path, 'length': count, 'dtype': str(ds.dtype)})
        total += count
    return total, out


def _count_nnodes(blocks, *, scan_budget=25000000):
    import numpy as np
    total = 0
    min_n = 2**31
    max_n = 0
    triangular = 0
    node_references = 0
    valid = True
    sampled = False
    for path, ds in blocks:
        if ds.ndim != 1 or ds.dtype.kind not in 'iu':
            raise ValueError('Invalid face nnodes at ' + path)
        total += int(ds.shape[0])
        if total > scan_budget:
            sampled = True
            valid = False
            break
        for start in range(0, len(ds), CHUNK_ROWS):
            vals = np.asarray(ds[start:start+CHUNK_ROWS])
            if len(vals):
                min_n = min(min_n, int(vals.min()))
                max_n = max(max_n, int(vals.max()))
                triangular += int(np.count_nonzero(vals == 3))
                node_references += int(vals.sum(dtype=np.int64))
                if min_n < 3 or max_n > 1024:
                    valid = False
    return {'face_records_scanned':total, 'minimum_nodes_per_face':None if min_n==2**31 else min_n,
            'maximum_nodes_per_face':max_n or None, 'triangular_faces_scanned':triangular, 'referenced_face_nodes':node_references,
            'complete':not sampled, 'face_sizes_valid':valid}


def _coords_summary(blocks, *, row_budget=12000000):
    """Scan coordinates by chunks, avoiding a multi-GB HDF5 read."""
    import numpy as np
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    total = 0
    verified = True
    reasons = []
    for path, ds in blocks:
        if ds.ndim != 2 or ds.dtype.kind not in 'fiu':
            verified = False;reasons.append('Invalid coordinates: ' + path);continue
        if ds.shape[1] == 3 and ds.shape[0] != 3:
            axis = 0; n = int(ds.shape[0])
        elif ds.shape[0] == 3 and ds.shape[1] != 3:
            axis = 1; n = int(ds.shape[1])
        else:
            verified = False;reasons.append('Ambiguous coordinate shape: ' + path);continue
        total += n
        if total > row_budget:
            verified = False;reasons.append('Node scan exceeds safe row budget');break
        for start in range(0,n,CHUNK_ROWS):
            a = np.asarray(ds[start:start+CHUNK_ROWS,:] if axis==0 else ds[:,start:start+CHUNK_ROWS], dtype=np.float64)
            if axis==1:a=a.T
            if not np.isfinite(a).all():
                verified=False;reasons.append('Nonfinite node coordinates');break
            if len(a):
                low = np.minimum(low,a.min(axis=0));high = np.maximum(high,a.max(axis=0))
    return {'node_count_scanned':total,'bbox_min_m':low.tolist() if np.isfinite(low).all() else None,
            'bbox_max_m':high.tolist() if np.isfinite(high).all() else None,
            'complete':verified,'issues':reasons}


def _read_topology_zone_ranges(h5, base):
    import numpy as np
    path=base+'/faces/zoneTopology'
    if path not in h5:return {'status':'missing_zone_topology'}
    g=h5[path]
    if not all(x in g for x in ('id','minId','maxId')):
        return {'status':'incomplete_zone_topology'}
    ids=np.asarray(g['id']).reshape(-1)
    lo=np.asarray(g['minId']).reshape(-1)
    hi=np.asarray(g['maxId']).reshape(-1)
    if not(len(ids)==len(lo)==len(hi)):
        return {'status':'zone_length_mismatch'}
    records=[];valid=True
    for i in range(len(ids)):
        z,a,b=int(ids[i]),int(lo[i]),int(hi[i])
        if a<1 or b<a:valid=False
        if len(records)<5000:records.append({'zone_id':z,'min_face_id':a,'max_face_id':b,'count':b-a+1})
    ranges=sorted((int(a),int(b)) for a,b in zip(lo,hi))
    disjoint=all(ranges[i][0]>ranges[i-1][1] for i in range(1,len(ranges)))
    contiguous=(bool(ranges) and ranges[0][0]==1 and
                all(ranges[i][0]==ranges[i-1][1]+1 for i in range(1,len(ranges))))
    return {'status':'indexed' if valid else 'invalid_range','zone_count':len(ids),
            'ranges_disjoint':disjoint, 'ranges_contiguous':contiguous, 'min_face_id':ranges[0][0] if ranges else None,
            'max_face_id':max((x[1] for x in ranges),default=None),
            'zone_ranges':records[:100]}


def inspect_case_topology(case_path):
    """Return validation evidence without reading full face connectivity or field arrays."""
    try:
        import h5py
    except ImportError:
        return {'status':'h5py_missing'}
    try:
        with h5py.File(str(Path(case_path).resolve()), 'r') as h:
            mesh_ids = sorted(h.get('/meshes', {}).keys()) if '/meshes' in h else []
            if len(mesh_ids)!=1:
                return {'status':'unsupported_mesh_count','mesh_ids':mesh_ids[:20]}
            base='/meshes/'+mesh_ids[0]
            face_block_paths={
                'face_nodes':_blocks(h,base+'/faces/nodes',suffix='nodes'),
                'face_nnodes':_blocks(h,base+'/faces/nodes',suffix='nnodes'),
                'face_c0':_blocks(h,base+'/faces/c0'),
                'face_c1':_blocks(h,base+'/faces/c1'),
                'node_coordinates':_blocks(h,base+'/nodes/coords'),
            }
            array_info={}
            for key in ('face_nodes','face_nnodes','face_c0','face_c1'):
                size,descs=_shape_of_blocks(face_block_paths[key])
                array_info[key]={'entries':size, 'blocks':descs[:10]}
            if not face_block_paths['node_coordinates']:
                return {'status':'missing_node_coordinates','arrays':array_info}
            coordinates=_coords_summary(face_block_paths['node_coordinates'])
            faces=_count_nnodes(face_block_paths['face_nnodes'])
            zones=_read_topology_zone_ranges(h,base)
            nfaces=array_info['face_nnodes']['entries']
            c0=array_info['face_c0']['entries']
            c1=array_info['face_c1']['entries']
            # Counts alone cannot establish global Face ID ordering across
            # parallel CFF blocks. Until their min/max ID attributes are
            # explicitly decoded, forbid the global native-flux map.
            exactly_one_block=(all(len(face_block_paths[k])==1 for k in
                ('face_nodes','face_nnodes','face_c0','face_c1')) and
                len(face_block_paths['node_coordinates'])==1)
            # Fluent may save boundary c1 values separately/shorter than c0.
            connected=(nfaces==c0 and 0<=c1<=nfaces and nfaces>0)
            connectivity_lengths=(nfaces>0 and faces['complete'] and
                array_info['face_nodes']['entries']==faces['referenced_face_nodes'])
            zone_matches=(zones.get('status')=='indexed' and zones.get('ranges_disjoint') is True
                          and zones.get('ranges_contiguous') is True and
                          zones.get('min_face_id')==1 and zones.get('max_face_id')==nfaces)
            ready=bool(connected and connectivity_lengths and faces['complete'] and
                       faces['face_sizes_valid'] and coordinates['complete'] and
                       zone_matches and exactly_one_block and c1==nfaces)
            return {'status':'geometry_arrays_consistent' if ready else 'geometry_requires_mapping_review',
                    'mesh_id':mesh_ids[0], 'arrays':array_info,'coordinates':coordinates,
                    'face_geometry':faces,'zone_topology':zones,
                    'checks':{'face_c0_aligned':connected,'face_node_count_plausible':connectivity_lengths,
                              'zone_face_coverage_verified':zone_matches,
                              'global_block_order_unambiguous':exactly_one_block,
                              'full_face_c1_array_available':c1==nfaces,
                              'all_faces_triangular':faces['complete'] and faces['triangular_faces_scanned']==nfaces},
                    'flux_orientation_verified':False,
                    'wafer_cut_sections_computed':False,
                    'reason':'单块 CFF 面/节点布局核对，不是 SV_FLUX 法向认证或晶圆片间 RT0 物理积分；并行多块网格需要独立校验全局 ID 偏移'}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return {'status':'topology_reader_failed','reason':str(exc)[:350],
                'flux_orientation_verified':False,'wafer_cut_sections_computed':False}
