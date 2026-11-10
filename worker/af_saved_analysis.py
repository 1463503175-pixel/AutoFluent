# -*- coding: utf-8 -*-
"""Direct Case/Data saved-solution analysis. Never requires Codex, WB or Fluent.

Uses *actual* native SV_FLUX only on explicitly saved, named measurement
surfaces. Without interior wafer-gap surfaces it refuses to invent their fluxes.
The generic tetrahedral geometry-to-measurement-surface adapter remains separate.
"""
import html
import json
import os
from pathlib import Path
import re
import statistics
import time

MAX_MEASUREMENT_ZONES = 300
MAX_FACES_PER_ZONE = 250000


def identify_measurement_zone(name):
    s = str(name).lower().strip()
    layer = None
    if re.search(r'(?:^|[\s_\-])(up|upper|top)(?:[\s_\-]|$)', s) or '上层' in s or '上排' in s:
        layer = 'upper'
    elif re.search(r'(?:^|[\s_\-])(down|lower|bottom)(?:[\s_\-]|$)', s) or '下层' in s or '下排' in s:
        layer = 'lower'
    if not layer:
        return None  # never infer upper/lower from global coordinates or face IDs
    # A wall simply named wafer-13 is NOT a wafer-gap measurement surface.
    if re.search(r'(?:gap|channel|interwafer|片间|通道)', s) and not re.search(r'wall|壁面|壁', s):
        ids = re.findall(r'(?<!\d)(\d{1,4})(?!\d)', s)
        if ids:
            return {'layer':layer, 'kind':'channel', 'channel_index':int(ids[-1])}
    if 'bypass' in s or '旁路' in s:
        branch = 'central' if re.search(r'central|center|middle|中央|中间', s) else (
            'left' if re.search(r'left|左', s) else 'right' if re.search(r'right|右', s) else 'unclassified')
        return {'layer':layer, 'kind':'bypass', 'branch':branch}
    return None


def extract_named_measurement_flux(mesh, data_file, phase, names_by_id):
    """Read bounded real native face flux on already-named internal cut surfaces.

    The reported direction is the *stored mesh face orientation*. Conversion to
    the physically common upstream-to-downstream direction is unverified.
    """
    named = [(sid, name, identify_measurement_zone(name)) for sid,name in names_by_id]
    named = [(sid,name,meta) for sid,name,meta in named if meta]
    if len(named) > MAX_MEASUREMENT_ZONES:
        return {'status':'too_many_named_measurements','zones':[], 'reason':'命名测量面过多，拒绝大规模读取'}
    import numpy as np
    rows = []
    for sid,name,meta in named:
        row = dict(meta, surface_name=name, surface_id=int(sid))
        try:
            lo,hi = mesh.get_surface_locs(sid)
            n = int(hi)-int(lo)+1
            if n <= 0 or n > MAX_FACES_PER_ZONE:
                row.update(status='invalid_face_count',face_count_estimate=n)
                rows.append(row)
                continue
            a = np.asarray(data_file.get_face_scalar_field_data(phase,'SV_FLUX',[sid])).ravel()
            if len(a)!=n or not np.all(np.isfinite(a)):
                row.update(status='invalid_flux_data',expected_faces=n,actual_faces=int(len(a)))
                rows.append(row)
                continue
            row.update(status='raw_native_flux',face_count=n,
                       signed_net_kg_s=float(a.sum(dtype=np.float64)),
                       positive_signed_kg_s=float(a[a>0].sum(dtype=np.float64)),
                       negative_signed_magnitude_kg_s=float(-a[a<0].sum(dtype=np.float64)))
        except Exception as exc:
            row.update(status='read_failed',detail=str(exc)[:240])
        rows.append(row)
    return {'status':'native_measurement_zones_read' if rows else 'no_named_measurement_surfaces',
            'zones':rows,'orientation':'stored_native_face_sign_unverified',
            'note':'只有已存在的命名截面可读取；不根据晶圆壁面名称或固定坐标推测片间测量面'}


