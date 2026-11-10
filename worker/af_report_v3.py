# -*- coding: utf-8 -*-
"""Pure renderer: native structured data -> single-file, offline, printable HTML.

This module MUST NOT import h5py, Fluent/Workbench, or read mesh/solver data.
UI structure follows the supplied 2.0 report: overview, paths, positions,
baffle, pressure, trust, comparison, next steps, expandable provenance.
Numbers are drawn exclusively from an analysis result. No cached example values.
"""
from __future__ import annotations
from html import escape
import json
import math


STYLE = r'''
:root{color-scheme:light;--ink:#203946;--muted:#536b77;--teal:#087e8b;--low:#5574a5;--high:#6e6688;
--blue:#0072b2;--orange:#d55e00;--line:#d6e0e6;--bg:#f1f5f7}
*{box-sizing:border-box}body{background:var(--bg);font:15px/1.65 system-ui,"Microsoft YaHei UI","PingFang SC",sans-serif;color:var(--ink);margin:0;padding:24px}
main{max-width:1080px;margin:0 auto}header,section{background:#fff;border-radius:12px;padding:clamp(18px,3.3vw,30px);margin-bottom:18px}
header{background:#133644;color:#fff}header h1{font-size:clamp(23px,3.6vw,36px);line-height:1.23;margin:4px 0 10px}
header p{margin:8px 0}header .eyebrow{color:#ccdde3;font-size:13px}
section h2{font-size:20px;margin:0 0 14px}h3{font-size:16px;margin:19px 0 8px}
.pills{display:flex;flex-wrap:wrap;gap:8px}.pill{font-size:12px;padding:3px 10px;background:#eaf4f8;border-radius:25px;color:#23526a}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.card{background:white;border-radius:11px;padding:16px;border:1px solid #e1e8eb}.card small,.card strong{display:block}
.card strong{font-size:21px;font-variant-numeric:tabular-nums}.card small{color:var(--muted)}
.small{font-size:12px;color:var(--muted)}.muted{color:var(--muted)}.notavailable{color:#8b5860}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin:10px 0 20px}
tr+tr{border-top:1px solid var(--line)}th,td{padding:9px 10px;white-space:nowrap;text-align:left}thead{background:#f0f5f7}
.bar{display:flex;height:26px;overflow:hidden;background:#e8edf0;border-radius:6px;margin:8px 0 13px}
.bar span{display:block;font-size:11px;text-align:center;color:#fff;line-height:26px;overflow:hidden;white-space:nowrap}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;margin:6px 0 16px}
.legend b{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.chart{width:100%;height:auto;max-height:390px;background:#fafcfd;border:1px solid var(--line);border-radius:9px}
svg text{font:11px system-ui,sans-serif;fill:#47616c}.gridline{stroke:#dce6ea;stroke-width:1}
.note{border-left:3px solid #087e8b;background:#f4f9fa;padding:11px 14px;margin:12px 0}
.warn{border-left:3px solid #b25222;background:#fbf6f1;padding:11px 14px;margin:12px 0}
details{border-top:1px solid var(--line);padding:12px 0}summary{cursor:pointer;font-weight:600}
pre{white-space:pre-wrap;word-break:break-all;font-size:11px;background:#fafcfd;padding:12px}
code{font-size:12px} .foot{font-size:12px;color:var(--muted)}
@media print{body{background:#fff;padding:0}main{max-width:none}header,section,.card{break-inside:avoid;box-shadow:none;border:1px solid #cbd8de;border-radius:0}header{print-color-adjust:exact;-webkit-print-color-adjust:exact}.chart,.bar{break-inside:avoid;print-color-adjust:exact;-webkit-print-color-adjust:exact}details{break-inside:auto}}
'''


def _e(value):
    return escape(str(value), quote=True)


def _fmt(value, digits=4):
    if value is None:
        return '未取得'
    try:
        number=float(value)
    except (TypeError,ValueError):
        return '未取得'
    if not math.isfinite(number):
        return '未取得'
    return format(number,'.%dg'%digits)


def _td(value):
    return '<td>'+_e(value)+'</td>'


def _table(headers, rows):
    if not rows:
        return '<p class="notavailable">未取得可核验数据。</p>'
    return ('<div class="scroll"><table><thead><tr>'+''.join('<th>'+_e(h)+'</th>' for h in headers)+
            '</tr></thead><tbody>'+''.join('<tr>'+''.join(_td(v) for v in row)+'</tr>' for row in rows)+
            '</tbody></table></div>')


def _cards(items):
    return '<div class="cards">'+''.join('<div class="card"><small>'+_e(label)+'</small><strong>'+_e(value)+'</strong></div>'
                                        for label,value in items)+'</div>'


def _get_layers(metrics):
    return metrics.get('layers',{}) if isinstance(metrics.get('layers'),dict) else {}


def _fractions_bar(layer, inlet):
    names=[('wafer','晶圆片间','var(--teal)'),('central','中央旁路','var(--muted)'),
           ('outer_low_z','低 Z 外围','var(--low)'),('outer_high_z','高 Z 外围','var(--high)')]
    vals=layer.get('partition_net_kg_s',{})
    if not isinstance(inlet,(int,float)) or inlet<=0 or not vals:
        return '<p class="notavailable">未取得可核验的分区占比。</p>'
    shares=[(key,label,color,float(vals.get(key,0))/float(inlet)*100) for key,label,color in names]
    # Signed net flow with recirculation cannot be represented as a 100% stack.
    if any(x[3]<-1e-8 for x in shares) or sum(x[3] for x in shares)>100.5:
        return '<p class="warn">存在负向净通量或总占比超过 100%，不绘制可能误导的堆叠条；详见正反向统计表。</p>'
    return '<div class="bar">'+''.join('<span style="width:%.5f%%;background:%s" title="%s %s%%">%s</span>'%
                 (max(0,min(100,share)),color,_e(label),_fmt(share),
                  _fmt(share)+'%' if share>=10 else '') for _,label,color,share in shares)+'</div>'


