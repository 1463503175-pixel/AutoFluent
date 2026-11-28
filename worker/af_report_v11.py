"""V11 presentation adapter; no solver, geometry inference or cached sample data."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import html
import json
import math
from pathlib import Path


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean(value):
    if isinstance(value, dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):return None
    return value


def adapt(metrics, synthetic_demo=False):
    layers=metrics.get('layers') or {}
    port=metrics.get('port_flux') or {}
    inlet=port.get('inlet_kg_s')
    if not finite(inlet) or inlet<=0:return None
    if set(layers)!= {'upper','lower'}:return None
    data={'flow':{},'channels':[],'holes':[],'baffle':[],'stages':[],'stations':[],
          'rawTables':[],'rawFigures':[],'pressureMap':{'bins':[],'outlets':[],
          'physical_coordinates_verified':False},'meta':clean(metrics)}
    for key,layer in layers.items():
        channels=layer.get('channels') or []
        if not channels or any(not finite(c.get('net_kg_s')) or c['net_kg_s']<=0
                               or not finite(c.get('x_center_m')) or not finite(c.get('z_center_m'))
                               for c in channels):return None
        avg=sum(c['net_kg_s'] for c in channels)/len(channels)
        if not finite(avg) or avg<=0:return None
        parts=layer.get('partition_net_kg_s') or {}
        def percent(k):
            v=parts.get(k)
            return 100*v/inlet if finite(v) else None
        f={'wafer':percent('wafer'),'central':percent('central'),'low':percent('outer_low_z'),
           'high':percent('outer_high_z'),'cv':layer.get('cv_pct'),
           'mass_cut':layer.get('whole_cut_net_kg_s'),'y':layer.get('cut_height_m'),
           'end':100*layer['outer_x_end_subset_kg_s']/inlet if finite(layer.get('outer_x_end_subset_kg_s')) else None}
        # shortcut: signed/reversing partitions use the full evidence view until signed stacks are supported.
        if any(not finite(f[k]) or f[k]<0 for k in ('wafer','central','low','high')):return None
        six=layer.get('six_regions_directional_kg_s') or {}
        f['six']={name:100*six[name]['net_kg_s']/inlet for name in ('wafer','central','left','right','front','back')} if all(name in six and finite(six[name].get('net_kg_s')) for name in ('wafer','central','left','right','front','back')) else None
        if f['six'] and any(v<0 for v in f['six'].values()):return None
        f['region_geometry']=layer.get('six_region_geometry')
        f['regions_available']=f['six'] is not None
        data['flow'][key]=f
        for c in channels:
            data['channels'].append({'layer':key,'row':c.get('row'), 'ch':str(c.get('channel_id','未编号')),
                'gap_index':c.get('gap_index'),'x':1000*c['x_center_m'],'z':1000*c['z_center_m'], 'net':c['net_kg_s'],
                'fwd':c.get('positive_kg_s'),'back':c.get('negative_magnitude_kg_s'),
                'ratio':100*c['net_kg_s']/avg,'x_range_m':c.get('x_range_m'),'z_range_m':c.get('z_range_m')})
    data['layerGeometry']={}
    for key,layer in layers.items():
        y=layer.get('cut_height_m')
        candidates=(metrics.get('geometry') or {}).get('layers',[])
        candidate=min(candidates,key=lambda g:abs(g.get('y_cut_m',1e9)-y)) if finite(y) and candidates else None
        data['layerGeometry'][key]=candidate if candidate and abs(candidate.get('y_cut_m',1e9)-y)<1e-7 else None
    baffle=metrics.get('baffle') or {}
    pair=metrics.get('baffles') or {}
    data['baffles']={'upper':adapt_baffle(pair.get('upper') or {},'上部进气挡板'),'lower':adapt_baffle(pair.get('lower') or baffle,'下部出气挡板')}
    data['holes']=data['baffles']['lower']['holes'];data['baffle']=data['baffles']['lower']['rows']
    pressure=metrics.get('pressure') or {}
    data['pressureDrop']=pressure.get('pressure_drop_pa') if pressure.get('status','').startswith('computed_pressure_proxy') else None
    data['massImbalance']=port.get('mass_imbalance_pct')
    data['stations']=[{'name':s.get('id','未编号'),'mean':s.get('mean_pa'),'y_m':s.get('y_m'),'thickness_m':s.get('thickness_m')} for s in pressure.get('stations',[])]
    data['stages']=[{'name':s.get('name','未编号'),'pa':s['drop_pa']} for s in pressure.get('segment_static_drop_pa',[]) if finite(s.get('drop_pa'))]
    cells=(baffle.get('lower_plenum_static_pressure_pa') or {}).get('xz_bins') or []
    cells=[c for c in cells if all(finite(c.get(k)) for k in ('x_lo_m','x_hi_m','z_lo_m','z_hi_m','mean_pa','cell_count'))]
    if cells:
        xmin=min(c['x_lo_m'] for c in cells);xmax=max(c['x_hi_m'] for c in cells)
        zmin=min(c['z_lo_m'] for c in cells);zmax=max(c['z_hi_m'] for c in cells)
        if xmax>xmin and zmax>zmin:
            for c in cells:
                if c['x_hi_m']<=c['x_lo_m'] or c['z_hi_m']<=c['z_lo_m']:continue
                data['pressureMap']['bins'].append({'x':49+777*(c['x_lo_m']-xmin)/(xmax-xmin),
                    'y':41+214*(c['z_lo_m']-zmin)/(zmax-zmin),'w':777*(c['x_hi_m']-c['x_lo_m'])/(xmax-xmin),
                    'h':214*(c['z_hi_m']-c['z_lo_m'])/(zmax-zmin),'mean_pa':c['mean_pa'],'n':c['cell_count'],
                    **{k:c[k] for k in ('x_lo_m','x_hi_m','z_lo_m','z_hi_m')}})
    data['pressureMap']['sample_y_range_m']=[baffle.get('floor_y_m'),baffle.get('baffle_lower_y_m')]
    data['pressureMap']['outlets']=[{'index':p.get('physical_port_index',i+1),'center_m':p['center_m'],'net_kg_s':p.get('net_kg_s'),'bounds_m':p.get('bounds_m')}
        for z in port.get('boundary_zones',[]) if z.get('kind')=='outlet'
        for i,p in enumerate(z.get('physical_ports',[])) if len(p.get('center_m') or [])==3 and all(finite(v) for v in p['center_m'])]
    data['pressureReference']=(pressure.get('reference') or {})
    data['spatialContext']=metrics.get('spatial_context') or {}
    provenance=metrics.get('provenance') or {}
    model=Path(str(provenance.get('case_path','未命名模型')).replace('\\','/')).name
    data.update(name=model,source=str(provenance.get('case_path','未取得')),
        reportMetadata={'model':model,'dataset_tag':'当前模型原生统计','synthetic_demo':synthetic_demo,
                        'geometry_validation':'candidate','convergence':'unknown'})
    data['rawTables']=[{'section':'通道原生统计','head':['层','通道','净流量 kg/s','X mm','Z mm'],
        'rows':[[c['layer'],c['ch'],c['net'],c['x'],c['z']] for c in data['channels']]}]
    for key,board in data['baffles'].items():
        if board['holes']:data['rawTables'].append({'section':board['label']+'逐孔流量','head':['挡板','孔号','X mm','Z mm','正向 kg/s','反向 kg/s','净流量 kg/s','相对本板均值 %'],'rows':[[board['label'],h['id'],h['x'],h['z'],h['fwd'],h['back'],h['net'],h['ratio']] for h in board['holes']]})
    data['analysis']=analyze(data)
    data['analysis']['boards']={key:board_insight(board) for key,board in data['baffles'].items()}
    upper=data['baffles']['upper']
    if upper['available']:
        data['analysis']['actions'].insert(1,{'title':'检查上部进气挡板的供气分配','evidence':board_insight(upper)[0],
            'hypothesis':'上游供气及各孔驱动压差的差异可能影响气体进入晶圆区的位置分配。',
            'check':'比较上挡板低流量孔、相邻孔两侧压力，并对照下游晶圆通道的X/Z位置。',
            'variable':'根据对应证据选择上游分配空间或局部孔阵列中的一个变量。',
            'accept':'上挡板逐孔CV、最弱孔及晶圆低流量区域一起比较。','target':'baffles','board':'upper'})
    for action in data['analysis']['actions']:
        action.setdefault('target','channels' if action['title'].startswith('定位') else 'baffles' if '挡板' in action['title'] else 'evidence')
        if action['target']=='baffles':action.setdefault('board','lower')
    data['analysis']['plain_summary']='这份报告主要看三件事：气体有没有充分经过晶圆、每个位置分到的气体是否接近、气体经过结构时压力怎样变化。' 
    return clean(data)


def render_enhanced_report(metrics, evidence, synthetic_demo=False):
    from af_species_exposure import render_section
    species_section=render_section(metrics.get('species_exposure'))
    from af_wall_reaction import render_wall_reaction,render_ald_stages
    species_section+=render_wall_reaction(metrics.get('wall_reaction'))+render_ald_stages(metrics)
    compact_metrics=dict(metrics)
    if metrics.get('wall_reaction'):
        compact_metrics['wall_reaction']={k:v for k,v in metrics['wall_reaction'].items() if k not in ('heat','water_heat')}
    data=adapt(compact_metrics,synthetic_demo)
    if data is None:
        # Keep supported partial/signed/single-layer statistics; never fabricate a two-layer layout.
        return evidence.replace('</main>',species_section+'</main>',1).replace('<body>','<body><p role="status">V11交互图不适用当前分区或流量方向；以下保留完整原生统计，缺项明确标注。</p>',1)
    root=Path(__file__).resolve().parents[1]/'templates'/'report_v11'
    template=(root/'report_template.html').read_text(encoding='utf-8')
    raw=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    raw=raw.replace('&',r'\u0026').replace('<',r'\u003c').replace('>',r'\u003e').replace('\u2028',r'\u2028').replace('\u2029',r'\u2029')
    replacements={'/*__AF_CSS__*/':(root/'report.css').read_text(encoding='utf-8'),
                  '/*__AF_JS__*/':(root/'report.js').read_text(encoding='utf-8'),'/*__AF_DATA__*/':raw}
    import re
    template=re.sub(r'/\*__AF_(?:CSS|JS|DATA)__\*/',lambda m:replacements[m.group()],template)
    # Keep only diagnostics with no dedicated chart or CSV in the enhanced report.
    unique_titles=('原生主分区正反向统计','中央 X/Z 子旁路正反向流量',
                   '来源、限制与核验记录','晶圆识别逐阶段诊断',
                   '挡板选板与孔轮廓诊断','来源和原生读取诊断','展开总压计算假设')
    archive=''.join(
        '<details class="panel space-top">'+block[len('<details>'):]
        for block,title in re.findall(r'(<details><summary>(.*?)</summary>.*?</details>)',evidence,re.S)
        if any(title.startswith(prefix) for prefix in unique_titles))
    template=template.replace('<section id="channels"',species_section+'<section id="channels"',1)
    return template.replace('<!--__AF_EVIDENCE__-->',archive,1)


def analyze(data):
    """Explain stored statistics without inferring new solver or geometry results."""
    label={'upper':'上层','lower':'下层'}
    def fmt(v,n=2):return f'{v:.{n}f}' if finite(v) else '未取得'
    summaries={}
    for key in label:
        channels=[c for c in data['channels'] if c['layer']==key]
        weak=[c for c in channels if c['ratio']<90]
        low=min(channels,key=lambda c:c['ratio'])
        summaries[key]={'minimum':low,'weak_count':len(weak),'count':len(channels),
            'rows':len({c['row'] for c in channels}),
            'weak_x_mm':[min(c['x'] for c in weak),max(c['x'] for c in weak)] if weak else None}
    focus=min(summaries,key=lambda k:summaries[k]['minimum']['ratio'])
    s=summaries[focus];w=s['minimum'];f=data['flow']
    diff=abs(f['upper']['wafer']-f['lower']['wafer'])
    cv=[k for k in label if finite(f[k].get('cv'))]
    cvtext='；'.join(label[k]+'CV为'+fmt(f[k]['cv'])+'%' for k in cv)
    if len(cv)==2 and f[cv[0]]['cv']!=f[cv[1]]['cv']:
        c=max(cv,key=lambda k:f[k]['cv']);cvtext+='，'+label[c]+'通道间的流量差异更大'
    region=('低流量通道的X位置覆盖'+fmt(s['weak_x_mm'][0])+'～'+fmt(s['weak_x_mm'][1])+' mm。') if s['weak_x_mm'] else '各通道均达到本层平均流量的90%。'
    weaktext=(label[focus]+'有'+str(s['weak_count'])+'条通道低于层均值的90%。') if s['weak_count'] else ''
    overview='上层和下层分别有'+fmt(f['upper']['wafer'])+'%和'+fmt(f['lower']['wafer'])+'%的入口净流量经过晶圆间隙，两层相差'+fmt(diff)+'个百分点。'+cvtext+'。'+weaktext+'最弱'+label[focus]+'通道'+w['ch']+'位于X='+fmt(w['x'])+' mm，为层均值的'+fmt(w['ratio'])+'%。'
    source=data['meta'];geometry=source.get('geometry') or {};inlet=(source.get('port_flux') or {}).get('inlet_kg_s')
    count=geometry.get('total_plates')
    research='本次分析气体在上下两层晶圆、中央与外围空间之间的分配，以及挡板和集气腔的压力变化。'
    research+='已识别'+(str(count)+'片晶圆、' if count is not None else '')+str(len(data['channels']))+'条片间通道。'
    if finite(inlet):research+='入口净质量流量为'+f'{inlet:.4e}'+' kg/s。'
    flow=[]
    for key in label:
        k=f[key];bypass=k['central']+k['low']+k['high']
        flow.append(label[key]+'每100份入口流量中，片间约'+fmt(k['wafer'])+'份，中央旁路'+fmt(k['central'])+'份，两侧外围合计'+fmt(k['low']+k['high'])+'份。已统计旁路合计'+fmt(bypass)+'%。')
    channel=[label[focus]+'最弱'+w['ch']+'的流量为'+f'{w["net"]:.4e}'+' kg/s，较层均值低'+fmt(100-w['ratio'])+'%。'+region,
        cvtext+'。总片间流量描述整层供气，各通道的低值和分布范围反映局部供气差异。',
        '优先检查X='+fmt(w['x'])+' mm附近的通道入口与出口压差，再与相邻通道、支撑件和挡板位置对照。']
    holes=data['holes'];rows=data['baffle'];hole=[]
    if holes:
        lo=min(holes,key=lambda h:h['net']);hi=max(holes,key=lambda h:h['net'])
        hole.append('共'+str(len(holes))+'个孔，最弱孔#'+str(lo['id'])+'为全板单孔均值的'+fmt(lo['ratio'])+'%，最强孔#'+str(hi['id'])+'为'+fmt(hi['ratio'])+'%。')
        if rows:
            top=max(rows,key=lambda r:r['mass']);avg=sum(h['net'] for h in holes)/len(holes)
            hole.append('第'+str(top['row'])+'排共'+str(top['n'])+'孔，总流量最高；平均每孔为全板单孔均值的'+fmt(100*top['mass']/top['n']/avg)+'%。孔数和平均单孔流量共同决定整排通过量。')
        else:hole.append('孔位与流量已经取得，孔排归属尚缺少可靠分组。')
        hole.append('先对照高低流量孔的空间分布，再检查孔径、孔两侧压差及邻近排气路径，确定值得调整的区域。')
    else:hole=['当前结果未取得逐孔流量或完整孔位。','通道与压力分析继续使用已有统计。','补充挡板孔位和对应面通量后，可比较各排与单孔分配。']
    stages=data['stages'];bins=data['pressureMap']['bins'];pressure=[]
    if stages:
        top=max(stages,key=lambda t:abs(t['pa']))
        pressure.append('已统计内部测站中，'+top['name']+'的静压变化幅度最大，上游减下游为'+fmt(top['pa'],5)+' Pa。')
    else:pressure.append('内部测站静压差尚未取得。')
    if bins:
        lo=min(bins,key=lambda p:p['mean_pa']);hi=max(bins,key=lambda p:p['mean_pa'])
        pressure.append('集气腔平均静压分箱范围为'+fmt(lo['mean_pa'],5)+'～'+fmt(hi['mean_pa'],5)+' Pa。最低值区域的X中心为'+fmt((lo['x_lo_m']+lo['x_hi_m'])*500)+' mm，Z中心为'+fmt((lo['z_lo_m']+lo['z_hi_m'])*500)+' mm。')
    else:pressure.append('当前结果未取得集气腔静压分箱。')
    pressure.append('将低流量通道上下游的压力与相邻正常通道对照，可检验局部驱动压差是否偏小。')
    actions=[{'title':'定位'+label[focus]+'低流量区','evidence':channel[0],
        'hypothesis':'局部通道的驱动压差或相邻流路阻力可能存在差异。',
        'check':'在最弱通道及相邻通道的上下游提取同口径压力和流向，核对空间对应。',
        'variable':'依据压差对照结果，选择供气分配、局部通流面积或下游排气路径中的一个变量试验。',
        'accept':'最弱通道流量、低流量通道数量与范围改善，同时记录两层CV、旁路及整体压降。'}]
    if holes:actions.append({'title':'检查挡板的局部分流差异','evidence':hole[0],
        'hypothesis':'孔径或孔两侧压差的空间差异可能影响逐孔分配。',
        'check':'对最强、最弱孔及邻近孔比较孔径、压差和流向；结合各排平均单孔流量。',
        'variable':'只对证据支持的局部区域选择一个孔阵列或流路变量。',
        'accept':'逐孔CV及极值差缩小，晶圆弱流指标同步检查。'})
    actions.append({'title':'统一下一轮比较条件','evidence':'当前入口流量、统计分区和各模块数值已保存在报告明细。',
        'hypothesis':'保持比较条件一致，才能判断结构调整的实际效果。',
        'check':'固定入口流量、材料、温度、统计截面，并记录网格策略与残差及监控历史。',
        'variable':'一次修改一个设计变量，保存原始方案作为基线。',
        'accept':'同时对照流量分配、最弱通道、CV、压降、质量守恒与实际收敛。'})
    return {'research':research,'overview':overview,'focus':focus,'summaries':summaries,
        'flow':flow+['两层片间占比相差'+fmt(diff)+'个百分点。继续比较逐通道分配，可识别整层总量中被平均的局部低值。'],
        'channel':channel,'baffle':hole,'pressure':pressure,'actions':actions,
        'diagnosis':['观察：'+weaktext+'最弱'+label[focus]+' '+w['ch']+'，X='+fmt(w['x'])+' mm。'+region,
        '待验证解释：局部压差、上游分配及下游排气路径可能共同影响流量。优先用同位置的压差和流向检验。',
        '验证顺序：先核对通道和孔位，再比较局部驱动压差，最后选择单变量结构试验。']}


def adapt_baffle(baffle, label):
    result={'label':label,'holes':[],'rows':[],'available':False,'status':baffle.get('status','not_identified'),'reason':baffle.get('reason','该保存结果尚未包含此挡板的逐孔统计'),'cut_y_m':baffle.get('cut_height_m'),'cv':baffle.get('all_hole_cv_pct'),'outline':baffle.get('outer_outline_xz_m',[])}
    holes=baffle.get('holes') or []
    if baffle.get('status')=='geometry_candidate_holes_native_flux_computed' and holes and all(
        finite(h.get('net_kg_s')) and h['net_kg_s']>0 and len(h.get('center_m') or [])==3
        and all(finite(v) for v in h['center_m']) for h in holes):
        avg=sum(h['net_kg_s'] for h in holes)/len(holes)
        # Group by measured Z coordinates, retaining explicit row counts from the native analysis.
        import statistics
        widths=[h.get('width_z_m') for h in holes]
        groups=[]
        if all(finite(w) and w>0 for w in widths):
            spacing=statistics.median(widths)
            for h in sorted(holes,key=lambda h:h['center_m'][2]):
                if not groups or h['center_m'][2]-groups[-1][-1]['center_m'][2]>spacing*.75:groups.append([])
                groups[-1].append(h)
        membership={id(h):i+1 for i,g in enumerate(groups) for h in g}
        for i,h in enumerate(holes):
            result['holes'].append({'id':i+1,'source_id':h.get('hole_id'),
                'x':1000*h['center_m'][0],'z':1000*h['center_m'][2],
                'row':membership.get(id(h)),'width_x_mm':1000*h['width_x_m'] if finite(h.get('width_x_m')) else None,'width_z_mm':1000*h['width_z_m'] if finite(h.get('width_z_m')) else None,
                'fwd':h.get('positive_kg_s'),'back':h.get('negative_magnitude_kg_s'),
                'net':h['net_kg_s'],'ratio':100*h['net_kg_s']/avg})
        for row in sorted({h['row'] for h in result['holes'] if h['row'] is not None}):
            group=[h for h in result['holes'] if h['row']==row]
            result['rows'].append({'row':row,'n':len(group),'mass':sum(h['net'] for h in group),
                                  'z':sum(h['z'] for h in group)/len(group)})
    result['available']=bool(result['holes'])
    return result


def board_insight(board):
    holes=board['holes'];rows=board['rows'];name=board['label']
    if not holes:return [name+'的逐孔流量或孔位尚未取得。','当前已有结果保留，另一块挡板单独显示。','使用包含双挡板统计的新分析结果后，可查看此板分配情况。']
    low=min(holes,key=lambda h:h['ratio']);high=max(holes,key=lambda h:h['ratio'])
    intro=name+'识别出'+str(len(holes))+'个孔、'+str(len(rows))+'排。最弱孔为本板单孔均值的'+f'{low["ratio"]:.2f}'+'%，最强孔为'+f'{high["ratio"]:.2f}'+'%。'
    role='它决定气体进入晶圆区前怎样分配。' if board['label'].startswith('上') else '它反映气体离开晶圆区时各位置的排气分配。'
    return [intro,role+'颜色和极值均相对于这块板自己的平均单孔流量。','对照低流量孔和相邻孔的孔径及两侧压力，检查分配差异来自哪里。']