def analyze_native_flux(pyfluent_record):
    """Produce candidate CV only where separately saved named channel cuts exist."""
    measurement = pyfluent_record.get('native_measurements') or {}
    rows = [r for r in measurement.get('zones',[]) if r.get('status')=='raw_native_flux']
    bylayer = {}
    for layer in ('upper','lower'):
        channels = [r for r in rows if r.get('layer')==layer and r.get('kind')=='channel']
        channels.sort(key=lambda r:r['channel_index'])
        ids = [r['channel_index'] for r in channels]
        label = {'status':'missing_named_channels','channel_count':len(ids)}
        if len(ids)>=2 and len(ids)==len(set(ids)):
            net = [float(r['signed_net_kg_s']) for r in channels]
            signs = {1 if x>0 else -1 if x<0 else 0 for x in net}
            if len(signs)==1 and 0 not in signs:
                magnitudes = [abs(x) for x in net]
                mean = statistics.fmean(magnitudes)
                label = {'status':'candidate_native_flux_cv_orientation_unverified',
                         'channel_count':len(net), 'channel_indices':ids,
                         'channel_magnitudes_kg_s':magnitudes,
                         'signed_flux_kg_s':net,
                         'total_channel_magnitude_kg_s':sum(magnitudes),
                         'cv_pct':100*statistics.pstdev(magnitudes)/mean,
                         'direction_sign':list(signs)[0],
                         'warning':'CV 基于已命名测量面的原生有符号通量幅值；尚未核验各截面物理法向及分区闭合'}
            else:
                label['status'] = 'mixed_or_zero_native_direction'
        elif len(ids)!=len(set(ids)):
            label['status'] = 'duplicate_channel_indices'
        bylayer[layer] = label
    bypass = {layer:[r for r in rows if r.get('layer')==layer and r.get('kind')=='bypass']
              for layer in ('upper','lower')}
    ports=pyfluent_record.get('native_boundary_flux') or {}
    zone_ports=[r for r in ports.get('zones',[]) if r.get('status')=='raw_face_flux_extracted']
    inlet=[r for r in zone_ports if 'inlet' in r['zone']['name'].lower()]
    outlet=[r for r in zone_ports if 'outlet' in r['zone']['name'].lower()]
    port_result={'status':'incomplete_port_zones','zones':zone_ports}
    if inlet and outlet:
        mi=sum(abs(r['signed_sum_kg_s']) for r in inlet)
        mo=sum(abs(r['signed_sum_kg_s']) for r in outlet)
        port_result.update(status='signed_magnitude_preview_unverified_normals',
                           inlet_net_magnitude_kg_s=mi, outlet_net_magnitude_kg_s=mo,
                           magnitude_difference_pct=(100*abs(mi-mo)/mi) if mi>0 else None,
                           note='原生边界净通量幅值预览；未核验面朝向、真实入口/出口回流与全域守恒')
        if all('mass_magnitude_weighted_total_pressure_pa' in r for r in inlet+outlet):
            if mi>0 and mo>0:
                pin=sum(abs(r['signed_sum_kg_s'])*r['mass_magnitude_weighted_total_pressure_pa'] for r in inlet)/mi
                pout=sum(abs(r['signed_sum_kg_s'])*r['mass_magnitude_weighted_total_pressure_pa'] for r in outlet)/mo
                port_result['candidate_mass_weighted_total_pressure_drop_pa']=pin-pout
                port_result['pressure_note']='使用边界原生 total-pressure 字段与净质量通量幅值加权；法向及回流尚未核验'
    return {'status':'candidate_only_not_geometry_verified' if rows else 'no_measured_native_flux',
            'layers':bylayer,'bypass_raw':bypass,'port_flux_preview':port_result,
            'reliability':'candidate_only__does_not_verify_closed_physical_cuts_or_geometry_mapping',
            'missing_for_full_analysis':['完整网格面-单元-节点映射及原生 SV_FLUX 法向核验',
                 '按当前实际几何构建互斥的晶圆片间/中央及外围旁路截面',
                 '同一物理截面上正向/反向通量分解及闭合守恒核验',
                 '总压质量加权差及收敛稳定性核验']}


