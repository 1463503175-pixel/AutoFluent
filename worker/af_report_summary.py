# -*- coding: utf-8 -*-
"""Human-readable overview derived strictly from the native H5 result.

No field is invented. A successful HDF5 read must not be presented as a
successful physical measurement. Two serial wafer-cut fractions are not summed.
"""
import html
import math
import re
from pathlib import Path


def _e(x):
    return html.escape(str(x), quote=True)


def _num(value, places=4, suffix=''):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '未取得'
    return f'{value:,.{places}f}{suffix}'


def _flux(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '未取得'
    return f'{value:.7e}'


def _td(s):
    return '<td>' + _e(s) + '</td>'


def _tr(values):
    return '<tr>' + ''.join(_td(v) for v in values) + '</tr>'


def _percent_of_inlet(value, inlet):
    if (not isinstance(inlet, (int, float)) or inlet <= 0 or
            not isinstance(value, (int, float)) or not math.isfinite(value)):
        return '未取得'
    return _num(100 * value / inlet, 3, '%')


SUMMARY_STYLE = '''<style id="af-overview-css">
.af-overview{font-family:system-ui,"Microsoft YaHei",sans-serif;line-height:1.55;color:#273547;max-width:1100px}
.af-overview *{box-sizing:border-box}
.af-overview .af-muted{color:#626e7d;font-size:.87rem}
.af-overview .af-lead{margin:10px 0 20px;color:#425569}
.af-overview .af-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:10px;margin:14px 0 22px}
.af-overview .af-kpi{border:1px solid #dce3ea;border-radius:9px;padding:13px;background:#f8fafc;min-width:0}
.af-overview .af-kpi-label{color:#526479;font-size:.88rem;display:block}
.af-overview .af-kpi-value{font-size:1.33rem;font-weight:700;display:block;overflow-wrap:anywhere;margin-top:2px}
.af-overview table{width:100%;border-collapse:collapse;font-size:.92rem}
.af-overview th{background:#f2f5f8;text-align:left}
.af-overview th,.af-overview td{padding:10px;border-bottom:1px solid #dde4eb;white-space:normal}
.af-overview .af-scroll{overflow-x:auto}
.af-overview .af-facts{padding-left:1.35rem}
.af-overview .af-facts li{margin:5px 0}
.af-overview .af-flag{font-weight:650}
.af-overview h2{margin:22px 0 10px}
.af-overview .af-context{padding:10px 13px;background:#f6f8fa;border-left:3px solid #708497;margin:17px 0}
@media print{.af-overview .af-kpis{grid-template-columns:repeat(4,1fr)}.af-overview .af-kpi{break-inside:avoid}}
</style>'''


def _header(status, provenance):
    return ('<section class="af-overview" aria-label="关键指标汇总">'
            '<h2>一页看懂 · 仿真关键结果</h2>'
            '<p class="af-lead"><strong>分析等级：</strong>' + _e(status) +
            '<br><span class="af-muted">' + _e(provenance) + '</span></p>')


def _kpi(label, value, unit=''):
    return '<div class="af-kpi"><span class="af-kpi-label">' + _e(label) + '</span><span class="af-kpi-value">' + _e(value) + '</span><span class="af-muted">' + _e(unit) + '</span></div>'


def _rows_table(headers, rows):
    return ('<div class="af-scroll"><table><thead><tr>' + ''.join('<th>' + _e(h) + '</th>' for h in headers) +
            '</tr></thead><tbody>' + ''.join(_tr(r) for r in rows) + '</tbody></table></div>')


def _layer_summary(layers, inlet):
    ordered = [(k, layers.get(k)) for k in ('upper', 'lower', 'single') if isinstance(layers.get(k), dict)]
    rows = []
    for label, data in ordered:
        part = data.get('partition_net_kg_s') or {}
        rows.append([{'upper':'上层', 'lower':'下层', 'single':'单层'}[label],
                     data.get('channel_count', '未取得'), _flux(data.get('net_kg_s')),
                     _num(data.get('share_inlet_pct'), 3, '%'), _num(data.get('cv_pct'), 4, '%'),
                     _percent_of_inlet(part.get('central'), inlet),
                     _percent_of_inlet(part.get('outer_low_z'), inlet),
                     _percent_of_inlet(part.get('outer_high_z'), inlet)])
    return _rows_table(['截面', '通道数', '片间净流量 kg/s', '占入口', '通道 CV',
                        '中央旁路/入口', '低 Z 外围/入口', '高 Z 外围/入口'], rows)




def _channel_curve(layers):
    """Simple self-contained SVG; original channel numbers remain in CSV/JSON."""
    sections=[]
    for layer_name in ('upper','lower'):
        layer=layers.get(layer_name) or {}
        values=layer.get('channels_net_kg_s') or []
        if len(values)<2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in values):
            continue
        lo=min(values); hi=max(values); span=max(hi-lo,1e-30)
        points=' '.join('%.1f,%.1f'%(18+744*i/(len(values)-1),145-113*(v-lo)/span)
                        for i,v in enumerate(values))
        sections.append('<h3>'+('上层' if layer_name=='upper' else '下层')+'逐通道质量流量（净值）</h3>'
                        '<svg viewBox="0 0 780 165" role="img" aria-label="逐通道净质量流量" '
                        'style="width:100%;height:auto;border:1px solid #e0e5eb;border-radius:7px">'
                        '<line x1="18" y1="145" x2="764" y2="145" stroke="#a8b4c0"/>'
                        '<polyline points="'+points+'" stroke="#246b78" stroke-width="1.5" fill="none"/>'
                        '</svg><p class="af-muted">共 '+str(len(values))+' 条；最小 '+_flux(lo)+
                        ' kg/s，最大 '+_flux(hi)+' kg/s。曲线仅表示此层通道顺序，不代表空间坐标。</p>')
    return '<h2>逐通道分布</h2>'+''.join(sections) if sections else ''