def _position_plot(channels,title, *, common_y_range=None):
    # Each row has its own X-position set; staggered rows are never treated
    # as a single interleaved series.
    byrow={}
    for channel in channels:
        if channel.get('x_center_m') is not None and channel.get('net_kg_s') is not None:
            byrow.setdefault(int(channel.get('row',0)),[]).append(channel)
    if not byrow:
        return '<p class="notavailable">通道空间坐标未取得；不使用通道序号伪装物理 X 坐标。</p>'
    xvals=[float(c['x_center_m']) for points in byrow.values() for c in points]
    yvals=[float(c['net_kg_s']) for points in byrow.values() for c in points]
    if not all(math.isfinite(x) for x in xvals+yvals):
        return '<p class="notavailable">通道坐标或流量存在非有限值。</p>'
    xmin,xmax=min(xvals),max(xvals);ymin,ymax=(common_y_range if common_y_range is not None else (min(yvals),max(yvals)))
    if xmax<=xmin:xmax=xmin+1e-6
    if ymax<=ymin:ymax=ymin+max(1e-15,abs(ymin)*.1)
    xlo,xhi=75,825;ylo,yhi=248,35
    def pos(x,y):
        return (xlo+(x-xmin)/(xmax-xmin)*(xhi-xlo),ylo-(y-ymin)/(ymax-ymin)*(ylo-yhi))
    lines=[]
    for ix in range(5):
        y=ymin+(ymax-ymin)*ix/4
        sy=pos(xmin,y)[1]
        lines.append('<line x1="75" x2="825" y1="%.2f" y2="%.2f" class="gridline"/>'%(sy,sy))
        lines.append('<text x="9" y="%.2f">%s</text>'%(sy+4,_e(_fmt(y,3))))
    for ix in range(5):
        x=xmin+(xmax-xmin)*ix/4
        sx=pos(x,ymin)[0]
        lines.append('<text x="%.1f" y="268" text-anchor="middle">%s</text>'%(sx,_e(_fmt(x,3))))
    colors=['#0072b2','#d55e00','#6e6688','#087e8b']
    for index,(row,points) in enumerate(sorted(byrow.items())):
        points=sorted(points,key=lambda c:float(c['x_center_m']))
        color=colors[index%len(colors)]
        coords=' '.join('%.1f,%.1f'%pos(float(c['x_center_m']),float(c['net_kg_s'])) for c in points)
        lines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.8"/>'%(coords,color))
        for ch in points:
            sx,sy=pos(float(ch['x_center_m']),float(ch['net_kg_s']))
            info=('层 %s · 排 %s · 通道 %s · X=%s m · Z=%s m · 正向=%s kg/s · 反向=%s kg/s · 净量=%s kg/s' % (
                title,row,ch.get('channel_id',''),_fmt(ch.get('x_center_m'),8),
                _fmt(ch.get('z_center_m'),8),_fmt(ch.get('positive_kg_s'),8),
                _fmt(ch.get('negative_magnitude_kg_s'),8),_fmt(ch.get('net_kg_s'),8)))
            lines.append('<circle cx="%.2f" cy="%.2f" r="2.3" fill="%s"><title>%s</title></circle>'%
                         (sx,sy,color,_e(info)))
        for ch in (min(points,key=lambda c:c['net_kg_s']),max(points,key=lambda c:c['net_kg_s'])):
            sx,sy=pos(float(ch['x_center_m']),float(ch['net_kg_s']))
            lines.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s"><title>%s</title></circle>'%
                         (sx,sy,color,_e('第 %d 排 %s X=%s m, %s kg/s'%(
                             row,ch.get('channel_id',''),_fmt(ch['x_center_m']),_fmt(ch['net_kg_s'])))))
        lines.append('<text x="%d" y="%d" fill="%s">第 %d 排</text>'%(690,15+14*index,color,row))
    lines.append('<text x="795" y="286">X (m)</text>')
    lines.append('<text x="75" y="18">质量流量 kg/s（真实 X 坐标）</text>')
    return ('<h3>'+_e(title)+'</h3><svg class="chart" viewBox="0 0 860 300" role="img" aria-label="'+
            _e(title+' 片间流量空间分布')+'">'+''.join(lines)+'</svg>')


def _hole_svg(baffle, port=None):
    holes=baffle.get('holes') or []
    if not holes:return '<p class="notavailable">没有通过真实孔位识别的挡板孔；孔位图未取得。</p>'
    coords=[v.get('center_m') for v in holes if isinstance(v.get('center_m'),list) and len(v['center_m'])==3]
    if len(coords)!=len(holes):return '<p class="notavailable">孔位坐标不完整。</p>'
    xs=[float(c[0]) for c in coords];zs=[float(c[2]) for c in coords]
    if max(xs)<=min(xs) or max(zs)<=min(zs):return '<p class="notavailable">孔位范围退化。</p>'
    flows=[float(h['net_kg_s']) for h in holes]
    low,high=min(flows),max(flows)
    marks=[]
    for i,(h,c) in enumerate(zip(holes,coords)):
        x=45+760*(c[0]-min(xs))/(max(xs)-min(xs))
        y=258-222*(c[2]-min(zs))/(max(zs)-min(zs))
        shade=max(0,min(1,(flows[i]-low)/(high-low))) if high>low else .5
        color='#b25222' if shade<.2 else ('#087e8b' if shade>.8 else '#5574a5')
        marks.append('<circle cx="%.1f" cy="%.1f" r="4.2" fill="%s"><title>孔 %d：X=%s, Z=%s，流量=%s kg/s</title></circle>'%
                     (x,y,color,i+1,_e(_fmt(c[0])),_e(_fmt(c[2])),_e(_fmt(flows[i]))))
    for idx,label in ((flows.index(min(flows)),'最低'),(flows.index(max(flows)),'最高')):
        c=coords[idx];xx=45+760*(c[0]-min(xs))/(max(xs));yy=258-222*(c[2]-min(zs))/(max(zs))
        marks.append('<circle cx="%.1f" cy="%.1f" r="9" stroke="#b25222" stroke-width="2" fill="none"><title>%s孔：%s kg/s</title></circle>'%
            (xx,yy,_e(label),_e(_fmt(flows[idx],9))))
    for zone in (port or {}).get('boundary_zones',[]):
        if zone.get('kind')!='outlet':continue
        for loc in zone.get('physical_ports',[]):
            c=loc.get('center_m')
            if not c or len(c)<3:continue
            xx=45+760*(c[0]-min(xs))/(max(xs));yy=258-222*(c[2]-min(zs))/(max(zs))
            marks.append('<circle cx="%.1f" cy="%.1f" r="7" stroke="#181818" stroke-width="2" fill="none"><title>出口投影：%s</title></circle>'%
                         (xx,yy,_e(zone.get('zone','出口'))))
    marks.append('<text x="45" y="20">X–Z 原生孔位（红圈极值，黑圈出口投影）</text>')
    marks.append('<text x="760" y="288">X (m)</text>')
    marks.append('<text x="7" y="30">Z</text>')
    return '<svg class="chart" viewBox="0 0 860 305" role="img" aria-label="真实挡板孔位">'+''.join(marks)+'</svg>'


