# -*- coding: utf-8 -*-
"""Independent bounded native-port result, usable when wafer geometry fails.

This computes actual native Face-ID SV_FLUX boundary sums without allocating
triangles, c0/c1 cell centroids or any full-mesh result arrays. It is never
reported as proof of convergence or wafer-area measurement.
"""
from pathlib import Path
import re


def analyze_ports_only(case_path,data_path, *,config=None,chunk_size=131072):
    import h5py
    import numpy as np
    from af_progress import update as progress, portion
    progress(15,'端口独立分析','晶圆识别受阻，采用边界区原生 SV_FLUX 只读统计')
    from af_adaptive_native import (_indexed_datasets,_face_zones,
                                     _select_face_results,_subset_field,
                                     UnsupportedCFF)
    case=Path(case_path);data=Path(data_path)
    with h5py.File(case,'r') as cf,h5py.File(data,'r') as df:
        if 'settings/Case File' not in df:raise UnsupportedCFF('Missing Data Case File identity')
        raw=df['settings/Case File'][0]
        linked=(raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)).replace('\\','/').split('/')[-1]
        if linked.casefold()!=case.name.casefold():
            raise UnsupportedCFF('Case/Data internal basename identity mismatch')
        if 'meshes' not in cf or len(cf['meshes'])!=1:
            raise UnsupportedCFF('Port-only analysis requires one uniquely selected CFF mesh')
        mesh=cf['meshes'][next(iter(cf['meshes']))]
        c0=_indexed_datasets(mesh['faces/c0'],context='faces/c0')
        nfaces=c0[-1][1]
        c0=_indexed_datasets(mesh['faces/c0'],total=nfaces,context='faces/c0')
        zones=_face_zones(mesh,nfaces)
        progress(28,'端口数据元信息','识别进出口区与唯一原生 Face ID 结果组')
        rid,pid,fields,evidence=_select_face_results(df,nfaces)
        if 'faces/c1' not in mesh:raise UnsupportedCFF('Missing c1 adjacency')
        c1=_indexed_datasets(mesh['faces/c1'],total=nfaces,allow_partial=True,context='faces/c1')
        # A per-face boolean occupancy mask is 1 byte/face, and remains
        # bounded even for 26+ million faces (~25 MiB).
        internal=np.zeros(nfaces,dtype=bool)
        for lo,hi,ds in c1:
            for st in range(lo,hi,chunk_size):
                sp=min(st+chunk_size,hi)
                val=np.asarray(ds[st-lo:sp-lo],dtype=np.int64)
                if np.any(val<0):raise UnsupportedCFF('Negative c1 cell IDs at Face '+str(st+1))
                internal[st:sp]=val>0
                portion(29,47,sp,nfaces,'边界/内部面审核','已核对 %d/%d 面邻接 ID'%(sp,nfaces))
        progress(49,'独立边界通量','按区分块读取真实 Face ID 上的正反向质量通量')
        requested_in={x.casefold() for x in config['ports']['inlet_zones']} if config else set()
        requested_out={x.casefold() for x in config['ports']['outlet_zones']} if config else set()
        seen_in=set();seen_out=set();details=[]
        totals={'inlet':{'positive':0.,'reverse':0.,'net':0.,'abs':0.},
                'outlet':{'positive':0.,'reverse':0.,'net':0.,'abs':0.},
                'other_external':{'positive':0.,'reverse':0.,'net':0.,'abs':0.}}
        port_ids=[];port_flux=[];port_types=[]
        for name,lo,hi in zones:
            label=name.strip().casefold()
            if config:
                role='inlet' if label in requested_in else ('outlet' if label in requested_out else 'other_external')
            else:
                role=('inlet' if re.search(r'(^|[-_\s])inlet($|[-_\s\d])',label) else
                      'outlet' if re.search(r'(^|[-_\s])outlet($|[-_\s\d])',label) else 'other_external')
            if role=='inlet':seen_in.add(label)
            if role=='outlet':seen_out.add(label)
            orient=-1 if role=='inlet' else 1
            part={'positive':0.,'reverse':0.,'net':0.,'abs':0.}
            count=0
            for st in range(lo,hi,chunk_size):
                sp=min(st+chunk_size,hi)
                boundary=~internal[st:sp]
                if role!='other_external' and not np.all(boundary):
                    raise UnsupportedCFF('Port zone contains interior Face IDs: '+name)
                if not np.any(boundary):continue
                ids=np.arange(st,sp,dtype=np.int64)[boundary]
                val=_subset_field(fields,'SV_FLUX',ids,nfaces)
                if val is None:raise UnsupportedCFF('Native SV_FLUX invalid for '+name)
                signed=orient*val
                plus=float(signed[signed>0].sum(dtype=np.float64))
                minus=float(-signed[signed<0].sum(dtype=np.float64))
                part['positive']+=plus;part['reverse']+=minus
                part['net']+=plus-minus;part['abs']+=plus+minus
                count+=len(ids)
                if role!='other_external':
                    port_ids.append(ids);port_flux.append(val);port_types.append(np.full(len(ids),role=='inlet',dtype=bool))
            for key in part:totals[role][key]+=part[key]
            if role!='other_external':
                details.append({'zone':name,'kind':role,'face_count':count,
                                'positive_kg_s':part['positive'],
                                'negative_magnitude_kg_s':part['reverse'],
                                'net_kg_s':part['net']})
        if config and (seen_in!=requested_in or seen_out!=requested_out):
            raise UnsupportedCFF('Configured port zones not found: '+str(sorted((requested_in-seen_in)|(requested_out-seen_out))))
        if not any(x['kind']=='inlet' for x in details) or not any(x['kind']=='outlet' for x in details):
            raise UnsupportedCFF('No confirmed inlet and outlet zones for standalone port measurement')
        pin=totals['inlet']['net'];pout=totals['outlet']['net']
        if pin<=0 or pout<=0:raise UnsupportedCFF('Port native SV_FLUX sign conflicts with inlet/outlet designation')
        other=totals['other_external']
        # Significant non-port vents render two-port 'imbalance' incomplete.
        allowance=max(1e-12,pin*1e-4)
        port={'status':'computed_native_ports_only',
              'inlet_kg_s':pin,'outlet_kg_s':pout,
              'mass_imbalance_pct':100*abs(pin-pout)/pin if other['abs']<=allowance else None,
              'external_boundary_absolute_unclassified_kg_s':other['abs'],
              'external_boundary_unclassified_net_kg_s':other['net'],
              'external_audit':'pass' if other['abs']<=allowance else 'unclassified_flux_requires_review',
              'boundary_zones':details,
              'native_flux_blocks':evidence,
              'result_group':rid,'phase_group':pid,
              'nfaces':nfaces,
              'limitation':'Signed port flux assumes boundary names and Fluent outward face sign; not a wafer partition or convergence verification'}
        progress(80,'可用压力字段','在端口局部读取压力、密度和速度；缺失则保留质量流量')
        pressure={'status':'not_available','reason':'Port pressure fields missing or incomplete'}
        if port_ids:
            ids=np.concatenate(port_ids)
            f=np.concatenate(port_flux)
            in_mask=np.concatenate(port_types)
            fields_needed={k:_subset_field(fields,k,ids,nfaces)
                           for k in ('SV_P','SV_DENSITY','SV_U','SV_V','SV_W')}
            if all(v is not None for v in fields_needed.values()):
                d=fields_needed['SV_DENSITY']
                if np.all(d>0):
                    vv=sum(fields_needed[key]**2 for key in ('SV_U','SV_V','SV_W'))
                    total=fields_needed['SV_P']+.5*d*vv
                    weights=np.abs(f)
                    if np.any(in_mask) and np.any(~in_mask) and weights[in_mask].sum()>0 and weights[~in_mask].sum()>0:
                        in_pa=float(np.average(total[in_mask],weights=weights[in_mask]))
                        out_pa=float(np.average(total[~in_mask],weights=weights[~in_mask]))
                        pressure={'status':'computed_pressure_proxy_mach_unverified',
                                  'inlet_total_pa':in_pa,'outlet_total_pa':out_pa,
                                  'pressure_drop_pa':in_pa-out_pa,
                                  'definition':'p + rho*v²/2, |SV_FLUX|-weighted; Mach and gas properties unverified'}
        progress(88,'部分物理结果完成','端口与未分类边界通量已核对；晶圆仍需几何/资源审核')
        return {'schema':'autofluent.partial_native.v1','status':'partial_physical_metrics',
                'source':{'case_path':str(case),'data_path':str(data),'result_group':rid,'phase_group':pid},
                'modules':{'port_flux':{'status':'completed','source':'native_SV_FLUX'},
                           'port_pressure':{'status':pressure['status']},
                           'wafer_geometry':{'status':'not_computed'},
                           'baffle':{'status':'not_computed'},
                           'convergence':{'status':'not_verified'}},
                'port_flux':port,'pressure':pressure}
