"""Read saved surface site fractions and summarize them by actual wall area."""
import sys
from math import ceil
sys.dont_write_bytecode = True
import base64
import html
import re

import numpy as np


def wall_reaction(case, data, mesh, geometry, water=False):
    settings = case['settings/Rampant Variables'][0].decode('utf-8')
    fields = data[f"results/{mesh['result_group']}/{mesh['phase_group']}/faces"]
    site_fields = {int(k.split('_')[-1]): fields[k] for k in fields if re.fullmatch(r'SV_SITE_F_\d+', k)}
    if not site_fields:
        return None
    domain = re.search(r'\(domains\s+.*?\(material\s+\.\s+([^\s()]+)\)', settings, re.S)
    if not domain:
        raise ValueError('无法识别流体混合物')
    material = re.search(r'\(' + re.escape(domain[1]) + r'\s+mixture\b(?:(?!\(\w+\s+mixture\b).)*?\(species\s+\(names\s+\([^()]*\)\s+\([^()]*\)\s+\(([^()]*)\)', settings, re.S)
    if not material:
        raise ValueError('无法识别表面物种顺序')
    species = [x.strip('"').lower() for x in material[1].split()]
    if sorted(site_fields) != list(range(len(species))) or len(set(species)) != len(species):
        raise ValueError('表面物种与覆盖率字段不一致')

    # Site columns are aligned by inclusive, one-based face ranges.
    def blocks(group):
        parts = sorted((int(a.attrs['minId'][0])-1, int(a.attrs['maxId'][0]), a) for a in group.values())
        if not parts or any(a < 0 or b > mesh['nf'] or a >= b or ds.shape != (b-a,)
                            for a,b,ds in parts) or any(a[1] > b[0] for a,b in zip(parts,parts[1:])):
            raise ValueError('壁面字段面编号段无效')
        return parts

    first = blocks(site_fields[0])
    ranges = [(a,b) for a,b,_ in first]
    nfaces = sum(b-a for a,b in ranges)
    budget = mesh.get('memory_budget_mb', 6144)
    if mesh.get('projected_peak_memory_mb_not_measured', 0) + nfaces*(128+8*len(species))/1024**2 > budget:
        raise ValueError('壁面反应分析超出当前内存预算')
    ids = np.concatenate([np.arange(a,b) for a,b in ranges])
    wall = np.sort(mesh['wall_ids'])
    probe = np.searchsorted(wall, ids)
    if not len(wall) or np.any(probe==len(wall)) or not np.array_equal(wall[probe], ids):
        raise ValueError('覆盖率字段包含未核实的壁面')
    values = {}
    invalid = np.zeros(len(ids), dtype=bool)
    for i,name in enumerate(species):
        parts = blocks(site_fields[i])
        if [(a,b) for a,b,_ in parts] != ranges:
            raise ValueError('覆盖率字段面编号范围不一致')
        v = np.concatenate([ds[:] for _,_,ds in parts])
        if not np.isfinite(v).all() or np.any(v < -.001) or np.any(v > 1.001):
            raise ValueError('覆盖率存在严重越界或非有限值')
        invalid |= (v < -2e-5) | (v > 1+2e-5)
        values[name] = np.clip(v, 0, 1)
    closure = np.abs(sum(values.values())-1)
    if np.any(closure > .001):
        raise ValueError('覆盖率总和不闭合，当前仅支持单一位点相')
    invalid |= closure > 2e-5

    vertices = mesh['xyz'][mesh['conn'][ids]]
    centers = vertices.mean(axis=1)
    area = np.linalg.norm(np.cross(vertices[:,1]-vertices[:,0], vertices[:,2]-vertices[:,0]), axis=1)/2
    if not np.isfinite(area).all() or np.any(area <= 0):
        raise ValueError('壁面面积无效')
    excluded = int(invalid.sum())
    excluded_area = float(area[invalid].sum())
    if excluded > max(1, len(ids)*.0001) or excluded_area > area.sum()*.0001:
        raise ValueError('异常覆盖率面超过 0.01% 的数量或面积上限')
    tol=max(2e-8,float(np.linalg.norm(np.ptp(mesh['xyz'],axis=0)))*2e-7)
    planar=np.ptp(vertices[:,:,0],axis=1)<=tol
    x,y,z = centers.T
    groups = [('有覆盖率数据的全部壁面', np.ones(len(ids), dtype=bool))]
    layers = (geometry or {}).get('layers', [])
    if len(layers) >= 2:
        for i,layer in enumerate(sorted(layers,key=lambda l: np.mean([r['y_min_m'] for r in l['rows']]))):
            bounds = [(r['y_min_m'],r['y_max_m'],r['z_min_m'],r['z_max_m']) for r in layer['rows']]
            mask = np.zeros(len(ids), dtype=bool)
            for ylo,yhi,zlo,zhi in bounds:
                mask |= (y>=ylo)&(y<=yhi)&(z>=zlo)&(z<=zhi)
            if not mask.any():
                continue
            label = '下层' if i==0 else ('上层' if i==len(layers)-1 else f'第 {i+1} 层')
            groups.append((label,mask))
            if i==0:
                low,high = y[mask].min(),y[mask].max()
                groups.append(('下层底部 25% 高度范围',mask&(y <= low+.25*(high-low))))
            edge = x[mask].max()-.05*np.ptp(x[mask])
            groups.append((label+' +X 末端 5% 长度范围',mask&(x>=edge)))

    rows=[]
    oh = values.get('oh<s>')
    for label,mask in groups:
        mask = mask & ~invalid
        w=area[mask]
        if not len(w):
            continue
        rows.append({'name':label,'area':float(w.sum()),'faces':len(w),
                     'mean':{k:float(np.average(v[mask],weights=w)) for k,v in values.items()},
                     'oh_le_01':float(w[oh[mask]<=.1].sum()/w.sum()) if oh is not None else None})
    methyl_species = ('tmaads<s>', 'dma<s>', 'mma<s>', 'dmah2o<s>', 'mmaoh<s>', 'mmaoh2o<s>', 'mmah2o<s>')
    supported_methyl = set(values) == set(methyl_species) | {'oh<s>', 'res1<s>', 'res2<s>'}
    methyl = np.clip(sum(values[k] for k in methyl_species), 0, 1) if supported_methyl else None
    water_inlet = water
    water = methyl is not None
    wafers=[]; heat=[]; water_heat=[]
    if oh is not None:
        for layer in sorted(layers,key=lambda l: np.mean([r['y_min_m'] for r in l['rows']])):
            for row_index,row in enumerate(layer['rows'],1):
                lo,hi=np.asarray(row['face_positions_lo_m']),np.asarray(row['face_positions_hi_m'])
                if len(lo)!=len(hi) or len(lo)!=row['plate_count'] or not np.all(lo<hi):
                    continue
                stations=np.column_stack((lo,hi)).ravel()
                if not np.all(np.diff(stations)>2*tol):
                    continue
                in_row=planar & ~invalid & (y>=row['y_min_m']-tol) & (y<=row['y_max_m']+tol) & (z>=row['z_min_m']-tol) & (z<=row['z_max_m']+tol)
                ix=np.flatnonzero(in_row)
                right=np.clip(np.searchsorted(stations,x[ix]),1,len(stations)-1)
                left=right-1
                nearest=np.where(abs(x[ix]-stations[left])<abs(x[ix]-stations[right]),left,right)
                matched=abs(x[ix]-stations[nearest])<=tol
                ix,nearest=ix[matched],nearest[matched]
                footprint=np.pi*(row['y_max_m']-row['y_min_m'])*(row['z_max_m']-row['z_min_m'])/4
                side_area=np.bincount(nearest,weights=area[ix],minlength=len(stations)).reshape(-1,2)
                total=side_area.sum(axis=1)
                good=((side_area>=.9*footprint)&(side_area<=1.1*footprint)).all(axis=1)&(footprint>0)
                passed=np.bincount(nearest//2,weights=area[ix]*(oh[ix]<=.1),minlength=len(lo))
                consumed=np.bincount(nearest//2,weights=area[ix]*(1-oh[ix]),minlength=len(lo))
                mma=values.get('mma<s>')
                mma_area=np.bincount(nearest//2,weights=area[ix]*mma[ix],minlength=len(lo)) if mma is not None else None
                methyl_mean = methyl_high = methyl_peak = None
                if methyl is not None:
                    methyl_mean = np.bincount(nearest, weights=area[ix]*methyl[ix], minlength=len(stations)).reshape(-1,2)
                    methyl_high = np.bincount(nearest, weights=area[ix]*(methyl[ix]>.05), minlength=len(stations)).reshape(-1,2)
                    methyl_peak = np.zeros(len(stations))
                    np.maximum.at(methyl_peak, nearest, methyl[ix])
                    methyl_peak = methyl_peak.reshape(-1,2)
                order=np.argsort(nearest,kind='stable')
                bounds=np.searchsorted(nearest[order],np.arange(len(stations)+1))
                for j in range(len(lo)):
                    sides=[]; water_sides=[]
                    for s in (2*j,2*j+1):
                        f=ix[order[bounds[s]:bounds[s+1]]]
                        if not good[j] or not len(f):
                            sides.append(None)
                            if water: water_sides.append(None)
                            continue
                        yy=(vertices[f,:,1]-row['y_min_m'])/(row['y_max_m']-row['y_min_m'])
                        zz=(vertices[f,:,2]-row['z_min_m'])/(row['z_max_m']-row['z_min_m'])
                        q=np.stack((zz,1-yy),axis=2).reshape(-1,6)
                        consume=1-oh[f]
                        color=np.where(oh[f]<=.1,np.maximum(230,np.rint(255*consume)),np.minimum(229,np.rint(255*consume)))
                        packed=np.column_stack((np.rint(np.clip(q,0,1)*255),color)).astype('uint8')
                        w=area[f]
                        sides.append([float(w[oh[f]>.1].sum()/w.sum()),float(np.average(consume,weights=w)),base64.b64encode(packed.tobytes()).decode('ascii')])
                        if water:
                            residue=methyl[f]
                            packed[:,6]=np.where(residue>.05,np.maximum(13,np.rint(255*residue)),np.minimum(12,np.rint(255*residue)))
                            water_sides.append([float(w[residue>.05].sum()/w.sum()),float(np.average(residue,weights=w)),base64.b64encode(packed.tobytes()).decode('ascii')])
                    heat.append([layer['id'],row_index,j+1,sides])
                    if water: water_heat.append([layer['id'],row_index,j+1,water_sides])
                wafers.append({'layer':layer['id'],'row':row_index,'plates':[
                    {'x_mm':float((a+b)*500),'pass_area':float(passed[j]/total[j]) if good[j] else None,
                     'oh_consumed':float(consumed[j]/total[j]) if good[j] else None,
                     'mma_coverage':float(mma_area[j]/total[j]) if good[j] and mma_area is not None else None,
                     'methyl_mean':float(methyl_mean[j].sum()/total[j]) if good[j] and methyl_mean is not None else None,
                     'methyl_above_5_area':float(methyl_high[j].sum()/total[j]) if good[j] and methyl_high is not None else None,
                     'methyl_sides':[
                         {'mean':float(methyl_mean[j,k]/side_area[j,k]),
                          'above_5_area':float(methyl_high[j,k]/side_area[j,k]),
                          'peak':float(methyl_peak[j,k])} for k in range(2)
                     ] if good[j] and methyl_mean is not None else None}
                    for j,(a,b) in enumerate(zip(lo,hi))]})
    raw=data['settings/Data Variables'][0].decode('utf-8')
    t=re.search(r'\(flow-time\s+([^()\s]+)',raw)
    return {'status':'computed','water_inlet':water_inlet,'water_heat':water_heat,'species':species,'rows':rows,'wafers':wafers,'heat':heat,'excluded_faces':excluded,'excluded_area_m2':excluded_area,'time':float(t[1]) if t else None}


def render_wall_reaction(result):
    tma=_render_wall_metric(result)
    if not result or result.get('status') != 'computed':
        return tma
    if result.get('water_heat'):
        water=_render_wall_metric(dict(result,water_surface=True,heat=result['water_heat']))
        start=water.index('</div>')+len('</div>')
        body=water[start:-len('</section>')].replace('wall-reaction','wall-reaction-water').replace('wafer-surfaces','wafer-surfaces-water')
        state = ('本帧入口识别为供水。' if result.get('water_inlet') else
                 '本帧未确认处于供水阶段，图中显示保存时刻的甲基残留，仅供阶段对照。')
        body = f'<p>{state}</p>'+body
    else:
        body = '<p>未取得可绘制的含甲基位点双面数据，请核对表面机理和晶圆网格匹配。</p>'
    return tma[:-len('</section>')]+'<section id="wall-reaction-water"><h3>水阶段 · 含甲基位点残留</h3>'+body+'</section></section>'


def _render_wall_metric(result):
    if not result:
        return ''
    esc=html.escape
    water=result.get('water_surface',False)
    head='<section id="wall-reaction"><div class="chapter-heading"><span class="serial"></span><h2>壁面反应与表面覆盖率</h2></div>'
    if result.get('excluded_faces'):
        head+=f"<p role='status'>已排除 {result['excluded_faces']} 个覆盖率异常网格面（{result['excluded_area_m2']:.6g} m²）。统计和热图使用有效网格面。</p>"
    if result['status']!='computed':
        return head+'<p>'+esc(result['reason'])+'</p></section>'
    if water and not result.get('heat'):
        return head+'<p>未取得已匹配的水阶段晶圆双面网格。</p></section>'
    from af_species_report import table
    species=result['species']
    flags='oh<s>' in species
    cols=['区域','反应壁面面积（m²）']+species+(['净 OH 消耗（以初始 100% 为基准）','OH 覆盖率 ≤ 10% 的面积占比'] if flags else [])
    def percent(v):
        return f'{v*100:.3f}%'
    rows=[[r['name'],f"{r['area']:.5f}",*(percent(r['mean'][s]) for s in species),
           *([percent(1-r['mean']['oh<s>']),percent(r['oh_le_01'])] if flags else [])] for r in result['rows']]
    t=result['time']
    msg=f'流动时间 {t:g} s。' if t is not None else ''
    msg+='覆盖率按原网格面面积加权，区域用面心归属。幅度不超过 0.002 个百分点的数值越界按 0–1 限幅。'
    if flags:
        msg+='初始 OH 覆盖率为 100% 时，1 减当前 OH 覆盖率可用作 TMA 阶段净 OH 消耗。OH ≤ 10% 是可对照的筛选线。'
    styles='<style>#wall-reaction .species-table-wrap{overflow-x:auto}#wall-reaction table{width:100%;min-width:580px;border-collapse:collapse;font-variant-numeric:tabular-nums}#wall-reaction th,#wall-reaction td{padding:10px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid #e1e9ef}#wall-reaction th:first-child,#wall-reaction td:first-child{text-align:left}#wall-reaction th{background:#eef5f9;color:#233e50}#wall-reaction summary{cursor:pointer;margin:16px 0 8px}</style>'
    details='<details><summary>查看全部表面物种覆盖率及壁面面积</summary>'+table(cols,rows)+'</details>'
    if not flags:
        return head+styles+'<p>'+esc(msg)+'</p>'+table(cols,rows)+'</section>'
    show_mma='mma<s>' in species
    key_cols=['区域','平均 OH 消耗率','OH 消耗达到 90% 的面积占比']
    if show_mma:
        key_cols.append('平均 MMA 覆盖率')
    key_rows=[]
    for r in result['rows']:
        row=[r['name'],percent(1-r['mean']['oh<s>']),percent(r['oh_le_01'])]
        if show_mma:
            row.append(percent(r['mean']['mma<s>']))
        key_rows.append(row)
    regions=result['rows']
    weakest=max(regions,key=lambda r: 1-r['oh_le_01'])
    scale=max(1,ceil((1-weakest['oh_le_01'])*100))
    cards=[
        ('有数据壁面 · 平均 OH 消耗',key_rows[0][1],'初始 OH 覆盖率按 100% 计算'),
        ('有数据壁面 · 达标面积',key_rows[0][2],'局部净 OH 消耗 ≥ 90%'),
        ('薄弱区域 · 达标面积',percent(weakest['oh_le_01']),weakest['name']),
        ('薄弱区域 · 未达标面积',percent(1-weakest['oh_le_01']),weakest['name']),
    ]
    cards_html=''.join(f'<article class="kpi"><label>{esc(label)}</label><strong>{value}</strong><small>{esc(note)}</small></article>' for label,value,note in cards)
    chart=''
    for r in regions:
        remaining=1-r['oh_le_01']
        highlight=' class="wall-weak"' if r is weakest else ''
        chart+=f'<li{highlight}><span>{esc(r["name"])}</span><span class="wall-track" aria-hidden="true"><i style="width:{remaining*10000/scale:.5f}%"></i></span><strong>{percent(remaining)}</strong></li>'
    styles+='<style>#wall-reaction .wall-chart{margin:16px 0;background:#fff;border:1px solid var(--border);border-radius:13px;padding:16px}#wall-reaction .wall-chart h3{margin:0}#wall-reaction .wall-chart p{margin:5px 0 12px}#wall-reaction .wall-chart ul{list-style:none;padding:0;margin:0}#wall-reaction .wall-chart li{display:grid;grid-template-columns:minmax(8em,1fr) minmax(4em,2fr) auto;align-items:center;gap:10px;padding:5px 0}#wall-reaction .wall-track{background:#edf3f6;border-radius:5px;height:10px;overflow:hidden}#wall-reaction .wall-track i{display:block;background:#208579;height:100%;border-radius:inherit}#wall-reaction .wall-weak .wall-track i{background:#bf6826}#wall-reaction .wall-chart strong{font-variant-numeric:tabular-nums;text-align:right;min-width:4.5em}</style>'
    visual=f'<div class="kpis">{cards_html}</div><div class="wall-chart"><h3>哪些区域还有较多壁面未达到筛选线？</h3><p>局部净 OH 消耗低于 90% 的面积占各区域壁面面积的比例。条长刻度为 0–{scale}%。</p><ul>{chart}</ul></div>'
    wafer_rows=result.get('wafers',[])
    if wafer_rows:
        mapping=''
        layer_ids=list(dict.fromkeys(r['layer'] for r in wafer_rows))
        for index in reversed(range(len(layer_ids))):
            layer=layer_ids[index]
            title=('下层','上层')[index] if len(layer_ids)==2 else f'第 {index+1} 层'
            mapping+=f'<div class="wafer-layer"><h4>{esc(title)}</h4>'
            for r in (r for r in wafer_rows if r['layer']==layer):
                plates=''
                for i,p in enumerate(r['plates'],1):
                    ratio=(1-p['methyl_above_5_area'] if p.get('methyl_above_5_area') is not None else None) if water else p['pass_area']
                    label=f"{title} · 第 {r['row']} 排 · 第 {i} 片 · X = {p['x_mm']:.1f} mm"
                    if ratio is None:
                        tint='unknown'
                        label+=' · 双面数据未核实'
                    else:
                        tint='high' if ratio>=.999 else 'mid' if ratio>=.98 else 'low' if ratio>=.95 else 'weak'
                        label+=f" · 局部甲基残留 ≤5% 的面积 {percent(ratio)} · 平均甲基残留 {percent(p['methyl_mean'])}" if water else f" · 达标面积 {percent(ratio)} · 平均 OH 消耗 {percent(p['oh_consumed'])}"
                    label=esc(label,quote=True)
                    plates+=f'<button type="button" class="{tint}" title="{label}" aria-label="{label}" aria-pressed="false"></button>'
                mapping+=f"<div class='wafer-row'><span>第 {r['row']} 排</span><div class='wafer-plates' style='--count:{len(r['plates'])}'>{plates}</div></div>"
            mapping+='</div>'
        styles+='<style>#wall-reaction .wafer-map{margin:16px 0;overflow:auto}#wall-reaction .wafer-layer{min-width:610px}#wall-reaction .wafer-layer h4{margin:12px 0 6px}#wall-reaction .wafer-row{display:grid;grid-template-columns:4.5em 1fr;gap:8px;margin:4px 0;align-items:center}#wall-reaction .wafer-plates{display:grid;grid-template-columns:repeat(var(--count),minmax(0,1fr));gap:2px}#wall-reaction .wafer-plates button{padding:0;border:0;border-radius:5px/12px;height:37px;cursor:pointer}#wall-reaction .wafer-plates button:focus-visible{outline:2px solid #162b36;outline-offset:1px}#wall-reaction .high{background:#25877b}#wall-reaction .mid{background:#a6cabb}#wall-reaction .low{background:#edbf78}#wall-reaction .weak{background:#ae542a}#wall-reaction .unknown{background:#cbd1d6}#wall-reaction .wafer-legend span{display:inline-block;padding:3px 7px;margin:2px;border-radius:4px;color:#172c37}#wall-reaction .wafer-legend .high,#wall-reaction .wafer-legend .weak{color:#fff}#wall-reaction output{display:block;margin-top:10px;font-weight:600}</style>'
        legend='<div class="wafer-legend" role="group" aria-label="按达标面积筛选">'+''.join(f'<button type="button" data-filter="{kind}" aria-pressed="{str(not kind).lower()}">{label}</button>' for kind,label in [('', '全部'),('high','≥99.9%'),('mid','98–&lt;99.9%'),('low','95–&lt;98%'),('weak','&lt;95%'),('unknown','未取得')])+'</div>'
        count=sum(len(r["plates"]) for r in wafer_rows)
        handler=esc("const b=event.target.closest('button');if(!b)return;const detail=this.querySelector('.wafer-detail');const close=this.querySelector('[data-wafer-close]');const old=this.querySelector('.wafer-plates [aria-pressed=\"true\"]');if(b.dataset.filter!==undefined){this.querySelector('.wafer-legend [aria-pressed=\"true\"]').setAttribute('aria-pressed','false');b.setAttribute('aria-pressed','true');let n=0;for(const plate of this.querySelectorAll('.wafer-plates button')){plate.disabled=b.dataset.filter!==''&&!plate.classList.contains(b.dataset.filter);if(!plate.disabled)n++;}this.querySelector('.wafer-count').textContent='显示 '+n+' 片晶圆';if(!old||!old.disabled)return;}if(old)old.setAttribute('aria-pressed','false');if(b.matches('.wafer-plates button')){b.setAttribute('aria-pressed','true');detail.textContent=b.title;close.hidden=false;}else{detail.textContent='点击晶圆查看编号、位置与反应数值。';close.hidden=true;if(b===close&&old)old.focus();}",quote=True)
        styles+='<style>#wall-reaction .wafer-legend{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px}#wall-reaction .wafer-legend button,#wall-reaction [data-wafer-close]{font:inherit;border:1px solid #d6e2e7;background:#fff;color:#355564;border-radius:8px;padding:6px 10px;cursor:pointer}#wall-reaction .wafer-legend button[aria-pressed=true]{background:#e3f2ef;border-color:#83bcb2;color:#086c61;font-weight:700}#wall-reaction .wafer-plates button:disabled{opacity:.16;cursor:default}#wall-reaction .wafer-plates button[aria-pressed=true]{outline:2px solid #152a35;outline-offset:1px;z-index:1}#wall-reaction .wafer-detail-box{display:flex;align-items:center;justify-content:space-between;gap:10px;background:#eef7f5;border:1px solid #b7d8d2;border-radius:9px;padding:10px;margin-top:12px}#wall-reaction .wafer-detail-box output{margin:0}</style>'
        chart_title='水阶段逐片晶圆残留分布' if water else 'TMA 指标 · 逐片晶圆 OH 消耗分布'
        measure='局部含甲基位点残留不超过 5%' if water else '局部净 OH 消耗达到 90%'
        visual=f'<figure class="wall-chart wafer-map" onclick="{handler}"><h3>{chart_title}</h3><p>从左到右为 X 负向到正向。颜色表示每片晶圆两侧主表面中，{measure}的面积占比。</p>{legend}<small class="wafer-count" aria-live="polite">显示 {count} 片晶圆</small>{mapping}<div class="wafer-detail-box"><output class="wafer-detail" aria-live="polite">点击晶圆查看编号、位置与反应数值。</output><button type="button" data-wafer-close hidden>关闭 ×</button></div></figure>'+visual
    if result.get('heat'):
        heat_panel=render_wafer_heat(result)
        if heat_panel:
            return head+styles+visual[:visual.index('</figure>')+9]+heat_panel+('<p>按原网格面面积加权。5% 为查看用筛选线。</p>' if water else '<p>初始 OH 覆盖率按 100% 计算净消耗，面积按原网格面加权。</p>')+'</section>'
    precise='<details><summary>查看各区域的精确数值</summary>'+table(key_cols,key_rows)+'</details>'
    return head+styles+visual+precise+details+'<p>'+esc(msg)+'</p></section>'


def render_ald_stages(metrics):
    import json
    from pathlib import Path
    exposure=metrics.get('species_exposure') or {}
    inlet=exposure.get('inlet_mass_fractions') or {}
    if not exposure:
        return ''
    snapshot={'provenance':metrics.get('provenance'),'wall_reaction':{k:v for k,v in (metrics.get('wall_reaction') or {}).items() if k not in ('heat','water_heat')}}
    snapshot['inlet_flow_kg_s']=(metrics.get('port_flux') or {}).get('inlet_kg_s')
    snapshot['geometry']={'layers':[{'id':r['id']} for r in (metrics.get('geometry') or {}).get('layers',[])]}
    snapshot['species_exposure']={k:exposure.get(k) for k in ('channels','inlet_mass_fractions','flow_time_s','gas_inventory_kg','gas_inventory_stats')}
    raw=json.dumps(snapshot,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<',chr(92)+'u003c')
    script=Path(__file__).with_name('af_ald_stages.js').read_text(encoding='utf-8')
    tabs=''.join(f'<button type="button" data-ald-stage="{i}" aria-pressed="false">{name}</button>' for i,name in enumerate(('TMA 脉冲','第一次 N₂ 吹扫','H₂O 脉冲','第二次 N₂ 吹扫')))
    return ('''<section id="ald-stages" class="section"><div class="chapter-heading"><span class="serial"></span><h2>ALD 阶段对照</h2></div>
<style>#ald-stages .ald-panel{background:#fff;border:1px solid var(--border,#d4e1e7);border-radius:13px;padding:16px;overflow:auto}#ald-stages .ald-controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:10px}#ald-stages .ald-controls button,#ald-stages select{border:1px solid #cadcde;background:#fff;color:#244551;border-radius:7px;padding:6px;font:inherit}#ald-stages [aria-pressed=true]{outline:2px solid #174d49;outline-offset:1px}#ald-stages [data-ald-stage][aria-pressed=true]{background:#e2f1ee}#ald-stages .ald-row{display:grid;grid-template-columns:7em 1fr;gap:6px;align-items:center;min-width:620px;margin:5px 0}#ald-stages .ald-strip{display:grid;gap:2px}#ald-stages .ald-strip button{height:28px;border:0;padding:0;border-radius:4px;background:#d9e0e4;cursor:pointer}#ald-stages [data-stage-detail]:not(:empty){background:#edf6f3;border-radius:7px;padding:10px}</style><div class="ald-panel"><div class="ald-controls">'''+tabs+
    '''</div><div class="ald-controls"><label>保存时刻 <select data-time></select></label><label>残留组分 <select data-gas></select></label><label>载入本阶段 AF 报告 <input type="file" data-files multiple accept=".html,.json"></label><button type="button" data-current hidden>将当前纯 N₂ 帧归入此阶段</button></div><button type='button' data-export-inventory disabled>导出本阶段残留 CSV</button><p data-load-status role="status"></p><p data-unassigned hidden></p><p data-stage-status></p><div data-stage-grid></div><p data-stage-detail aria-live="polite"></p></div><script type="application/json">'''+raw+'</script><script>'+script+'</script></section>')


def render_wafer_heat(result):
    import json
    from pathlib import Path
    layers=list(dict.fromkeys(r['layer'] for r in result['wafers']))
    if len(layers)!=2:
        return ''
    def tag(r,i):
        layer='下层' if r['layer']==layers[0] else '上层'
        return f"{layer} · 第 {r['row']} 排 · 第 {i} 片 ·"
    water=result.get('water_surface',False)
    ratios=[(1-p['methyl_above_5_area'] if p.get('methyl_above_5_area') is not None else None,tag(r,i)) if water else (p['pass_area'],tag(r,i)) for r in result['wafers'] for i,p in enumerate(r['plates'],1)]
    weak=sorted((ratio,tag) for ratio,tag in ratios if ratio is not None and ratio<.95)
    metric='含甲基位点残留' if water else 'OH 消耗'
    ranking=''.join(f'<button type="button" data-rank="{html.escape(tag,quote=True)}">{html.escape(tag)} 未达标 {(1-ratio)*100:.3f}%</button>' for ratio,tag in weak)
    faces=''.join(f'<article><h4>{name}</h4><canvas width="420" height="420" aria-label="{name}局部{metric}分布"></canvas><p data-side="{i}"></p></article>' for i,name in enumerate(('−X 面','+X 面')))
    legend="<div aria-label=\"两面共用的局部 OH 消耗率色阶\" style=\"width:min(100%,300px);margin:12px 0 8px auto;font-size:.85em\"><strong>局部 OH 消耗率</strong><div aria-hidden=\"true\" style=\"height:10px;border-radius:3px;background:linear-gradient(to right,hsl(26 80% 86%) 0%,hsl(26 80% 42.49%) 90%,hsl(168 45% 80%) 90%,hsl(168 45% 46.75%) 100%)\"></div><div style=\"position:relative;min-height:1.6em\"><span>0%</span><span style=\"position:absolute;left:50%;transform:translateX(-50%)\">50%</span><span style=\"position:absolute;left:90%;transform:translateX(-100%);white-space:nowrap;padding-right:4px;border-right:1px solid #314f5b\">90% 筛选线</span><span style=\"position:absolute;right:0;top:1.35em;white-space:nowrap\">100%</span></div></div>"
    if water:
        legend='<div style="width:min(100%,300px);margin:12px 0 8px auto"><strong>局部含甲基位点残留</strong><div style="height:10px;border-radius:3px;background:linear-gradient(90deg,hsl(168 45% 46.75%) 0%,hsl(168 45% 80%) 5%,hsl(26 80% 42.49%) 5%,hsl(26 80% 86%) 100%)"></div><div style="display:flex;justify-content:space-between"><span>0%　5% 筛选线</span><span>100%</span></div></div>'
    script=Path(__file__).with_name('af_wafer_heat.js').read_text(encoding='utf-8')
    return ('<style>#wafer-surfaces .face-pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:12px}#wafer-surfaces article{padding:10px;border:1px solid #d8e5e8;border-radius:10px;text-align:center}#wafer-surfaces canvas{display:block;margin:auto;width:min(100%,325px);height:auto}#wafer-surfaces [data-rank]{font:inherit;cursor:pointer;border:1px solid #cbdde0;border-radius:6px;background:#fff;color:#204752;padding:4px 7px;margin:3px}</style>'
            f'<div id="wafer-surfaces" class="wall-chart" data-water="{int(water)}" data-layers="{json.dumps(layers)}"><h3>晶圆双面{metric}分布</h3><p data-picked aria-live="polite"></p><p>两面均以 +Y 朝上、+Z 朝右。</p><div class="face-pair">{faces}</div>{legend}'
            + (f'<details><summary>未达标面积超过 5% 的晶圆 · {len(weak)} 片</summary>{ranking}</details>' if weak else '')
            + '<script type="application/json">'+json.dumps(result['heat'],separators=(',',':'))+'</script></div><script>'+script+'</script>')