def _baffle_row_histogram(baffle):
    """True per-row net flux, with a count-proportional equal-hole reference."""
    rows=baffle.get('rows') or []
    if not rows:return '<p class="notavailable">孔排数据未取得。</p>'
    if any(r.get('net_kg_s',0)<0 for r in rows):
        return '<p class="warn">部分孔排存在负向净通量，不绘制易误读的正向柱状图。</p>'
    total=sum(float(r['net_kg_s']) for r in rows)
    holes=sum(int(r.get('hole_count',0)) for r in rows)
    if holes<=0 or total<=0:return '<p class="notavailable">没有正向孔排流量。</p>'
    base=[total*float(r['hole_count'])/holes for r in rows]
    scale=max([float(r['net_kg_s']) for r in rows]+base)*1.16
    equal_counts=len({r['hole_count'] for r in rows})==1
    step=740/len(rows)
    width=min(72,step*.60)
    lines=['<text x="60" y="26" style="font-size:18px">排净流量（10⁻⁶ kg/s）</text>']
    for i,(r,reference) in enumerate(zip(rows,base)):
        x=60+(i+.5)*step
        h=175*float(r['net_kg_s'])/scale
        ref=175*reference/scale
        fill='#b25222' if float(r['net_kg_s'])>reference else '#087e8b'
        lines.append(f'<rect x="{x-width/2:.1f}" y="{224-h:.1f}" width="{width:.1f}" height="{h:.1f}" fill="{fill}"><title>第 {_e(r.get("index"))} 排：{_e(_fmt(r["net_kg_s"],9))} kg/s</title></rect>')
        if not equal_counts:
            lines.append(f'<path d="M{x-width/2:.1f} {224-ref:.1f}h{width:.1f}" stroke="#536b77" stroke-dasharray="5 4"/>')
    if equal_counts:
        lines.append(f'<path d="M60 {224-175*base[0]/scale:.1f}H800" stroke="#536b77" stroke-dasharray="6 4"/>')
    for i,r in enumerate(rows):
        x=60+(i+.5)*step
        value=float(r['net_kg_s'])
        lines.append(f'<text x="{x:.1f}" y="{215-175*value/scale:.1f}" text-anchor="middle" style="font-size:17px;paint-order:stroke;stroke:#fff;stroke-width:3px">{value*1e6:.3f}</text>')
        lines.append(f'<text x="{x:.1f}" y="249" text-anchor="middle" style="font-size:16px">第{_e(r.get("index"))}排</text>')
    reference_note='各排平均流量' if equal_counts else '按孔数比例分配的各排参考流量'
    return '<svg class="chart" viewBox="0 0 860 284" role="img" aria-label="底部挡板各排净流量柱图">'+''.join(lines)+f'<text x="60" y="274" style="font-size:14px">虚线：{reference_note}　橙色：高于参考值</text></svg>'


def _plenum_pressure_svg(baffle, port=None):
    info=baffle.get('lower_plenum_static_pressure_pa') or {}
    cells=info.get('xz_bins') or []
    if not cells:return '<p class="notavailable">集气腔 X–Z 空间静压样本不足，未生成模拟云图。</p>'
    xmin=min(c['x_lo_m'] for c in cells);xmax=max(c['x_hi_m'] for c in cells)
    zmin=min(c['z_lo_m'] for c in cells);zmax=max(c['z_hi_m'] for c in cells)
    lo=min(c['mean_pa'] for c in cells);hi=max(c['mean_pa'] for c in cells)
    if xmax<=xmin or zmax<=zmin:return '<p class="notavailable">压力空间坐标退化。</p>'
    rects=[]
    for c in cells:
        x=56+760*(c['x_lo_m']-xmin)/(xmax-xmin);y=254-204*(c['z_hi_m']-zmin)/(zmax-zmin)
        w=760*(c['x_hi_m']-c['x_lo_m'])/(xmax-xmin);h=204*(c['z_hi_m']-c['z_lo_m'])/(zmax-zmin)
        t=(c['mean_pa']-lo)/(hi-lo) if hi>lo else 0.5
        red=int(45+196*t);blue=int(180-112*t);green=int(112+29*t)
        rects.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="rgb(%d,%d,%d)"><title>真实单元中心样本 %d 个 · 静压均值 %s Pa</title></rect>'%
                     (x,y,w,h,red,green,blue,c['cell_count'],_e(_fmt(c['mean_pa'],9))))
    for zone in (port or {}).get('boundary_zones',[]):
        if zone.get('kind')!='outlet':continue
        for loc in zone.get('physical_ports',[]):
            c=loc.get('center_m')
            if not c:continue
            x=56+760*(c[0]-xmin)/(xmax-xmin);y=254-204*(c[2]-zmin)/(zmax-zmin)
            rects.append('<circle cx="%.2f" cy="%.2f" r="7" fill="none" stroke="#101010" stroke-width="2"><title>%s 出口投影</title></circle>'%(x,y,_e(zone.get('zone','出口'))))
    rects.append('<text x="56" y="20">集气腔 X–Z 实际单元中心静压分箱（非 Fluent 原生云图）</text>')
    rects.append('<text x="56" y="287">静压范围 %s ~ %s Pa；仅占有真实样本的区域绘制</text>'%(_fmt(lo,7),_fmt(hi,7)))
    return '<svg class="chart" viewBox="0 0 860 305" role="img" aria-label="真实采样 X Z 静压分布">'+''.join(rects)+'</svg>'


def _section(name,body):
    return '<section><h2>'+_e(name)+'</h2>'+body+'</section>'