def _observations(layers, imbalance):
    points = []
    upper, lower = layers.get('upper'), layers.get('lower')
    if isinstance(upper, dict) and isinstance(lower, dict):
        uc, lc = upper.get('cv_pct'), lower.get('cv_pct')
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (uc, lc)):
            points.append('上下层通道 CV 相差 ' + _num(abs(uc-lc), 4, ' 个百分点') +
                          '；' + ('上层较高' if uc>lc else '下层较高' if lc>uc else '两层相同') + '。')
        us, ls = upper.get('share_inlet_pct'), lower.get('share_inlet_pct')
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (us, ls)):
            points.append('上下层片间占入口比例相差 ' + _num(abs(us-ls), 3, ' 个百分点') +
                          '。两个截面在串联位置，比例不得相加。')
    if isinstance(imbalance, (int,float)) and math.isfinite(imbalance):
        points.append('该快照的入口/出口净流量相对差为 ' + _num(imbalance, 6, '%') +
                      '；它不是求解收敛已完成的证明。')
    return '<h2>结果解读</h2><ul class="af-facts">' + ''.join('<li>'+_e(x)+'</li>' for x in points) + '</ul>'


def reference_summary(metrics):
    """11.9 hard-calibrated script result, obtained from real H5 or an explicitly labeled historic CSV."""
    layers = metrics['layers']; inlet=metrics['inlet_net_kg_s']; outlet=metrics['outlet_net_kg_s']
    status='11.9 专用几何规则通过；求解收敛尚未独立核验'
    source='使用当前原生面 SV_FLUX、c0/c1 阶梯截面；只对通过严格 11.9 几何条件的模型成立。'
    out=[SUMMARY_STYLE, _header(status,source), '<div class="af-kpis">',
         _kpi('片间通道总数',str(metrics['channel_count']),'条；上下层分别统计'),
         _kpi('总入口净流量',_flux(inlet),'kg/s'),
         _kpi('总出口净流量',_flux(outlet),'kg/s'),
         _kpi('入口/出口质量差',_num(metrics['mass_imbalance_pct'],6,'%')),
         _kpi('进出口总压降',_num(metrics['pressure_drop_pa_low_speed_approx'],6),'Pa · 低速近似'),
         _kpi('挡板孔总数',str(metrics['baffle_holes']),'孔；仅 11.9 规则'),
         _kpi('挡板全部孔流量 CV',_num(metrics.get('baffle_hole_cv_pct'),3,'%')),
         _kpi('集气腔表压极差',_num(metrics.get('plenum_gauge_range_pa'),6),'Pa；单元质心采样'),
         '</div>', '<h2>片间与旁路核心数据</h2>', _layer_summary(layers,inlet),
         _observations(layers,metrics['mass_imbalance_pct']),
         '<p class="af-muted">Data 内部残差 WB 计数：'+_e(metrics.get('wb_residual_iteration_count_metadata','未取得'))+
         '（仅元数据，不等于已证明的当前解总迭代数，也不证明收敛）。</p>',
         _channel_curve(layers),
         '<div class="af-context"><strong>解释边界：</strong>上、下层为串联流路；中央/外围分区只在同层内互斥。'
         '这些数值来自所示 Case/Data 保存快照，不能单凭保存解判断残差、压降及 CV 已持续稳定。'
         '总压降按低速近似 p + ρ|u|²/2 及端口质量通量加权。</div>',
         '</section>']
    return ''.join(out)