def write_saved_report(result, output_dir):
    """Write separate non-destructive JSON/HTML from this actual H5 inspection."""
    d=Path(output_dir).resolve(); d.mkdir(parents=True,exist_ok=True)
    slug=re.sub(r'[^\w.-]+','_',str(result.get('model_id','model')))[:65]
    base='saved_%s_%s_%s'%(slug,time.strftime('%Y%m%d_%H%M%S'),os.urandom(4).hex())
    json_path=d/(base+'.json'); html_path=d/(base+'.html')
    # Do not recursively include the paths of output artifacts in the archived result.
    with json_path.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    derived=result.get('derived_metrics',{})
    py=result.get('pyfluent',{})
    def esc(s): return html.escape(str(s),quote=True)
    lines=['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
           '<title>AutoFluent H5 保存解分析</title><style>body{font:15px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#20303b}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}small{color:#63717a}h2{margin-top:32px}</style>',
           '<h1>AutoFluent · H5 保存解分析</h1>',
           '<p><strong>模型：</strong>%s</p>'%esc(result.get('model_id','')),
           '<p><strong>读取状态：</strong>%s · <strong>物理分析：</strong>%s</p>'%(esc(py.get('status')),esc(result.get('physical_analysis_status'))),
           '<p><small>来源：%s · %s</small></p>'%(esc(result.get('case',{}).get('path')),esc(result.get('data',{}).get('path'))),
           '<h2>CFF 网格拓扑检查</h2>',
           '<p>网格结构状态：%s（非片间流量计算）</p>'%esc((result.get('cff_topology') or {}).get('status','未取得')),
           '<p>区域拓扑与面连接证据：%s</p>'%esc((result.get('cff_topology') or {}).get('checks',{})),
           '<p><small>拓扑检查不能证明片间积分已完成；原生通量仍需独立验证面方向和测量截面。</small></p>',
           '<h2>原生 Face ID 映射与单元守恒自检</h2>',
           '<p>全域原生面通量：%s</p>'%esc((py.get('global_native_sv_flux') or {}).get('status','未取得')),
           '<p>单元面通量收支：%s</p>'%esc((py.get('native_cell_flux_balance') or {}).get('status','未取得')),
           '<p>相对单元通量失衡 P95：%s</p>'%esc((py.get('native_cell_flux_balance') or {}).get('relative_cell_imbalance_p95','未取得')),
           '<p>边界有符号通量和：%s kg/s（未核验面法向）</p>'%esc((py.get('native_cell_flux_balance') or {}).get('boundary_signed_net_kg_s','未取得')),
           '<p><small>c0 外向为候选约定，不能仅凭误差小认定已验证方向；面通量自检不等于晶圆片间/旁路测量，也不能用于替代收敛判断。</small></p>',
           '<h2>片间原生通量候选值</h2>',
           '<table><tr><th>层</th><th>通道数</th><th>CV</th><th>状态</th></tr>']
    for name in ('upper','lower'):
        r=derived.get('layers',{}).get(name,{})
        cv='未取得' if r.get('cv_pct') is None else ('%.5g%%（待截面核验）'%r['cv_pct'])
        lines.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(name),esc(r.get('channel_count','—')),esc(cv),esc(r.get('status','未取得'))))
    port=derived.get('port_flux_preview') or {}
    lines.append('</table><h2>进出口原生通量</h2>')
    if port.get('magnitude_difference_pct') is not None:
        lines.append('<p>进出口净通量幅值差：%.5g%%（仅预览，未核验法向）</p>'%port['magnitude_difference_pct'])
    else:
        lines.append('<p>未取得可计算的完整进出口原生通量。</p>')
    lines.append('<table><tr><th>区域</th><th>有符号净流量 kg/s</th><th>正符号</th><th>负符号幅值</th></tr>')
    for r in port.get('zones',[]):
        lines.append('<tr><td>%s</td><td>%.8g</td><td>%.8g</td><td>%.8g</td></tr>' % (
            esc(r.get('zone',{}).get('name')),r['signed_sum_kg_s'],r['positive_signed_kg_s'],r['negative_signed_magnitude_kg_s']))
    lines.append('</table><h2>原生面测量记录</h2><table><tr><th>名称</th><th>类型</th><th>有符号净通量 kg/s</th><th>状态</th></tr>')
    for r in (py.get('native_measurements') or {}).get('zones',[]):
        v=r.get('signed_net_kg_s')
        lines.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(r.get('surface_name')),esc(r.get('kind')),esc('未取得' if v is None else '%.8g'%v),esc(r.get('status'))))
    lines.append('</table><h2>未完成的物理验证</h2><ul>')
    for m in derived.get('missing_for_full_analysis',[]):lines.append('<li>%s</li>'%esc(m))
    lines.append('</ul><p><small>只读分析。缺少真实测量面时不输出片间 CV、旁路占比或压降；上、下层为串联截面，严禁相加。</small></p></html>')
    from af_report_summary import inspection_summary, insert_summary
    from af_report_v3 import render_inspection_report
    html_content = render_inspection_report(result)
    with html_path.open('x',encoding='utf-8') as f: f.write(html_content)
    return {'analysis_json':str(json_path),'html_file':str(html_path)}