def render_native_report(metrics, *, synthetic_demo=False):
    if metrics.get('schema')!='autofluent.adaptive_native.v1':
        raise ValueError('Expected a structured adaptive-native analysis result; refuse mixed report input')
    provenance=metrics.get('provenance') or {}
    layers=_get_layers(metrics)
    port=metrics.get('port_flux') or {}
    baffle=metrics.get('baffle') or {}
    pressure=metrics.get('pressure') or {}
    inlet=port.get('inlet_kg_s')
    model=provenance.get('case_path','未知模型').replace('\\','/').split('/')[-1]
    subtitle=('合成数据测试专用 · 不代表真实 Fluent 保存解' if synthetic_demo else ('11.9 严格专用几何原生统计 · 未独立验证收敛' if provenance.get('report_profile')=='11_9_validated_reference' else 'Fluent Case/Data · 原生面通量统计 · 几何候选结果'))
    layer_label={'upper':'上层','lower':'下层'}
    layers={layer_label.get(k,k):layers[k] for k in sorted(layers,key=lambda k:({'upper':0,'lower':1}.get(k,2),k))}
    findings=[]
    for name,layer in layers.items():
        share=layer.get('share_inlet_pct')
        if isinstance(share,(int,float)) and math.isfinite(share):
            findings.append(f'以入口净流量为 100 份，{name}约有 {_fmt(share)} 份从晶圆片之间通过。')
    if baffle.get('status')=='geometry_candidate_holes_native_flux_computed' and baffle.get('all_hole_cv_pct') is not None:
        rows=baffle.get('rows') or []
        if rows and all(isinstance(r.get('net_kg_s'),(int,float)) for r in rows):
            largest=max(rows,key=lambda r:r['net_kg_s'])
            total=sum(r['net_kg_s'] for r in rows)
            if total>0:
                findings.append(f'底部挡板第 {largest["index"]} 排通过的气体最多，约占整个挡板净流量的 {100*largest["net_kg_s"]/total:.1f}%。')
    if pressure.get('pressure_drop_pa') is not None:
        findings.append(f'气体从入口流到出口，总压约下降 {_fmt(pressure["pressure_drop_pa"],6)} Pa。')
    header=('<header><div class="eyebrow">AutoFluent / ALD 流动分析 / 独立离线报告</div>'
            '<h1>气体怎样通过晶圆和挡板？</h1><p>'+_e(subtitle)+'</p><p><strong>'+_e(''.join(findings))+'</strong></p>'
            '<p>方案：<strong>'+_e(model)+'</strong></p>'
            '<p class="eyebrow">数据来源：原生 SV_FLUX；'+
            ('11.9 严格几何专用方法，不能推广到其他模型' if provenance.get('report_profile')=='11_9_validated_reference' else ('显式配置真实几何识别' if provenance.get('config_mode')=='explicit_verified_real_geometry' else '自动候选几何识别'))+
            '；没有单独的迭代记录时不得认定求解收敛。</p></header>')
    summary=[('晶圆总数',_fmt((metrics.get('geometry') or {}).get('total_plates'),6)),
             ('有效片间通道',_fmt((metrics.get('geometry') or {}).get('total_channels'),6)),
             ('入口质量流量',_fmt(inlet)+' kg/s'),
             ('进出口净差',_fmt(port.get('mass_imbalance_pct'))+'%'),
             ('端口总压降',(_fmt(pressure.get('pressure_drop_pa'))+' Pa' if pressure.get('status','').startswith('computed_pressure_proxy') else '未取得')),
             ('挡板孔数',str(baffle.get('hole_count')) if baffle.get('status')=='geometry_candidate_holes_native_flux_computed' else '未取得')]
    body=[_cards(summary)]
    flow=[]
    flow_rows=[]
    palette=[('晶圆片间','#087e8b'),('中央旁路','#536b77'),('低 Z 外围','#5574a5'),('高 Z 外围','#6e6688')]
    flow.append('<div class="legend">'+''.join('<span><b style="background:%s"></b>%s</span>'%(color,label) for label,color in palette)+'</div>')
    for lname,layer in layers.items():
        flow.append('<h3>'+_e(lname)+' · '+_fmt(layer.get('channel_count'),6)+' 条通道</h3>')
        flow.append(_fractions_bar(layer,inlet))
        parts=layer.get('partition_net_kg_s',{})
        flow_rows.append([lname,_fmt(parts.get('wafer')),_fmt(parts.get('central')),
                          _fmt(parts.get('outer_low_z')),_fmt(parts.get('outer_high_z')),
                          _fmt(layer.get('whole_cut_net_kg_s')),
                          _fmt(layer.get('share_inlet_pct'))+'%',_fmt(layer.get('cv_pct'))+'%'])
    flow.append(_table(['截面','片间 kg/s','中央 kg/s','低侧 kg/s','高侧 kg/s','完整截面 kg/s','片间占入口','通道 CV'],flow_rows))
    if isinstance(inlet,(int,float)) and inlet>0:
        def share_text(value):
            return f'{100*value/inlet:.3f}%' if isinstance(value,(int,float)) and math.isfinite(value) else '未取得'
        share_rows=[]
        for name,layer in layers.items():
            parts=layer.get('partition_net_kg_s') or {}
            share_rows.append([name,*(share_text(parts.get(k)) for k in ('wafer','central','outer_low_z','outer_high_z')),
                               share_text(layer.get('whole_cut_net_kg_s'))])
        flow.append(_table(['截面','片间','中央','低 Z 侧','高 Z 侧','完整截面'],share_rows))
    flow.append('<p>怎么看这张图：片间比例越高，表示这一层有更多入口流量经过晶圆之间。剩下的气体经其他区域通过。CV 衡量同一层各条通道的流量差别，越接近零，表示各条通道分到的流量越接近。</p>')
    cut_rows=[]
    for key,layer in (metrics.get('layers') or {}).items():
        height=layer.get('cut_height_m',layer.get('y_cut_m'))
        geometry=next((g for g in (metrics.get('geometry') or {}).get('layers',[])
                       if g.get('y_cut_m')==height), {})
        cut_rows.append([layer_label.get(key,key),_fmt(height,8),
                         _fmt(layer.get('overlap_split_z_m',geometry.get('overlap_split_z_m')),8)])
    if cut_rows:
        flow.append(_table(['截面','统计高度 Y (m)','重叠区分界 Z (m)'],cut_rows))
    flow.append('<p class="small">各层截面位于串联流路，绝不跨层相加。端部旁路已经包含在外围主分区内，不重复计入。</p>')
    body.append(_section('1. 入口气体去了哪里', ''.join(flow)))
    plot=[];extrema=[];details=[]
    all_flows=[float(ch['net_kg_s']) for layer in layers.values() for ch in (layer.get('channels') or [])
               if isinstance(ch.get('net_kg_s'),(int,float)) and math.isfinite(ch['net_kg_s'])]
    common_scale=(min(all_flows),max(all_flows)) if all_flows else None
    for lname,layer in layers.items():
        channels=layer.get('channels') or []
        plot.append(_position_plot(channels,lname,common_y_range=common_scale))
        byrow={}
        for ch in channels:byrow.setdefault(ch.get('row'),[]).append(ch)
        for row,vals in sorted(byrow.items()):
            for which,ch in [('最低',min(vals,key=lambda c:c['net_kg_s'])),('最高',max(vals,key=lambda c:c['net_kg_s']))]:
                extrema.append([lname,row,which,ch.get('channel_id','未取得'),
                                _fmt(ch.get('x_center_m')),_fmt(ch['net_kg_s'])])
        for ch in channels:
            details.append([lname,ch.get('row'),ch.get('channel_id',''),
                            _fmt(ch.get('x_center_m'),9),_fmt(ch.get('z_center_m'),9),
                            _fmt(ch.get('positive_kg_s'),10),_fmt(ch.get('negative_magnitude_kg_s'),10),
                            _fmt(ch.get('net_kg_s'),10)])
    plot.append(_table(['层','排','极值','通道','X (m)','净流量 (kg/s)'],extrema))
    body.append(_section('2. 晶圆片间哪里流量偏低',
        '<p>横轴表示沿腔体长度的位置，曲线上的每个点代表一条晶圆间隙。点越低，表示这个位置分到的气体越少。下表给出了各排流量最高和最低的位置。</p>'+''.join(plot)))
    if baffle.get('status')=='geometry_candidate_holes_native_flux_computed':
        b_text=_cards([('检测到的孔数',baffle.get('hole_count')),('孔排数',baffle.get('hole_row_count')),
                       ('逐孔流量 CV',_fmt(baffle.get('all_hole_cv_pct'))+'%')])
        b_text+=_hole_svg(baffle,port)
        b_text+=_baffle_row_histogram(baffle)
        b_text+='<p>柱子越高，表示这一排孔合计通过的气体越多。橙色表示高于参考值。整排流量高时，可以结合上面的孔位图查看，是整排普遍偏高，还是少数孔特别高。</p>'
        b_text+=_table(['孔排','孔数','总流量 kg/s','排内 CV'],[
            [r.get('index'),r.get('hole_count'),_fmt(r.get('net_kg_s')),_fmt(r.get('cv_pct'))+'%']
            for r in baffle.get('rows',[])])
        b_text+='<details><summary>逐孔正反向流量与真实坐标（展开）</summary>'+_table(
            ['孔','X (m)','Z (m)','正向 (kg/s)','反向 (kg/s)','净量 (kg/s)'],[
                [j+1,_fmt(h.get('center_m',[None,None,None])[0],8),_fmt(h.get('center_m',[None,None,None])[2],8),
                 _fmt(h.get('positive_kg_s'),9),_fmt(h.get('negative_magnitude_kg_s'),9),_fmt(h.get('net_kg_s'),9)]
                for j,h in enumerate(baffle.get('holes',[]))])+'</details>'
        b_text+=('<p class="small">11.9 严格几何原生孔洞汇总，逐孔坐标在当前结构化参考结果中未提供；未添加模拟孔位。</p>' if provenance.get('report_profile')=='11_9_validated_reference' else '<p class="small">候选挡板孔由原生网格连通分量识别；孔数与板边流量需独立几何核验。</p>')
    else:
        b_text='<p class="notavailable">未取得可确认的挡板孔：'+_e(baffle.get('reason','缺少当前模型的可靠孔位识别'))+'</p>'
    body.append(_section('3. 挡板开孔与出口',b_text))
    if pressure.get('status','').startswith('computed_pressure_proxy'):
        p_text='<p>压降可以理解为气体从入口流到出口损失了多少压力。在入口流量和其他工况相同的情况下，压降越大，维持这股气流所需的压差越大。</p>'
        p_text+=_cards([('入口总压（估算）',_fmt(pressure.get('inlet_total_pa'))+' Pa'),
                        ('出口总压（估算）',_fmt(pressure.get('outlet_total_pa'))+' Pa'),
                        ('从入口到出口的总压降（估算）',_fmt(pressure.get('pressure_drop_pa'))+' Pa')])
        p_text+='<p class="small">这里采用低速气流近似。报告未独立核实实际气体材性，比较不同方案时需统一工况和计算口径。</p>'
        p_text+='<details><summary>展开总压计算假设</summary><p>'+_e(
            pressure.get('mach_screening_note','未记录详细计算假设'))+'</p></details>'
    else:p_text='<p class="notavailable">总压降未取得：'+_e(pressure.get('reason','缺少可靠端口压力、密度或速度字段'))+'</p>'
    plenum=baffle.get('lower_plenum_static_pressure_pa')
    if plenum:
        p_text+=_table(['位置','静压最小 Pa','静压最大 Pa','静压极差 Pa','平均 Pa'],[
            ['底部集气腔',_fmt(plenum.get('min')),_fmt(plenum.get('max')),
             _fmt(plenum.get('range')),_fmt(plenum.get('unweighted_mean'))]])
    else:p_text+='<p class="small">集气腔压力分布：未取得。</p>'
    psegments=pressure.get('plenum_x_segments') or []
    if baffle.get('lower_plenum_static_pressure_pa'):
        p_text+='<h3>集气腔 X–Z 实测静压分箱</h3>'+_plenum_pressure_svg(baffle,port)
    if psegments:
        p_text+='<h3>集气腔沿 X 分段静压</h3>'+_scalar_position_plot(psegments, '集气腔分段静压 Pa')
        p_text+=_table(['X 范围 (m)','单元平均静压 (Pa)'],[[str(_fmt(x['x_lo_m']))+' ~ '+str(_fmt(x['x_hi_m'])),_fmt(x['mean_pa'])] for x in psegments])
    if pressure.get('stations'):
        p_text += '<h3>配置测站 · 单元中心静压</h3>'
        p_text += _table(['测站','Y (m)','采样厚度 (m)','实际单元数','算术均值 (Pa)','最小 (Pa)','最大 (Pa)','状态'],
            [[x.get('id'),_fmt(x.get('y_m')),_fmt(x.get('thickness_m')),
              x.get('cell_count','未取得'),_fmt(x.get('mean_pa')),_fmt(x.get('min_pa')),
              _fmt(x.get('max_pa')),x.get('status','未取得')]
             for x in pressure['stations']])
        p_text += '<p class="small">该测站仅为真实单元中心静压的算术样本；非体积加权、非原生截面总压，不用于自动认定端口总压降。</p>'
    segments=pressure.get('segment_static_drop_pa') or []
    if segments:
        p_text+='<h3>分段静压差</h3>'+_table(['区段','上游减下游 (Pa)'],
            [[s['name'],_fmt(s.get('drop_pa'),7)] for s in segments])
    elif not pressure.get('stations'):
        p_text+='<p class="notavailable">分段静压未取得：'+_e(pressure.get('station_reason','未记录可核验的测站。'))+'</p>'
    body.append(_section('4. 压力与压降',p_text))
    checks=[['原生面数据','分段范围覆盖通过' if provenance.get('native_flux_blocks') else '未取得',
             '不能由文件名证明同一次 Fluent 保存'],
            ['几何识别','候选，需独立验证',''+_e((metrics.get('geometry') or {}).get('classification','自动识别'))],
            ['分区闭合',(metrics.get('checks') or {}).get('cut_partition_closure','未取得'),'净流量/正反流量均须互斥闭合'],
            ['整体质量守恒',_fmt(port.get('mass_imbalance_pct'))+'%','不能据此宣布收敛'],
            ['迭代与残差','未取得','Case/Data 快照不足以证实收敛'],
            ['压力稳定性','未取得','需 Fluent 监控历史或多个时间点结果']]
    residual_rows=[]
    outlet=port.get('outlet_kg_s')
    if isinstance(inlet,(int,float)) and isinstance(outlet,(int,float)):
        residual_rows.append(('整体进出口',abs(inlet-outlet)))
    closure_rows=[]
    for name,layer in layers.items():
        residual=(layer.get('flux_control_volume') or {}).get('cell_residual_below_cut_kg_s')
        if isinstance(residual,(int,float)):
            residual_rows.append((name+'截面以下',abs(residual)))
        closure=layer.get('closure_error_kg_s')
        parts=layer.get('partition_net_kg_s') or {}
        whole=layer.get('whole_cut_net_kg_s')
        if closure is None and isinstance(whole,(float,int)) and len(parts)==4 and all(isinstance(v,(float,int)) for v in parts.values()):
            closure=sum(parts.values())-whole
        closure_rows.append([name,_fmt(closure,7)])
    baffle_cv=baffle.get('flux_control_volume') or {}
    residual=baffle_cv.get('cell_residual_below_cut_kg_s')
    residual_rows.append(('挡板以下',abs(residual) if isinstance(residual,(int,float)) else None))
    closure_rows.append(['挡板',_fmt(baffle.get('board_cut_closure_error_kg_s'),7)])
    body.append(_section('5. 这些数字能说明什么',
        '<p>流量图可以定位气体通过得多或少的位置。守恒表检查气体流入和流出是否对得上，求解收敛仍需核对迭代记录。若要判断成膜是否均匀，还需要晶圆表面的暴露量或沉积数据。</p>'+
        _table(['检查维度','当前结论','验证边界'],checks)+
        '<h3>控制体质量不平衡</h3>'+_table(['范围','绝对差 (kg/s)','占入口'],
            [[name,_fmt(value,7),f'{100*value/inlet:.6g}%' if value is not None and inlet else '未取得']
             for name,value in residual_rows])+
        '<h3>分区通量闭合</h3>'+_table(['截面','分区加总差 (kg/s)'],closure_rows)))
    body.append(_section('6. 与基线方案对照',
        '<div class="note">本次只有一份保存解的统计。未读取与本模型同口径的基线结果，'
        '请在「模型对比」中使用共同截面复算。先核对上表的 Y 高度、排间分界和通道对应关系，再比较片间占比与 CV。'
        '端口压降另按边界范围、工况及压力定义核对。</div>'))
    tasks=[]
    for lname,layer in layers.items():
        channels=layer.get('channels') or []
        if not channels:continue
        low=min(channels,key=lambda c:c['net_kg_s'])
        tasks.append(['当前最弱片间通道 · '+str(lname),
                      str(low.get('channel_id','未取得'))+' / X='+_fmt(low.get('x_center_m'),8)+' m',
                      _fmt(low['net_kg_s'],8)+' kg/s；当前本层 CV '+_fmt(layer.get('cv_pct'))+'%',
                      '先核查局部入口阻力、片间遮挡及外围绕流；原因尚未判定',
                      '如能锁定一个结构变量，再仅改变该变量运行对照',
                      '保持入口流量、温度、压力、网格策略和同一统计截面',
                      '同口径重新计算；比较最弱通道、CV、旁路和压降，守恒及收敛核验不得降低'])
    body.append(_section('7. 下一轮仿真怎么安排',
        '<p class="small">优先进行可消除歧义的检查。单次保存解不能证明某一结构导致弱流。</p>'+ 
        _table(['发现','位置','原生证据','优先核查','唯一改动项','固定条件','对照判据'],tasks)))
    detail='<details><summary>逐通道正反向流量与空间坐标（展开）</summary>'+_table(
        ['层','排','通道','X (m)','Z (m)','正向 kg/s','反向幅值 kg/s','净 kg/s'],details)+'</details>'
    directional=[]
    for lname,layer in layers.items():
        for key,record in (layer.get('partition_directional_kg_s') or {}).items():
            directional.append([lname,key,_fmt(record.get('positive_kg_s')),
                                _fmt(record.get('negative_magnitude_kg_s')),_fmt(record.get('net_kg_s'))])
    detail+='<details><summary>原生主分区正反向统计（展开）</summary>'+_table(
        ['层','主分区','正向 kg/s','反向幅值 kg/s','净 kg/s'],directional)+'</details>'
    subsets=[]
    for lname,layer in layers.items():
        for key,record in (layer.get('bypass_directional_kg_s') or {}).items():
            subsets.append([lname,key,_fmt(record.get('positive_kg_s')),
                            _fmt(record.get('negative_magnitude_kg_s')),_fmt(record.get('net_kg_s'))])
    detail+='<details><summary>中央 X/Z 子旁路正反向流量（展开）</summary>'+_table(
        ['层','子区域','正向 kg/s','反向幅值 kg/s','净 kg/s'],subsets)+'</details>'
    detail+='<details><summary>来源、限制与核验记录（展开）</summary><pre>'+_e(json.dumps({
        'provenance':provenance,'checks':metrics.get('checks'),
        'limitations':metrics.get('limitations'),
        'geometry':metrics.get('geometry'),
        'pressure_status':pressure.get('status'),
        'baffle_status':baffle.get('status')},ensure_ascii=False,indent=2,default=str))+'</pre></details>'
    body.append(_section('8. 来源与完整明细',detail))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>'+_e(model)+' · ALD 仿真诊断</title><style>'+STYLE+'</style></head><body>'
            '<main>'+header+''.join(body)+'<div class="foot">AutoFluent 本地原生统计报告 · 不引用联网资源</div></main></body></html>')




