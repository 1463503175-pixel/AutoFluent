"""Snapshot-only mixture report, using the existing AF report design language."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import html
import math

STYLE='''
#species [hidden]{display:none!important}#species .species-top{display:flex;justify-content:space-between;gap:20px;align-items:center;background:#edf5f4;border:1px solid #cfdfdc;border-radius:14px;padding:18px 22px;margin-bottom:16px}#species .species-top strong{font-size:20px;color:#126f67}#species .species-top p{margin:3px 0;color:#506d76;font-size:12px}#species .species-controls{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:18px 0}#species select{font:inherit;max-width:100%;border:1px solid #adc8c4;border-radius:8px;padding:8px 12px;color:var(--ink);background:white}#species .species-note{font-size:11px;color:var(--muted);margin:7px 0 0}#species .species-findings{display:grid;grid-template-columns:1.45fr 1fr;gap:16px;margin-top:16px}#species .species-takeaway{font-size:18px;font-weight:750;line-height:1.6;margin:4px 0 13px;color:#1e4d59}#species svg{width:100%;height:auto;display:block}#species .species-chart-scroll{overflow-x:auto}#species .species-chart-scroll svg{min-width:700px}#species .species-table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.65;font-variant-numeric:tabular-nums}#species .species-table th{background:#f1f6f6;color:#59717a;font-size:11px;font-weight:750;white-space:nowrap}#species .species-table th,#species .species-table td{padding:12px 13px;text-align:right;border-bottom:1px solid #e5edef}#species .species-table th:first-child,#species .species-table td:first-child{text-align:left}#species .species-table tr:last-child td{border-bottom:0}#species .species-table tbody tr:hover{background:#f5faf9}#species .species-table td:first-child{white-space:nowrap;font-weight:650;color:#31525e}#species .species-section-caption{font-size:11px;color:#6e858d;line-height:1.8;margin:8px 0 0}#species .species-methods{margin-top:16px;padding:18px 22px;background:#f8fafb;border:1px solid #dce5e8;border-radius:12px}#species .species-methods summary{font-weight:750;cursor:pointer}#species .species-methods p{font-size:12px;color:#526c77;overflow-wrap:anywhere}#species .species-coverage{background:#f7faf9;padding:14px 17px;border-radius:10px;margin-top:14px}#species .species-coverage b{color:#1d6464}#species .species-coverage p{margin:5px 0;font-size:12px}#species .species-detail{min-height:64px}#species .species-channel-map rect[role=button]{cursor:pointer}#species .species-channel-map rect:focus-visible{outline:3px solid #122f3a;outline-offset:2px}#species .species-small{font-size:10px;fill:#627f89}#species .species-ylabels{font-size:12px;fill:#31525e}#species .species-header-right{font-size:12px;color:#476b72;text-align:right}#species .species-kpis{margin-bottom:16px}#species .species-kpis .kpi strong{font-size:clamp(21px,2vw,29px)}#species .species-table-wrap{overflow-x:auto}#species .species-limit{border-left:3px solid #bca275;padding:13px 16px;background:#faf7f1;margin-top:16px;font-size:12px;color:#6a5e43}#species .species-badge{font-size:11px;border-radius:6px;background:#e6efef;padding:4px 8px;color:#46656e}#species .species-subheading{display:flex;justify-content:space-between;align-items:center;gap:15px}#species .species-subheading h3{margin:0;font-size:16px}#species .species-legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:#607e87}#species .species-legend i{display:inline-block;width:11px;height:8px;margin-right:5px;border-radius:2px}#species .species-source{font-family:ui-monospace,monospace;font-size:10px;overflow-wrap:anywhere}#species .species-raw{margin-top:16px}#species .species-raw summary{cursor:pointer;font-size:12px;font-weight:700}#species .species-raw[open] summary{margin-bottom:14px}
@media(max-width:1050px){#species .species-findings{grid-template-columns:1fr}#species .species-top{align-items:flex-start}#species .species-top strong{font-size:17px}}
@media(max-width:650px){#species .species-top{display:block;padding:16px}#species .species-header-right{text-align:left;margin-top:9px}#species .species-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}#species .species-table th,#species .species-table td{padding:10px}#species .species-takeaway{font-size:16px}}
@media print{#species [data-species-panel][hidden]{display:none!important}#species .species-chart-scroll svg{min-width:0}#species .species-findings{display:block}#species .panel{break-inside:avoid;margin-bottom:12px}}
'''

def esc(x):return html.escape(str(x),quote=True)
def finite(x):return isinstance(x,(float,int)) and math.isfinite(x)
def pct(x):return f'{100*x:.4f}%' if finite(x) else '未取得'
def percent(x):return f'{x:.2f}%' if finite(x) else '未取得'
def region_name(r):return {'upper':'上层','lower':'下层','single':'单层'}.get(r['layer'],r['layer'])+' · 第'+str(r['row'])+'排'
def table(headers,rows):
    return '<div class="species-table-wrap"><table class="species-table"><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def grouped_mean(rows,name,field):
    stats=[r['species'][name].get(field) for r in rows];stats=[s for s in stats if s]
    weight='area_m2' if field in ('surface','wall_adjacent') else 'volume_m3'
    total=sum(s.get(weight,0) for s in stats)
    return sum(s['mean']*s.get(weight,0) for s in stats)/total if total else None

def comparison_chart(rows,name):
    colors=['#16877a','#497faa','#c18b4c'];fields=['bulk','surface','far_edge']
    values=[(r['species'][name].get(k) or {}).get('mean') for r in rows for k in fields]
    high=max([x for x in values if finite(x)]+[1e-12])*1.12
    width=680;height=52+len(rows)*89
    parts=[]
    for t in range(5):
        x=142+t*460/4
        parts.append(f'<path d="M{x} 18V{height-34}" stroke="#e4ecee"/><text x="{x}" y="{height-14}" text-anchor="middle" class="species-small">{100*high*t/4:.2f}%</text>')
    for i,r in enumerate(rows):
        y=33+i*89;parts.append(f'<text x="0" y="{y+20}" class="species-ylabels">{esc(region_name(r))}</text>')
        for j,k in enumerate(fields):
            v=(r['species'][name].get(k) or {}).get('mean')
            if not finite(v):continue
            length=460*v/high
            parts.append(f'<rect x="142" y="{y+j*17}" width="{length:.3f}" height="10" rx="3" fill="{colors[j]}"><title>{esc(k)} {pct(v)}</title></rect><text x="{min(650,148+length):.2f}" y="{y+j*17+9}" font-size="10" fill="#486772">{pct(v)}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="各分区当前时刻浓度比较">'+''.join(parts)+'</svg><div class="species-legend">'+''.join('<span><i style="background:'+c+'"></i>'+l+'</span>' for c,l in zip(colors,['片间体积均值','晶圆表面面积均值','远端边缘体积均值']))+'</div>'

def channel_map(channels,name,si):
    a=[c for c in channels if c.get('species',{}).get(name)]
    if not a:return '<p class="helper">未取得逐片间隙数据。</p>'
    groups=sorted({(c['layer'],c['row']) for c in a},key=lambda g:({'upper':0,'lower':1}.get(g[0],2),g[1]))
    xmin=min(c['x_range_m'][0] for c in a);xmax=max(c['x_range_m'][1] for c in a);span=max(xmax-xmin,1e-12)
    vmin=min(c['species'][name]['mean'] for c in a);vmax=max(c['species'][name]['mean'] for c in a)
    weak=min(a,key=lambda c:c['species'][name]['mean'])
    parts=[]
    for i,g in enumerate(groups):
        y=27+44*i
        parts.append(f'<text x="0" y="{y+20}" class="species-ylabels">{esc(region_name({"layer":g[0],"row":g[1]}))}</text>')
        for ch in a:
            if (ch['layer'],ch['row'])!=g:continue
            st=ch['species'][name];t=(st['mean']-vmin)/(vmax-vmin) if vmax>vmin else .5
            color='rgb('+','.join(str(round(a+(b-a)*t)) for a,b in zip((53,109,157),(199,115,64)))+')'
            x=150+850*(ch['x_range_m'][0]-xmin)/span;w=max(.5,850*(ch['x_range_m'][1]-ch['x_range_m'][0])/span)
            q=ch.get('native_net_kg_s');flow=f'{q:.4e} kg/s' if finite(q) else '未取得'
            detail=region_name(ch)+' · 间隙 '+str(ch['gap_index']+1)+' | X='+f"{ch['x_range_m'][0]*1000:.2f}～{ch['x_range_m'][1]*1000:.2f} mm"+' | 均值 '+pct(st['mean'])+'，最低 '+pct(st['min'])+'，P05 '+pct(st['p05'])+' | 净流量 '+flow
            stroke='#173c48' if ch is weak else '#fff'
            parts.append(f'<rect x="{x:.3f}" y="{y}" width="{w:.3f}" height="28" fill="{color}" stroke="{stroke}" stroke-width="{2 if ch is weak else .4}" tabindex="0" role="button" aria-label="{esc(detail)}" data-species-detail="{esc(detail)}" data-species-index="{si}"><title>{esc(detail)}</title></rect>')
    height=44*len(groups)+84
    for i in range(6):
        x=150+850*i/5
        parts.append(f'<text x="{x}" y="{height-43}" text-anchor="middle" class="species-small">{1000*(xmin+span*i/5):.1f}</text>')
    parts.append(f'<text x="1000" y="{height-22}" text-anchor="end" class="species-small">X / mm · 同一色标：{pct(vmin)} ～ {pct(vmax)}</text>')
    return f'<div class="species-chart-scroll"><svg class="species-channel-map" viewBox="0 0 1040 {height}" role="img" aria-label="当前帧逐通道浓度空间分布">'+''.join(parts)+'</svg></div>'+f'<div class="selection species-detail" id="speciesDetail{si}" aria-live="polite">点击色块查看同一时刻的通道浓度、X位置和净流量。深色外框标出平均浓度最低的通道。</div>'

def render_section(result):
    r=result or {'status':'not_available','reason':'此结果尚未计算物种分布，请重新分析保存解。'}
    time=r.get('flow_time_s');frame=f'{time:g} s 当前帧' if finite(time) else '保存帧时间未取得'
    rows=sorted(r.get('regions',[]),key=lambda x:({'upper':0,'lower':1}.get(x['layer'],2),x['row']))
    names=r.get('species',[])
    body='<style>'+STYLE+'</style><div class="chapter-heading"><span class="serial"></span><h2>混合气体组分与暴露证据</h2></div>'
    body+='<div class="species-top"><div><strong>'+esc(frame)+'</strong><p>以下浓度、极值与弱流区数据均来自同一时刻、不同空间位置；没有生成其他时刻的结果。</p></div><div class="species-header-right">'+str(len(names))+' 种组分 · '+str(len(r.get('channels',[])))+' 个片间通道<br>质量分数 / % · 每种气体可独立查看</div></div>'
    if not rows:
        return '<section id="species" class="section">'+body+'<article class="panel"><p>'+esc(r.get('reason','未取得分区数据。'))+'</p><p>无法判定吸附饱和；保留其他已取得的分析。</p></article></section>'
    def contrast(name):
        vals=[x['species'][name]['bulk']['mean'] for x in rows]
        return (max(vals)-min(vals))/max(sum(vals)/len(vals),1e-30)
    default=max(range(len(names)),key=lambda i:contrast(names[i]))
    body+='<div class="species-controls"><div class="segmented" role="group" aria-label="选择气体组分">'+''.join(f'<button type="button" data-species-choice="{i}" class="'+('active' if i==default else '')+'" aria-pressed="'+('true' if i==default else 'false')+'">'+esc(name)+'</button>' for i,name in enumerate(names))+'</div><label class="helper">全部组分 <select id="speciesSelect" aria-label="选择混合气体组分">'+''.join(f'<option value="{i}"'+(' selected' if i==default else '')+'>'+esc(name)+'</option>' for i,name in enumerate(names))+'</select></label></div>'
    for si,name in enumerate(names):
        panel='<div data-species-panel="'+str(si)+'"'+(' hidden' if si!=default else '')+'>'
        low=min(rows,key=lambda x:x['species'][name]['bulk']['mean']);high=max(rows,key=lambda x:x['species'][name]['bulk']['mean'])
        weakest=min(rows,key=lambda x:x['species'][name]['bulk']['min'])
        minimum=weakest['species'][name]['bulk']['min'];reference=r.get('inlet_mass_fractions',{}).get(name)
        kpis=[('保存解入口面',reference,'按进入域内的质量流量加权')]
        for key in dict.fromkeys(x['layer'] for x in rows):
            group=[x for x in rows if x['layer']==key]
            kpis.append(({'upper':'上层片间','lower':'下层片间','single':'片间区域'}.get(key,key),grouped_mean(group,name,'bulk'),'规则片间隙 · 体积加权'))
        kpis.append(('片间最低值',minimum,region_name(weakest)+' · 单元局部极值'))
        panel+='<div class="kpis species-kpis">'+''.join('<article class="kpi"><label>'+esc(label)+'</label><strong>'+pct(value)+'</strong><small>'+esc(note)+'</small></article>' for label,value,note in kpis)+'</div>'
        ratio=low['species'][name]['bulk']['mean']/high['species'][name]['bulk']['mean'] if high['species'][name]['bulk']['mean']>0 else None
        panel+='<div class="species-findings"><article class="panel"><div class="panel-head"><h3>从片间到表面，浓度相差多少？</h3><span class="species-badge">'+esc(name)+'</span></div>'+comparison_chart(rows,name)+'<p class="species-section-caption">纵列是空间分区，横轴是当前质量百分数，不是时间。表面值按真实三角形面积加权；远端指各排背向入口的一侧边缘。</p></article>'
        panel+='<article class="panel"><h3>当前帧可以确认什么</h3><p class="species-takeaway">'+esc(region_name(low))+'平均浓度为最高分区的 '+pct(ratio)+'</p><p>'+esc(name)+' 最低平均值为 '+pct(low['species'][name]['bulk']['mean'])+'，最高为 '+pct(high['species'][name]['bulk']['mean'])+'。这反映空间分布差异，不能单独推断反应完成程度。</p><div class="species-coverage"><b>相对入口的浓度参照</b><p>下列是低于入口参考浓度的体积占比，不是“未饱和比例”。</p>'+table(['空间分区','低于入口50%','低于入口90%'],[[region_name(x),percent(x['species'][name].get('below_half_inlet_volume_pct')),percent(x['species'][name].get('below_90pct_inlet_volume_pct'))] for x in rows])+'</div></article></div>'
        panel+='<article class="panel space-top"><div class="panel-head"><h3>浓度低值集中在哪些片间通道？</h3><span class="helper">'+esc(frame)+'</span></div>'+channel_map(r.get('channels',[]),name,si)+'<p class="species-section-caption">色块按真实X范围排列，每块代表一个通道的体积平均浓度；白色间隔包含晶圆和未纳入的区域。这是通道统计图，非 Fluent 连续场云图。</p></article>'
        panel+='<div class="two-col space-top"><article class="panel"><h3>远端、低流量与低速区</h3>'+table(['分区','远端均值 / 最低','低流量均值 / 最低','低速均值 / 最低'],[[region_name(x)]+[' / '.join(pct((x['species'][name].get(k) or {}).get(v)) for v in ('mean','min')) for k in ('far_edge','low_flow','low_speed')] for x in rows])+'<p class="species-section-caption">低流量：各层原生通道净流量最低10%分位阈值以下的通道；低速：各排片间速度最低10%分位阈值以下的单元。它们不是同一个集合。</p></article><article class="panel"><h3>晶圆片间朝向表面</h3>'+table(['分区','面积均值','最低','远端表面均值','近壁均值'],[[region_name(x),pct((x['species'][name].get('surface') or {}).get('mean')),pct((x['species'][name].get('surface') or {}).get('min')),pct((x['species'][name].get('surface_far_edge') or {}).get('mean')),pct((x['species'][name].get('wall_adjacent') or {}).get('mean'))] for x in rows])+'<p class="species-section-caption">表面质量分数来自保存的壁面 SV_Y；近壁值来自相邻流体单元，均按对应壁面面积加权。仅纳入朝向规则片间隙的实际晶圆面，首末片外侧面除外。</p></article></div>'
        panel+='<article class="panel space-top"><h3>分区浓度完整数值</h3>'+table(['分区','体积均值','最低','第5百分位','最高','均值 / 入口'],[[region_name(x)]+[pct(x['species'][name]['bulk'][k]) for k in ('mean','min','p05','max')]+[pct(x['species'][name].get('mean_inlet_ratio'))] for x in rows])
        where=weakest['species'][name].get('minimum_position_m')
        if where:panel+='<p class="species-section-caption">本组分片间最低值位置：X/Y/Z = '+esc(' / '.join(f'{x*1000:.2f}' for x in where))+' mm。该点仅对应 '+esc(frame)+'。</p>'
        panel+='<details class="species-raw"><summary>展开全部 '+str(len(r.get('channels',[])))+' 个通道明细</summary>'+table(['分区 / 间隙','X范围 mm','体积均值','最低','第5百分位','最高'],[[region_name(ch)+' / '+str(ch['gap_index']+1),' ～ '.join(f'{x*1000:.2f}' for x in ch['x_range_m'])]+[pct((ch['species'].get(name) or {}).get(k)) for k in ('mean','min','p05','max')] for ch in r.get('channels',[])])+'</details></article></div>'
        body+=panel
    reaction='未启用' if r.get('reaction_enabled') is False else ('已启用' if r.get('reaction_enabled') is True else '未取得')
    body+='<div class="species-limit"><b>暴露与饱和证据</b><br>当前反应模型：'+reaction+'。'+esc(r.get('saturation_status','无法判定：没有取得局部分压时间积分和工况饱和判据。'))+' 本页不将末端浓度乘以总时长冒充真实剂量。</div>'
    body+='<details class="species-methods"><summary>这些数据从哪里来，为什么只有一个时间也能得到？</summary><p>1. 保存的 Data 同时包含 '+esc(frame)+' 上各单元、壁面及入口面的空间场。均值、最低、最高、远端、低流量、低速统计均从这同一帧筛选，不需要额外求解其他时间。</p><p>2. 第5百分位（P05）是按体积权重排序后累计5%处的浓度值，不是0.05 s。低于入口50%/90%只是相对浓度参照，不是通用ALD工艺达标线。</p><p>3. 片间均值使用四面体体积加权；晶圆表面使用真实三角形面积加权；入口使用保存面场的进入质量流量加权。此前手工单元数平均、表面平均与本页体积平均的范围/权重不同，不应混作同一个数。</p><p>4. '+esc(r.get('weighting',''))+' '+esc(r.get('spatial_axes',''))+' 表面字段或物理分区缺失时明确显示未取得，不填补推测值。</p><p>5. 默认展示分区相对差异最大的组分，可切换所有真实物种；保留Case原名，未擅自指定前驱体或产物。</p><p class="species-source">Case: '+esc(r.get('source_case','未取得'))+'<br>Data: '+esc(r.get('source_data','未取得'))+'<br>物种字段：cells/SV_Y、faces/SV_Y；低流量：原生SV_FLUX；低速：SV_U/V/W。质量分数闭合误差 '+esc(r.get('mass_fraction_max_closure_error','未取得'))+'</p></details>'
    return '<section id="species" class="section">'+body+'</section>'
