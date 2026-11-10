# -*- coding: utf-8 -*-
"""Compact, non-sensitive-by-default HDF5 structural diagnostics.

Creates a SMALL local JSON snapshot of dataset names/shapes, and a few numbers
from relevant mesh/connectivity/flux datasets when safe. It never copies mesh
or field arrays in full, and it does not upload data anywhere. The snapshot is
for implementing real Fluent H5 adapters, not for deriving physical results.
"""
from pathlib import Path
import json
import math
import time
import uuid

MAX_DATASETS = 150000
MAX_MATCHES = 250
MAX_SAMPLES = 22
TOKENS = ('SV_FLUX','FACE','CELL','NODE','VERT','ZONE','COORD','CONNECT','TOPOLOGY','PRESSURE')


def _priority(name):
    name=name.upper()
    if 'SV_FLUX' in name:return 0
    if 'FACE' in name and any(t in name for t in ('NODE','CELL','CONNECT')):return 1
    if 'NODE' in name and 'COORD' in name:return 2
    if 'FACE' in name:return 3
    if 'CELL' in name:return 4
    return 5


def _small_sample(dataset):
    # Only numeric, low-rank datasets. Each slice is at most 4x4 entries;
    # HDF5 may still decompress a chunk, so callers cap at 22 datasets.
    if dataset.ndim < 1 or dataset.ndim > 2 or dataset.size == 0:
        return None
    if dataset.dtype.kind not in 'iufb':return None
    try:
        slices=tuple(slice(0,min(4, n)) for n in dataset.shape)
        value=dataset[slices]
        samples=value.reshape(-1).tolist()
        result=[]
        for v in samples:
            if isinstance(v,bool):result.append(bool(v))
            elif isinstance(v,(int,float)):
                if not math.isfinite(float(v)):return None
                result.append(int(v) if isinstance(v,int) else float(v))
            else:return None
        return result[:16]
    except (OSError,ValueError,TypeError,RuntimeError):
        return None


def inspect_structure(path):
    try:
        import h5py
    except ImportError:
        return {'status':'h5py_missing', 'entries':[], 'samples':[]}
    file=Path(path).resolve()
    entries=[]
    count=0
    with h5py.File(str(file),'r') as h:
        roots=sorted(h.keys())
        def visit(name,obj):
            nonlocal count
            if not isinstance(obj,h5py.Dataset):return
            count+=1
            if count>MAX_DATASETS or not any(t in name.upper() for t in TOKENS):return
            if len(entries)<MAX_MATCHES:
                entries.append({'path':name,'shape':list(obj.shape),'dtype':str(obj.dtype),
                                'chunks':list(obj.chunks) if obj.chunks else None,
                                'attribute_names':sorted(str(k) for k in obj.attrs.keys())[:20]})
        h.visititems(visit)
        sample_candidates=sorted(entries,key=lambda x:(_priority(x['path']),x['path']))[:MAX_SAMPLES]
        samples=[]
        for row in sample_candidates:
            vals=_small_sample(h[row['path']])
            if vals is not None:
                samples.append({'path':row['path'],'first_values':vals})
    return {'status':'metadata_and_bounded_samples','file':str(file),
            'root_groups':roots,'dataset_count':count,'matched_count':len(entries),
            'metadata_limit_reached':count>MAX_DATASETS,'samples':samples,'entries':entries,
            'physical_analysis_status':'not_computed',
            'warning':'每个抽样字段最多16个数；并未导出完整网格、通量或计算片间结果。'}


def save_pair_schema(case,data,output_dir):
    out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    results={'schema':'autofluent.h5_schema_probe.v1',
             'case':inspect_structure(case),'data':inspect_structure(data),
             'verification':'HDF5_METADATA_AND_TINY_SAMPLES_ONLY',
             'upload_status':'local_only_no_upload',
             'warning':'诊断文件可能含少量真实几何坐标，分享前请自行审查。'}
    path=out/('h5_schema_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.json')
    with path.open('x',encoding='utf-8') as fp:json.dump(results,fp,ensure_ascii=False,indent=2)
    return str(path)