def _scalar_position_plot(segments, title):
    points=[]
    for s in segments:
        if all(isinstance(s.get(k),(int,float)) and math.isfinite(s[k]) for k in ('x_lo_m','x_hi_m','mean_pa')):
            points.append(((s['x_lo_m']+s['x_hi_m'])/2,s['mean_pa']))
    if len(points)<2:return '<p class="notavailable">分段空间坐标不足，无法绘制曲线。</p>'
    points.sort()
    lo,hi=points[0][0],points[-1][0]
    minp,maxp=min(x[1] for x in points),max(x[1] for x in points)
    if abs(hi-lo)<1e-20 or not math.isfinite(hi-lo):return '<p class="notavailable">分段 X 轴范围异常。</p>'
    if maxp==minp:maxp=minp+max(abs(minp)*.01,1e-12)
    def mapxy(x,p):return 65+745*(x-lo)/(hi-lo),240-194*(p-minp)/(maxp-minp)
    poly=' '.join('%.1f,%.1f'%mapxy(x,v) for x,v in points)
    ticks=''.join('<text x="%.2f" y="264" text-anchor="middle">%s</text>'%(65+745*i/4,_e(_fmt(lo+(hi-lo)*i/4))) for i in range(5))
    return ('<svg class="chart" viewBox="0 0 860 285" role="img" aria-label="'+_e(title)+'">'
            '<text x="65" y="20">'+_e(title)+'</text><polyline fill="none" stroke="#087e8b" stroke-width="2" points="'+poly+'"/>'+
            ''.join('<circle cx="%.2f" cy="%.2f" r="3.2" fill="#087e8b"><title>X=%s m，静压=%s Pa</title></circle>'%(*mapxy(x,v),_e(_fmt(x,6)),_e(_fmt(v,6))) for x,v in points)+ticks+'</svg>')