def adaptive_summary(metrics):
    """Auto-inferred geometry: never imply physical region calibration."""
    layers=metrics.get('layers') or {}; ports=metrics.get('port_flux') or {}
    pressure=metrics.get('pressure') or {}
    inlet=ports.get('inlet_kg_s')
    pval=pressure.get('pressure_drop_pa') if str(pressure.get('status','')).startswith('computed_pressure_proxy_') else None
    all_channels=sum(r.get('channel_count',0) for r in layers.values() if isinstance(r,dict))
    out=[SUMMARY_STYLE, _header('自适应几何候选值 · 尚未独立确认晶圆及旁路分区',
         'H5 原生 SV_FLUX 已用于候选阶梯截面；正式模型比较前须审核物理区域与截面。'),
         '<div class="af-kpis">',_kpi('候选通道数',str(all_channels),'条'),
         _kpi('入口净流量',_flux(inlet),'kg/s'),
         _kpi('出口净流量',_flux(ports.get('outlet_kg_s')),'kg/s'),
         _kpi('质量流量差',_num(ports.get('mass_imbalance_pct'),6,'%')),
         _kpi('总压降候选',_num(pval,6) if pval is not None else '未取得','Pa · 低速近似，不等于收敛证明'),
         '</div>', '<h2>候选片间与旁路数据</h2>',_layer_summary(layers,inlet),
         _observations(layers,ports.get('mass_imbalance_pct')),
         '<h2>核验重点</h2><ul class="af-facts">']
    for label,layer in layers.items():
        sens=layer.get('cut_height_sensitivity') or {}
        s=sens.get('status','未检查')
        out.append('<li>'+_e({'upper':'上层','lower':'下层','single':'单层'}.get(label,label))+': 截面 ±1 mm 检查 '+_e(s)+'</li>')
    out += ['<li>检查识别出的实际晶圆、边界区与 X/Z 旁路，必要时调整几何配置。</li>',
            '<li>确认 Case/Data 来自同一网格修订和保存状态，核验迭代及监控历史。</li>',
            '</ul><div class="af-context">候选数据不能直接作为正式模型优劣排序。正、反向通量和逐通道明细保留在下文。'
            '上下层串联，不得将占入口比例相加。</div></section>']
    return ''.join(out)


def inspection_summary(result):
    status=result.get('physical_analysis_status','未取得')
    return (SUMMARY_STYLE + _header('仅完成保存解读取/结构诊断；未取得可信的完整片间物理结果',
             'Case/Data 可读取不等于自动几何识别成功；不应生成虚构的 CV、旁路占比或压降。')+
            '<div class="af-kpis">'+_kpi('分析状态',str(status))+_kpi('片间 CV','未取得')+
            _kpi('中央及外围旁路','未取得')+_kpi('收敛结论','未验证')+'</div>'+
            '<p class="af-context">请核查原生 SV_FLUX、网格拓扑、晶圆站点和测量截面；完整诊断及缺失原因见下方。</p>'+
            '</section>')


def insert_summary(original_html, summary_fragment):
    """Prepend overview to a *generated* HTML report, preserving all original detail."""
    marker=re.search(r'<h1(?:\s[^>]*)?>',original_html,re.I)
    if not marker:
        raise ValueError('Cannot locate generated report main heading')
    return original_html[:marker.start()] + summary_fragment + original_html[marker.start():]


def create_reference_overview(original_html_path, metrics, *, provenance_label=None):
    """Leave the trusted source-script HTML untouched; publish companion overview."""
    src=Path(original_html_path)
    target=src.with_name(src.stem+'.summary.html')
    original=src.read_text(encoding='utf-8')
    summary=reference_summary(metrics)
    if provenance_label:
        summary=summary.replace('</section>', '<p class="af-muted">数据来源：'+_e(provenance_label)+'</p></section>', 1)
    content=insert_summary(original,summary)
    with target.open('x',encoding='utf-8') as fp:
        fp.write(content)
    return str(target)
