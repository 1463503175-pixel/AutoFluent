"""Reuse native aperture/flux integration for a separately identified inlet plate."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
from copy import deepcopy
import math


def choose_upper_plate(lower, ports):
    selected=(lower.get('diagnostics') or {}).get('selected_plate') or {}
    high=selected.get('upper_y_m')
    inlet=[p['center_m'][1] for z in ports.get('boundary_zones',[]) if z.get('kind')=='inlet'
           for p in z.get('physical_ports',[]) if len(p.get('center_m') or [])==3]
    if high is None or not inlet:return None,'缺少下挡板或实际入口位置'
    y=sum(inlet)/len(inlet)
    candidates=[c for c in (lower.get('diagnostics') or {}).get('plate_candidates',[])
                if c.get('matching_plate_faces') and c['lower_y_m']>high
                and c['upper_y_m']<y and c['lower_y_m']<c['upper_y_m']]
    if len(candidates)!=1:return None,'入口侧挡板候选不唯一或未取得'
    return candidates[0],''


def compute_dual_baffles(mesh, centers, df, lower, ports, config=None):
    from af_adaptive_native import _baffle_plenum_candidates, UnsupportedCFF, GeometryAmbiguous
    lower={**lower,'role':'outlet','label':'下部出气挡板'}
    candidate,reason=choose_upper_plate(lower,ports)
    upper={'status':'not_identified','reason':reason,'role':'inlet','label':'上部进气挡板'}
    if candidate is not None:
        cfg=deepcopy(config or {});cfg.setdefault('measurement',{})['baffle_y_range_m']=[candidate['lower_y_m']-1e-8,candidate['upper_y_m']+1e-8]
        try:
            upper=_baffle_plenum_candidates(mesh,centers,df,config=cfg,include_plenum=False)
            if abs(upper.get('cut_height_m',float('inf'))-(candidate['lower_y_m']+candidate['upper_y_m'])/2)>1e-7:
                raise ValueError('上挡板实际积分高度与候选面不一致')
            upper.pop('lower_plenum_static_pressure_pa',None)
            upper.pop('floor_y_m',None)
        except (UnsupportedCFF,GeometryAmbiguous,ValueError,KeyError,IndexError,TypeError,RuntimeError,MemoryError,OSError) as exc:
            upper={'status':'not_identified','reason':str(exc)[:350]}
        upper.update(role='inlet',label='上部进气挡板')
    return {'upper':upper,'lower':lower}


def read_pressure_reference(case_file):
    import re
    dataset=case_file.get('settings/Rampant Variables')
    if dataset is None:return {'status':'unavailable'}
    raw=dataset[()]
    if isinstance(raw,bytes):text=raw.decode('utf-8','replace')
    elif hasattr(raw,'ravel'):text='\n'.join(x.decode('utf-8','replace') if isinstance(x,bytes) else str(x) for x in raw.ravel())
    else:text=str(raw)
    match=re.search(r'\(operating-pressure\s+([-+0-9.eE]+)\)',text)
    if not match or re.search(r'\(floating-operating-pressure\?\s+#t\)',text):return {'status':'unavailable'}
    value=float(match.group(1))
    if not math.isfinite(value):return {'status':'unavailable'}
    return {'status':'constant_operating_pressure_from_case','operating_pressure_pa':value,
            'field':'SV_P','source':'Case settings/Rampant Variables: operating-pressure',
            'static_absolute_conversion':'p_absolute = SV_P + operating_pressure'}


def spatial_context(mesh, ports):
    centers={kind:[p['center_m'] for z in ports.get('boundary_zones',[]) if z.get('kind')==kind
                   for p in z.get('physical_ports',[]) if len(p.get('center_m') or [])==3]
             for kind in ('inlet','outlet')}
    direction=None
    if centers['inlet'] and centers['outlet']:
        y_in=sum(c[1] for c in centers['inlet'])/len(centers['inlet'])
        y_out=sum(c[1] for c in centers['outlet'])/len(centers['outlet'])
        direction='negative' if y_out<y_in else 'positive' if y_out>y_in else None
    return {'coordinate_system':'native mesh Cartesian XYZ','units':'m','main_flow_axis':'Y',
            'main_flow_direction':direction,'inlet_centers_m':centers['inlet'],'outlet_centers_m':centers['outlet'],
            'plan_view':{'looking_along':'-Y','screen_right':'+X','screen_down':'+Z'}}