def normalize_reference_metrics(metrics, csv_path):
    """Adapter for the original 11.9 CSV results. Never recomputes or infers CFD.

    The original reference calculation remains byte-identical. This adapter
    only re-expresses measured rows and measured X extents for the common UI.
    """
    import csv
    import re
    if metrics.get('schema')!='autofluent.native_flux_metrics.v1' or metrics.get('profile')!='ald_11_9_native_v1':
        raise ValueError('Only validated native 11.9 reference data can use this adapter')
    with open(csv_path,encoding='utf-8-sig',newline='') as stream:
        raw=list(csv.DictReader(stream))
    label_map={'upper':'上层','lower':'下层'}
    layers={}
    for kind,zh in label_map.items():
        v=metrics['layers'][kind]
        channels=[]
        for row in raw:
            found=re.fullmatch(r'('+zh+r')(低 Z 排|高 Z 排)晶圆通道 (\d+)',row.get('区域',''))
            if found is None:continue
            row_num=1 if found.group(2)=='低 Z 排' else 2
            idx=int(found.group(3))
            xmatch=re.search(r'X\s+([+\-0-9.eE]+)\s+至\s+([+\-0-9.eE]+)\s+m',row.get('说明',''))
            if not xmatch:raise ValueError('11.9 channel lacks measured X extent: '+row['区域'])
            xlo,xhi=map(float,xmatch.groups())
            if not xlo<xhi:raise ValueError('Invalid original channel X range')
            def vfield(key):return float(row[key]) if row.get(key) else None
            ch={'row':row_num,'channel_id':'%s-r%d-c%03d'%(kind,row_num,idx),
                'x_center_m':(xlo+xhi)/2,'x_range_m':[xlo,xhi],
                'z_center_m':None,  # actual row center not encoded in native 11.9 CSV
                'positive_kg_s':vfield('正向 (kg/s)'),
                'negative_magnitude_kg_s':vfield('反向 (kg/s)'),
                'net_kg_s':vfield('净流量或绝对误差 (kg/s)')}
            channels.append(ch)
        if len(channels)!=v['channel_count']:
            raise ValueError('Native channel spatial rows missing: %s expected %d got %d'%(kind,v['channel_count'],len(channels)))
        recorded=sum(x['net_kg_s'] for x in channels)
        if abs(recorded-v['net_kg_s'])>max(1e-12,abs(v['net_kg_s'])*1e-5):
            raise ValueError('Native channel spatial rows do not close: '+kind)
        layers[kind]={**{k:v[k] for k in ('channel_count','net_kg_s','share_inlet_pct','cv_pct','partition_net_kg_s','whole_cut_net_kg_s','closure_error_kg_s')},
                      'channels':channels}
    hole_rows=[]
    plenum_segments=[]
    for row in raw:
        name=row.get('区域','')
        hm=re.fullmatch(r'挡板孔排 (\d+)（(\d+) 孔）',name)
        if hm:
            cvm=re.search(r'孔净流量 CV\s+([+\-0-9.eE]+)%',row.get('说明',''))
            hole_rows.append({'index':int(hm.group(1)),'hole_count':int(hm.group(2)),
                'net_kg_s':float(row['净流量或绝对误差 (kg/s)']),
                'cv_pct':float(cvm.group(1)) if cvm else None})
        xm=re.fullmatch(r'集气腔静压区 (\d+)',name)
        if xm:
            source=re.search(r'X\s+([+\-0-9.eE]+)\s+至\s+([+\-0-9.eE]+)\s+m，单元算术均值\s+([+\-0-9.eE]+)\s+Pa',row.get('说明',''))
            if source:
                vals=list(map(float,source.groups()))
                plenum_segments.append({'x_lo_m':vals[0],'x_hi_m':vals[1],'mean_pa':vals[2]})
    if hole_rows and sum(x['hole_count'] for x in hole_rows)!=metrics['baffle_holes']:
        raise ValueError('Native baffle row counts do not agree with validated metrics')
    pressure={'status':'computed_pressure_proxy_from_validated_native_reference',
              'pressure_drop_pa':metrics['pressure_drop_pa_low_speed_approx'],
              'mach_screening_note':'11.9 原始计算：低速近似 p + ρ|u|²/2；端口按质量流量加权。未获得可独立核验的求解收敛历史。',
              'plenum_x_segments':plenum_segments}
    baffle={'status':'geometry_candidate_holes_native_flux_computed' if hole_rows else 'not_available',
            'hole_count':metrics['baffle_holes'],'hole_row_count':len(hole_rows),
            'all_hole_cv_pct':metrics.get('baffle_hole_cv_pct'),'rows':hole_rows,
            'holes':[]}
    if metrics.get('plenum_gauge_range_pa') is not None:
        baffle['lower_plenum_static_pressure_pa']={'min':metrics.get('plenum_gauge_min_pa'),
                                                   'max':metrics.get('plenum_gauge_max_pa'),
                                                   'range':metrics.get('plenum_gauge_range_pa')}
    return {'schema':'autofluent.adaptive_native.v1',
            'provenance':{'report_profile':'11_9_validated_reference',
                          'case_path':metrics.get('case_file','未取得'),
                          'data_path':metrics.get('data_file','未取得'),
                          'algorithm_sha256':metrics.get('reference_script_sha256','未取得'),
                          'data_origin':'Fluent original reference CSV (not recalculated by renderer)'},
            'geometry':{'total_plates':metrics.get('wafer_count'),
                        'total_channels':metrics.get('channel_count'),
                        'classification':'严格 11.9 专用几何校验通过；其他几何不能直接应用'},
            'port_flux':{'inlet_kg_s':metrics['inlet_net_kg_s'],
                         'outlet_kg_s':metrics['outlet_net_kg_s'],
                         'mass_imbalance_pct':metrics['mass_imbalance_pct']},
            'layers':layers, 'pressure':pressure,'baffle':baffle,
            'checks':{'cut_partition_closure':metrics.get('checks',{}).get('pair_partition_per_layer','未取得'),
                      **metrics.get('checks',{})},
            'limitations':metrics.get('limitations',[]),
            'reference_measurement_note':'所有百分比/通量均来自原生 11.9 参考结果，空间 X 来源于逐通道 CSV 说明；Z 具体坐标和逐孔 X–Z 未提供，不予估算。'}


