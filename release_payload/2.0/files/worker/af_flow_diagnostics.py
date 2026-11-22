# -*- coding: utf-8 -*-
"""Read-only localization sharing the already loaded CFF mesh; no solver calls."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import math
import numpy as np

PRESSURE_DEFINITION='abs(native SV_FLUX)-weighted arithmetic c0/c1 cell p+rho*V2/2; low-speed proxy'


def channel_diagnostics(layers):
    result={}
    for name,layer in layers.items():
        channels=layer.get('channels') or []
        if not channels:continue
        q=np.array([c['net_kg_s'] for c in channels],dtype=float)
        x=np.array([c['x_center_m'] for c in channels],dtype=float)
        if not np.isfinite(q).all() or not np.isfinite(x).all():raise ValueError('Invalid channel fields')
        mean=float(q.mean());ss=float(np.sum((q-mean)**2))
        groups={}
        for c in channels:groups.setdefault(c['gap_index'],[]).append(c)
        row_sets=[{c['row'] for c in g} for g in groups.values()]
        paired=bool(row_sets and len(row_sets[0])>1 and all(s==row_sets[0] and len(g)==len(s) for s,g in zip(row_sets,groups.values())))
        coordinate_tolerance=max(1e-8,float(np.ptp(x))*1e-7)
        paired=paired and all(max(c['x_center_m'] for c in g)-min(c['x_center_m'] for c in g)<=coordinate_tolerance for g in groups.values())
        axial=None
        if paired and ss>0:
            between=sum(len(g)*(np.mean([c['net_kg_s'] for c in g])-mean)**2 for g in groups.values())
            axial=float(100*between/ss)
        lo,hi=float(x.min()),float(x.max());edges=np.linspace(lo,hi,11)
        bins=[]
        for i in range(10):
            mask=(x>=edges[i])&((x<edges[i+1]) if i<9 else (x<=edges[i+1]))
            if not mask.any():continue
            avg=float(q[mask].mean())
            bins.append({'index':i+1,'x_range_m':[float(edges[i]),float(edges[i+1])],
                'channel_count':int(mask.sum()),'mean_kg_s':avg,
                'mean_relative_pct':100*avg/mean if mean>0 else None,
                'variance_share_pct':float(100*np.sum((q[mask]-mean)**2)/ss) if ss>0 else None})
        # Degenerate X coordinates form one bin, never ten copies of a channel.
        if hi==lo:bins=bins[-1:]
        weak=min(channels,key=lambda c:c['net_kg_s'])
        result[name]={'status':'computed','channel_count':len(q),'mean_kg_s':mean,
            'cv_pct':float(100*q.std()/abs(mean)) if mean else None,'weakest':dict(weak),
            'weakest_mean_pct':100*weak['net_kg_s']/mean if mean>0 else None,
            'axial_variance_pct':axial,'row_difference_variance_pct':100-axial if axial is not None else None,
            'x_bins':bins,'note':'X bins use actual channel centres; different model layouts need a separate common spatial mask.'}
    return result


def pressure_statistics(q,p0,p1,pt0,pt1):
    arrays=[np.asarray(v,dtype=float) for v in (q,p0,p1,pt0,pt1)]
    if not arrays[0].size or any(v.shape!=arrays[0].shape or not np.isfinite(v).all() for v in arrays):
        raise ValueError('Missing/nonfinite internal pressure fields')
    q,p0,p1,pt0,pt1=arrays;w=np.abs(q)
    if w.sum()<=0:raise ValueError('No measurable cut flux')
    return {'net_kg_s':float(q.sum()),'reverse_kg_s':float(-q[q<0].sum()),
        'static_pa':float(np.average((p0+p1)*.5,weights=w)),
        'total_pa':float(np.average((pt0+pt1)*.5,weights=w)),
        'c0_total_pa':float(np.average(pt0,weights=w)),
        'c1_total_pa':float(np.average(pt1,weights=w))}


def pressure_plan(geometry,pressure,baffle):
    layers=sorted(geometry.get('layers') or [],key=lambda g:g['y_cut_m'],reverse=True)
    top=pressure.get('stations') or []
    plate=(baffle.get('diagnostics') or {}).get('selected_plate') or {}
    if not layers or len(top)<2 or not plate:raise ValueError('Verified layer/plate station geometry unavailable')
    if not top[0]['y_m']>top[1]['y_m']>layers[0]['y_max_m']:
        raise ValueError('Top pressure stations do not bracket a verified upstream plate')
    plan=[{'id':'top_up','y_m':top[0]['y_m']},{'id':'top_down','y_m':top[1]['y_m']}]
    for g in layers:
        for side,y in [('in',g['y_max_m']+.003),('out',g['y_min_m']-.003)]:
            plan.append({'id':'wafer_%s_%s'%(g['id'],side),'y_m':y,'layer_id':g['id'],'side':side})
    # Anchor common downstream probes to wafer end/floor, not the moved plate.
    # 1 um rounding removes triangulation extrema noise and is explicitly saved.
    plan.extend([{'id':'bottom_up','y_m':layers[-1]['y_min_m']-.004},
                 {'id':'bottom_down','y_m':plate['floor_y_m']+.002}])
    for p in plan:p['y_m']=round(float(p['y_m']),6)
    if not plan[-2]['y_m']>plate['upper_y_m'] or not plan[-1]['y_m']<plate['lower_y_m']:
        raise ValueError('No safe common wafer/floor-anchored probes around bottom plate')
    if not all(a['y_m']>b['y_m'] for a,b in zip(plan,plan[1:])):
        raise ValueError('Pressure station clearances overlap; no inferred segment values')
    return plan


def _read_cell_field(cells,key,nc,reader):
    if key not in cells:raise ValueError('Missing saved cell field '+key)
    values=np.empty(nc,dtype=float)
    for lo,hi,ds in reader(cells[key],total=nc,context=key):values[lo:hi]=ds[:]
    if not np.isfinite(values).all():raise ValueError('Nonfinite '+key)
    return values


def _correlation(x,y):
    if len(x)<3 or np.std(x)==0 or np.std(y)==0:return None
    return float(np.corrcoef(x,y)[0,1])


def driving_summary(driving):
    flow=[c['net_kg_s'] for c in driving];drop=[c['drive_pa'] for c in driving]
    correlation=_correlation(flow,drop)
    up=float(np.std([c['inlet_total_pa'] for c in driving]))
    down=float(np.std([c['outlet_total_pa'] for c in driving]))
    origin='not_isolated'
    # Advisory localization threshold, never a geometric causation certificate.
    if correlation is not None and correlation>=.8:
        if up>3*max(down,1e-15):origin='supply_side'
        elif down>3*max(up,1e-15):origin='downstream_side'
        else:origin='mixed'
    return {'origin':origin,'inlet_pressure_std_pa':up,'outlet_pressure_std_pa':down,
            'flow_drive_correlation':correlation,'rule':'correlation>=0.8 and pressure-std ratio>3; advisory only'}


def analyze_flow_diagnostics(mesh,centers,df,geometry,layers,ports,pressure,baffle):
    result={'status':'partial','channels':channel_diagnostics(layers),'pressure_definition':PRESSURE_DEFINITION,
        'pressure_stations':[],'segments':[],
        'limitations':['Internal total pressure is a cell-pair proxy, not an exact Fluent face report.',
            'Correlation/local pressure localization does not isolate geometric causation or certify convergence.']}
    try:
        from af_adaptive_native import _indexed_datasets,_subset_field
        from af_adaptive_geometry import partition_cut_faces
        from af_progress import update
        plan=pressure_plan(geometry,pressure,baffle)
        extra_mb=5*mesh['nc']*8/1024**2
        if mesh.get('projected_peak_memory_mb_not_measured',0)+extra_mb>mesh.get('memory_budget_mb',6144):
            raise MemoryError('Additional pressure fields exceed configured memory estimate')
        update(88,'偏流与压降定位','复用当前网格，读取保存的单元总压代理字段')
        cells=df['results/%s/%s/cells'%(mesh['result_group'],mesh['phase_group'])]
        p=_read_cell_field(cells,'SV_P',mesh['nc'],_indexed_datasets)
        rho=_read_cell_field(cells,'SV_DENSITY',mesh['nc'],_indexed_datasets)
        if np.any(rho<=0):raise ValueError('Nonpositive density')
        speed2=np.zeros(mesh['nc'])
        for key in ('SV_U','SV_V','SV_W'):
            v=_read_cell_field(cells,key,mesh['nc'],_indexed_datasets);speed2+=v*v
        del v
        pt=p+.5*rho*speed2
        del rho,speed2
        stations=[];inside=mesh['inside'];owner=mesh['owner'][inside];other=mesh['neighbors']
        for probe in plan:
            update(89,'偏流与压降定位',probe['id'],force=True)
            lower=centers[:,1]<probe['y_m'];across=lower[owner]!=lower[other]
            ids=inside[across];c0=owner[across]-1;c1=other[across]-1
            q=mesh['flux'][ids]*np.where(lower[other[across]],1,-1)
            s={**probe,**pressure_statistics(q,p[c0],p[c1],pt[c0],pt[c1]),'face_count':int(len(ids))}
            if abs(s['net_kg_s']/ports['inlet_kg_s']-1)>.01:
                raise ValueError('Pressure cut/inlet mass difference exceeds 1%; localization withheld')
            if 'layer_id' in probe:
                g=next(g for g in geometry['layers'] if g['id']==probe['layer_id'])
                points=mesh['xyz'][mesh['conn'][ids]].mean(axis=1)
                weights=np.abs(q);midpt=.5*(pt[c0]+pt[c1])
                f=partition_cut_faces(points,weights,g,min_faces_per_channel=1)
                e=partition_cut_faces(points,weights*midpt,g,min_faces_per_channel=1)
                local={}
                for flow,energy in zip(f['channels'],e['channels']):
                    if flow['channel_id']!=energy['channel_id'] or flow['net_kg_s']<=0:
                        raise ValueError('Channel pressure mapping incomplete')
                    local[flow['channel_id']]=energy['net_kg_s']/flow['net_kg_s']
                s['channel_total_pa']=local
            stations.append(s)
        result['pressure_stations']=stations
        byid={s['id']:s for s in stations}
        for g in geometry['layers']:
            name=next((k for k,v in layers.items() if abs(v['cut_height_m']-g['y_cut_m'])<1e-8),None)
            if name is None:continue
            up=byid['wafer_%s_in'%g['id']]['channel_total_pa'];down=byid['wafer_%s_out'%g['id']]['channel_total_pa']
            channels=layers[name]['channels'];driving=[]
            for c in channels:
                key=c['channel_id']
                if key not in up or key not in down:raise ValueError('Missing channel pressure')
                driving.append({'channel_id':key,'x_center_m':c['x_center_m'],'row':c['row'],
                    'net_kg_s':c['net_kg_s'],'inlet_total_pa':up[key],'outlet_total_pa':down[key],
                    'drive_pa':up[key]-down[key]})
            result['channels'][name]['driving_pressure']=driving
            result['channels'][name]['pressure_origin']=driving_summary(driving)
            result['channels'][name]['flow_drive_correlation']=result['channels'][name]['pressure_origin']['flow_drive_correlation']
        end=[('inlet',pressure.get('inlet_total_pa'))]+[(s['id'],s['total_pa']) for s in stations]+[('outlet',pressure.get('outlet_total_pa'))]
        if any(v is None for _,v in end):raise ValueError('Boundary total pressure unavailable')
        # Five non-overlapping macro segments share identical station values.
        vals=dict(end)
        spans=[('inlet_to_top','入口至上挡板前','inlet','top_up'),('top_plate','上挡板附近','top_up','top_down'),
               ('wafer_region','晶圆及层间','top_down','bottom_up'),('bottom_plate','下挡板附近','bottom_up','bottom_down'),
               ('outlet_region','集气腔及排气通道','bottom_down','outlet')]
        result['segments']=[{'id':key,'name':label,'from':lo,'to':hi,'drop_pa':vals[lo]-vals[hi]} for key,label,lo,hi in spans]
        if abs(sum(s['drop_pa'] for s in result['segments'])-pressure['pressure_drop_pa'])>1e-10:
            raise ValueError('Segment sum disagrees with boundary pressure drop')
        result['ports']=[]
        for name,lo,hi in mesh['zones']:
            if not any(z['zone']==name for z in ports.get('boundary_zones',[])):continue
            ids=np.arange(lo,hi);tri=mesh['xyz'][mesh['conn'][ids]]
            area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)*.5
            result['ports'].append({'zone':name,'area_m2':float(area.sum())})
        result['status']='computed_candidate';result['pressure_status']='computed_cell_pair_proxy'
    except (ValueError,KeyError,IndexError,TypeError,MemoryError,OSError) as exc:
        result['pressure_status']='not_available';result['reason']=str(exc)[:350]
        result['segments']=[]
    return result


def compare_pressure_diagnostics(a,b):
    sa=a.get('pressure_stations') or [];sb=b.get('pressure_stations') or []
    needed={s.get(k) for model in (a,b) for s in model.get('segments',[]) for k in ('from','to')} - {None,'inlet','outlet'}
    if needed:
        sa=[s for s in sa if s['id'] in needed];sb=[s for s in sb if s['id'] in needed]
        if {s['id'] for s in sa}!=needed or {s['id'] for s in sb}!=needed:
            return {'same_physical_stations':False,'segments':[],'note':'共同压降测站缺失，拒绝输出差值。'}
    same=(bool(sa) and len(sa)==len(sb) and a.get('pressure_definition')==b.get('pressure_definition') and
          all(u['id']==v['id'] and abs(u['y_m']-v['y_m'])<=1e-9 for u,v in zip(sa,sb)))
    left={s['id']:s for s in a.get('segments',[])};right={s['id']:s for s in b.get('segments',[])}
    rows=[]
    for key,u in left.items():
        if key not in right:continue
        v=right[key]
        rows.append({'id':key,'name':u.get('name',key),'drop_pa':[u['drop_pa'],v['drop_pa']],
                     'delta_pa':v['drop_pa']-u['drop_pa'] if same else None})
    return {'same_physical_stations':same,'segments':rows,
            'note':'共同物理测站的总压代理比较；仍需核对工况、网格及收敛。' if same else '测站或定义不同，不发布分段压降差值。'}


def geometry_changes(a,b):
    def describe(r):
        g=r.get('geometry') or {};baffle=r.get('baffle') or {}
        return {'wafer_count':g.get('total_plates'),'channel_count':g.get('total_channels'),
            'inlets':[p.get('center_m') for z in (r.get('port_flux') or {}).get('boundary_zones',[]) if z.get('kind')=='inlet' for p in z.get('physical_ports',[])],
            'baffle_hole_count':baffle.get('hole_count'),'plate':(baffle.get('diagnostics') or {}).get('selected_plate'),
            'rows':[{'layer':l['id'],'x_lo_m':row['face_positions_lo_m'][0],
                     'x_hi_m':row['face_positions_hi_m'][-1],'wide_gap_count':len(row.get('wide_gap_index',[]))}
                    for l in g.get('layers',[]) for row in l.get('rows',[])]}
    return {'before':describe(a),'after':describe(b),
            'warning':'几何变化必须分项核对；不把所有结果变化自动归因于入口数。'}


def render_diagnostics_html(diag):
    from af_report_v3 import _table,_fmt,_e,_position_plot
    if not diag:return '<p>当前结果未计算偏流与分段总压定位；旧报告可保留基础统计。</p>'
    rows=[];figures=[]
    origins={'supply_side':'供气侧压力分配','downstream_side':'下游抽吸压力分配','mixed':'上下游共同变化','not_isolated':'未可靠区分'}
    for name,d in diag.get('channels',{}).items():
        weak=d.get('weakest') or {}
        rows.append([name,weak.get('channel_id'),_fmt(weak.get('x_center_m'),8),_fmt(weak.get('net_kg_s')),
            _fmt(d.get('weakest_mean_pct'))+'%',_fmt(d.get('axial_variance_pct'))+'%',_fmt(d.get('flow_drive_correlation')),
            origins.get((d.get('pressure_origin') or {}).get('origin'),'未计算')])
        driving=d.get('driving_pressure') or []
        if driving:
            points=[{'row':c['row'],'channel_id':c['channel_id'],'x_center_m':c['x_center_m'],
                     'net_kg_s':c['drive_pa']} for c in driving]
            # Reuse existing positional chart, replacing its unit label explicitly.
            chart=_position_plot(points,str(name)+' · 通道驱动压差')
            chart=chart.replace('质量流量','驱动压差').replace('片间流量空间分布','驱动压差空间分布')
            chart=chart.replace(' · Z=未取得 m · 正向=未取得 kg/s · 反向=未取得 kg/s','')
            chart=chart.replace('净量=','驱动压差=').replace('kg/s','Pa')
            figures.append(chart)
    html=_table(['层','最弱通道','X m','流量 kg/s','占本层均值','共同轴向方差占比','流量/驱动压差相关系数','压力变化主要来源（候选）'],rows)
    html+=''.join(figures)
    html+='<h3>质量流量加权分段总压（单元重构代理）</h3>'
    html+=_table(['区段','压降 Pa'],[[s.get('name',s['id']),_fmt(s['drop_pa'])] for s in diag.get('segments',[])])
    html+='<p>'+_e(diag.get('reason','内部总压由相邻单元重构；不是孤立构件阻力系数，也不是单因素因果结论。'))+'</p>'
    html+='<details><summary>定位测站与插值证据</summary>'+_table(['测站','Y m','流量 kg/s','总压 Pa','c0/c1差 Pa'],
        [[s['id'],_fmt(s['y_m'],8),_fmt(s['net_kg_s']),_fmt(s['total_pa']),_fmt(s['c0_total_pa']-s['c1_total_pa'])] for s in diag.get('pressure_stations',[])])+'</details>'
    return html


def render_comparison_html(result):
    from af_report_v3 import STYLE,_table,_e,_fmt,_section
    diag=result.get('flow_diagnostics') or {};body=[];rows=[]
    for name,r in result.get('layers',{}).items():
        mins=[]
        for source in diag.get('channel_diagnostics',[{},{}]):
            d=source.get(name) or {};mins.append((d.get('weakest') or {}).get('net_kg_s'))
        rows.append([name,str(r.get('channel_count')),
            ' / '.join(_fmt(v) for v in r.get('mass_fraction_pct',[])),
            ' / '.join(_fmt(v) for v in r.get('channel_cv_pct',[])),
            ' / '.join(_fmt(v) for v in mins),r.get('delta_scope','未核验')])
    body.append(_section('实际完整通道对照（前 / 后）',_table(['层','通道数','片间占比 %','CV %','最弱 kg/s','差值资格'],rows)))
    pressure=diag.get('pressure_comparison') or {}
    body.append(_section('同位置分段压降',_table(['区段','前 Pa','后 Pa','后减前 Pa'],[
        [s.get('name',s['id']),_fmt(s['drop_pa'][0]),_fmt(s['drop_pa'][1]),_fmt(s.get('delta_pa'))]
        for s in pressure.get('segments',[])])+'<p>'+_e(pressure.get('note','旧结果没有分段总压，需重新分析保存解。'))+'</p>'))
    body.append(_section('几何变化及可比性', '<pre>'+_e(__import__('json').dumps(diag.get('geometry_changes',{}),ensure_ascii=False,indent=2))+'</pre>'+''.join('<p>'+_e(r)+'</p>' for r in result.get('reasons',[]))))
    for i,channels in enumerate(diag.get('channel_diagnostics',[])):
        body.append(_section(('前模型' if i==0 else '后模型')+'偏流定位',render_diagnostics_html({'channels':channels})))
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AF 模型对比与定位</title><style>'+STYLE+'</style><main><header><h1>模型对比与偏流定位</h1><p>候选诊断；相同总流量不代表几何、工况及收敛完全一致。</p></header>'+''.join(body)+'</main></html>'