def render_reference_report(metrics,csv_path,*,historical_demo=False):
    """Unify 11.9 output styling without replacing its validated physics."""
    adapted=normalize_reference_metrics(metrics,csv_path)
    html=render_native_report(adapted,synthetic_demo=False)
    if historical_demo:
        html=html.replace('</header>','<p class="eyebrow">历史 CSV 报告预览；没有重新运行 Fluent H5</p></header>',1)
    return html


def render_inspection_report(result):
    """One visual template for incomplete H5 inspections; never invent physics."""
    if result.get('schema')!='autofluent.offline_case_data.v1':
        raise ValueError('Invalid inspection result schema')
    py=result.get('pyfluent') or {}
    derived=result.get('derived_metrics') or {}
    model=result.get('model_id','未命名模型')
    partial=result.get('partial_native') or {}
    baffle=partial.get('baffle') or {}
    port=partial.get('port_flux') or derived.get('port_flux_preview') or {}
    port_pressure=partial.get('pressure') or {}
    blockers=result.get('first_full_analysis_blocker') or {}
    module_results=result.get('module_results') or {}
    head_status=('部分完成 · 晶圆几何待复核' if partial and (
        (blockers.get('stage') or '').startswith('wafer_') or
        (blockers.get('pipeline_stage') or '').startswith('wafer_') or
        blockers.get('stage') in ('physical_patch_pairing','row_assignment','expected_model_validation',
                                 'connected_patch_extraction','wall_candidate_screening','layer_generation'))
        else ('部分完成 · 物理分析待复核' if partial else '保存解读取诊断 · 物理结果未完成认证'))
    header=('<header><div class="eyebrow">AutoFluent / ALD 流动分析 / 独立离线报告</div>'
            '<h1>仿真结果与结构优化</h1><p>'+_e(head_status)+'</p>'
            '<p>方案：<strong>'+_e(model)+'</strong></p>'
            '<p class="eyebrow">当前报告只记录实际读取证据；不把文件索引、入口流量预览或局部自检当成片间物理结果。</p></header>')
    summary=_cards([('分析状态',head_status),
                    ('入口原生净流量', _fmt(port.get('inlet_kg_s'),7)+' kg/s' if partial else '未取得'),
                    ('片间流量与通道 CV','未取得'),('中央及外围旁路','未取得'),
                    ('独立挡板孔数',str(baffle.get('hole_count')) if baffle.get('status')=='geometry_candidate_holes_native_flux_computed' else '未取得'),
                    ('总压降',_fmt(port_pressure.get('pressure_drop_pa'))+' Pa' if port_pressure.get('status','').startswith('computed_') else '未取得'),
                    ('求解收敛','未验证')])
    sections=[]
    if blockers:
        sections.append(_section('首个实际阻塞', '<div class="warn"><strong>'+_e(blockers.get('reason','未取得'))+'</strong></div>'+
            '<p>阶段：'+_e(blockers.get('stage','未知'))+'；错误类型：'+_e(blockers.get('error_type','未知'))+'</p>'+
            _table(['物理模块','状态','原因或来源'], [[k,v.get('status'),v.get('reason',v.get('source',''))] for k,v in module_results.items()])+
            ('<details><summary>晶圆识别逐阶段诊断</summary><pre>'+_e(json.dumps(blockers.get('diagnostics'),ensure_ascii=False,indent=2,default=str))+'</pre></details>' if blockers.get('diagnostics') else '')))
    port_rows=[]
    for item in port.get('boundary_zones',[]):
        port_rows.append([item.get('zone'),item.get('kind'),_fmt(item.get('net_kg_s'),8),_fmt(item.get('positive_kg_s'),8),_fmt(item.get('negative_magnitude_kg_s'),8)])
    sections.append(_section('1. 入口气体去了哪里',
        '<p class="notavailable">完整互斥截面未完成有效识别，不绘制分区占比。</p>'+
        ('<p>以下是已由原生 Face ID 计算的端口通量，不代表片间占比。未分类外边界绝对通量：'+_fmt(port.get('external_boundary_absolute_unclassified_kg_s'),8)+' kg/s。</p>'+
        _table(['边界区','类型','净流量 kg/s','正向 kg/s','反向幅值 kg/s'],port_rows) if partial else
        '<p class="small">入口/出口候选有符号通量仍需要校核 Face ID、方向与边界区。</p>')))
    sections.append(_section('2. 晶圆片间哪里流量偏低',
        '<p class="notavailable">未取得通过几何核验的逐通道原生流量与真实 X 坐标，不能绘制通道 CV 或极值曲线。</p>'))
    if baffle.get('status')=='geometry_candidate_holes_native_flux_computed':
        hole_rows=[[str(r.get('index')),_fmt(r.get('hole_count'),0),_fmt(r.get('net_kg_s'),9),
                    _fmt(r.get('cv_pct'))+'%'] for r in baffle.get('rows',[])]
        hole_detail=[[i+1,_fmt(h.get('center_m',[None,None,None])[0],8),
                      _fmt(h.get('center_m',[None,None,None])[2],8),
                      _fmt(h.get('net_kg_s'),10),_fmt(h.get('positive_kg_s'),10),
                      _fmt(h.get('negative_magnitude_kg_s'),10)] for i,h in enumerate(baffle.get('holes',[]))]
        btext=('<p>独立于晶圆几何取得挡板 %s 孔，孔间 CV %s%%。板边净流量 %s kg/s，整板截面闭合差 %s kg/s。</p>' %
               (_e(str(baffle['hole_count'])),_fmt(baffle.get('all_hole_cv_pct')),
                _fmt(baffle.get('board_edge_net_kg_s'),9),_fmt(baffle.get('board_cut_closure_error_kg_s'),10)))
        btext+=_table(['孔排','孔数','净流量 kg/s','CV'],hole_rows)
        btext+=_hole_svg(baffle,port)
        btext+=_baffle_row_histogram(baffle)
        btext+='<details><summary>已验证原生逐孔明细（展开）</summary>'+_table(['孔','X (m)','Z (m)','净 kg/s','正向 kg/s','反向 kg/s'],hole_detail)+'</details>'
    else:
        btext='<p class="notavailable">挡板：'+_e(baffle.get('reason','未取得可认证开孔'))+'</p>'
        if baffle.get('whole_cut_net_kg_s') is not None:
            btext+=_table(['已取得的原生截面量','值'],[
                ['完整板截面净流量 (kg/s)',_fmt(baffle.get('whole_cut_net_kg_s'),10)],
                ['待核对积分片数量',baffle.get('candidate_integral_component_count','未取得')],
                ['未归属净流量 (kg/s)',_fmt(baffle.get('unassigned_net_kg_s'),10)],
                ['候选判定状态',baffle.get('status','未取得')]])
        if baffle.get('diagnostics'):
            btext+='<details><summary>挡板选板与孔轮廓诊断</summary><pre>'+_e(json.dumps(baffle['diagnostics'],ensure_ascii=False,indent=2,default=str))+'</pre></details>'
    sections.append(_section('3. 挡板开孔与出口',btext))
    station_rows=[[x.get('id'),_fmt(x.get('y_m')),_fmt(x.get('thickness_m')),
                   x.get('cell_count','未取得'),_fmt(x.get('mean_pa')),
                   x.get('status','未取得'),x.get('reason','')]
                  for x in port_pressure.get('stations',[])]
    ptext=('<p>端口质量流量加权压降（低速近似）：'+_fmt(port_pressure.get('pressure_drop_pa'))+' Pa。需另外核验 Mach 和材料。</p>'
         if port_pressure.get('status','').startswith('computed_') else
        '<p class="notavailable">端口总压降未取得：'+_e(port_pressure.get('reason','压力字段不完整'))+'</p>')
    if station_rows:
        ptext+='<h3>独立静压测站</h3>'+_table(['测站','Y (m)','厚度 (m)','单元数','平均静压 Pa','状态','原因'],station_rows)
    sections.append(_section('4. 压力与压降',ptext))
    zone_records=[]
    for item in port.get('zones',[]):
        v=item.get('signed_sum_kg_s')
        zone_records.append([item.get('zone',{}).get('name','未取得'), _fmt(v,8),
                             item.get('status','未取得')])
    verification=[['读取状态',py.get('status','未取得'),'不等于晶圆通道已识别'],
                  ['H5 网格拓扑',(result.get('cff_topology') or {}).get('status','未取得'),'几何识别仍需通过'],
                  ['单元通量自检',(py.get('native_cell_flux_balance') or {}).get('status','未取得'),'不等于收敛'],
                  ['端口质量守恒',(_fmt(port.get('mass_imbalance_pct'))+'%' if port.get('mass_imbalance_pct') is not None else '未取得'),
                   '已检验其他外边界通量；并非求解收敛证明' if partial else '仅有边界候选预览'],
                  ['残差/压降稳定','未取得','需实际迭代和监控数据']]
    sections.append(_section('5. 数据可信度',
        _table(['核验项','当前状态','限制'],verification)+
        _table(['边界区','候选有符号通量 kg/s','状态'],zone_records)))
    sections.append(_section('6. 与基线方案对照',
        '<p class="notavailable">没有两份通过统一物理截面与工况检查的结果，不计算优化差值。</p>'))
    missing=derived.get('missing_for_full_analysis',[]) or []
    notes=_table(['下一步核验工作'],[[str(m)] for m in missing]) if missing else '<p>优先核对晶圆壁面、真实截面、SV_FLUX 索引及边界方向，再重新分析。</p>'
    sections.append(_section('7. 下一轮仿真怎么安排',notes))
    details='<details><summary>来源和原生读取诊断（展开）</summary><pre>'+_e(json.dumps({
        'case':result.get('case'), 'data':result.get('data'),
        'pyfluent':py,'derived_metrics':derived,
        'verification':result.get('verification'),
        'warnings':result.get('warnings'),
        'module_results':module_results,'partial_native':partial,'first_full_analysis_blocker':blockers,
        'adaptive_native_attempt':result.get('adaptive_native_attempt'),
        'native_reference_attempt':result.get('native_reference_attempt')},
         ensure_ascii=False,indent=2,default=str))+'</pre></details>'
    sections.append(_section('8. 来源与完整明细',details))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>'+_e(model)+' · H5 读取诊断</title><style>'+STYLE+'</style></head><body><main>'+
            header+summary+''.join(sections)+
            '<div class="foot">AutoFluent 保存解只读报告 · 所有样式和数据均内嵌</div></main></body></html>')
