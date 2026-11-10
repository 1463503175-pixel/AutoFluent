# -*- coding: utf-8 -*-
"""Recalculate two independently saved CFF solutions on one explicit cut config.

This is a real recalculation, not subtraction of mismatched prior reports.
Unverified boundary conditions/convergence keep all conclusions provisional.
"""
import argparse
from html import escape
import json
from pathlib import Path
import uuid


def compare_saved_pairs_common_cut(before_file, after_file, config, *, before_data=None, after_data=None):
    from af_pairing import resolve_saved_pair
    from af_adaptive_native import analyze_saved_pair
    from af_adaptive_compare import compare_native_saved
    from af_model_config import validate_model_config
    from af_report_v3 import _fmt
    validate_model_config(config)
    # Explicit cut coordinates ensure both jobs use the SAME physical Y section;
    # other geometry signatures also need to match before declaring comparability.
    missing=[x['id'] for x in config['layers'] if x.get('cut_y_m') is None]
    if missing:
        raise ValueError('Common-cut recomputation requires explicit cut_y_m for each layer: '+', '.join(missing))
    ca,da=resolve_saved_pair(before_file,explicit_data=before_data)
    cb,db=resolve_saved_pair(after_file,explicit_data=after_data)
    if ca==cb and da==db:
        raise ValueError('Before and after resolve to exactly the same Case/Data snapshot')
    from af_progress import set_scope, update
    set_scope(0, 45)
    update(1,'对比计算 A','同一配置下读取第一份 Case/Data，计算原生分区')
    try:
        before=analyze_saved_pair(ca,da,config=config)
        set_scope(45,90)
        update(1,'对比计算 B','同一配置下读取第二份 Case/Data，计算原生分区')
        after=analyze_saved_pair(cb,db,config=config)
    finally:
        set_scope(0, 100)
    update(91,'同口径检查','按指标核对实测截面、通道、端口和守恒')
    comparison=compare_native_saved(before,after)
    # Geometry detection is a prerequisite for measured wafer metrics, but
    # port pressure can still be independently compared when wafer pairing fails.
    comparable=bool(comparison['same_cut_candidate'])
    measured_layers={}
    for label in sorted(set(before.get('layers',{}))|set(after.get('layers',{}))):
        first=before.get('layers',{}).get(label,{})
        second=after.get('layers',{}).get(label,{})
        measured_layers[label]={
            'cut_y_m':[first.get('cut_height_m'),second.get('cut_height_m')],
            'net_kg_s':[first.get('net_kg_s'),second.get('net_kg_s')],
            'share_inlet_pct':[first.get('share_inlet_pct'),second.get('share_inlet_pct')],
            'cv_pct':[first.get('cv_pct'),second.get('cv_pct')],
            'wafer_metric_difference_allowed':comparable and label in comparison.get('layers',{})
        }
    shared_spec={
        'axis':config['axis'],
        'layers':[{'id':x['id'],'cut_y_m':x['cut_y_m'],
                   'rows':[{'id':r['id'],'z_range_m':r['z_range_m']} for r in x['rows']]}
                  for x in config['layers']],
        'partition':config.get('partition',{}),
        'ports':config['ports'],
        'wafer_thickness_range_m':config['wafer']['thickness_range_m'],
        'regular_gap_range_m':config['wafer']['regular_gap_range_m']}
    status=('recomputed_same_cut_candidate_not_final_qualified' if comparable else
            'recomputed_but_layout_or_physics_not_comparable')
    update(95,'共同截面结果已生成','仍需人工核验实际工况、收敛和网格策略')
    return {'schema':'autofluent.common_cut_recomputed.v1','status':status,
            'input_paths':{'before':{'case':str(ca),'data':str(da)},
                           'after':{'case':str(cb),'data':str(db)}},
            'shared_measurement_config':shared_spec,
            'each_result_status':[before.get('status'),after.get('status')],
            'observations_by_layer':measured_layers,
            'comparison':comparison,
            'source_results':{'before':before,'after':after},
            'limitations':['Both saved solutions were recomputed independently with a shared config',
                           'A matching config cannot prove identical physical conditions or convergence',
                           'Wafer difference remains blocked if measured geometry/layout is inconsistent',
                           'Results are candidate observations only; not an automatically qualified design ranking']}


def render_common_cut_html(result):
    """Single-file preview of both actual calculated snapshots, no external assets."""
    from af_report_v3 import STYLE, _fmt
    e=lambda x:escape(str(x),quote=True)
    cmp=result['comparison']
    rows=[]
    for key,r in result['observations_by_layer'].items():
        cr=cmp.get('layers',{}).get(key,{})
        rows.append('<tr>'+''.join('<td>%s</td>'%e(v) for v in (
            key,_fmt(r['cut_y_m'][0]),_fmt(r['cut_y_m'][1]),
            _fmt(r['share_inlet_pct'][0]),_fmt(r['share_inlet_pct'][1]),
            _fmt(cr.get('fraction_delta_pp')) if r['wafer_metric_difference_allowed'] else '不可比较',
            _fmt(r['cv_pct'][0]),_fmt(r['cv_pct'][1]),
            _fmt(cr.get('cv_delta_pp')) if r['wafer_metric_difference_allowed'] else '不可比较'))+'</tr>')
    reasons=''.join('<p class="small">• %s</p>'%e(x) for x in cmp.get('reasons',[]))
    p=cmp.get('pressure_drop_pa',[None,None])
    pd=cmp.get('pressure_drop_delta_pa')
    details=e(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AutoFluent · 统一截面复算</title>
<style>'''+STYLE+'''</style><body><main><header><p class="eyebrow">AutoFluent / 离线同口径对比</p><h1>仿真结果与结构优化</h1><p>两份 Fluent 保存解重新计算；未验证求解收敛和全部工况，因此只给出候选观测值。</p>
<p>状态：'''+e(result['status'])+'''</p></header><section><h2>共同截面与晶圆指标</h2><div class="scroll"><table><tr><th>层</th><th>前截面 Y(m)</th><th>后截面 Y(m)</th><th>前片间 %</th><th>后片间 %</th><th>变化 pp</th><th>前 CV%</th><th>后 CV%</th><th>变化 pp</th></tr>'''+''.join(rows)+'''</table></div>
<p class="small">仅相同实测通道 X 边界、Z 分区与测量位置等口径通过检查时展示差值。</p></section><section><h2>端口总压降</h2>
<p>前：'''+_fmt(p[0])+''' Pa；后：'''+_fmt(p[1])+''' Pa；候选差值：'''+(_fmt(pd)+' Pa' if pd is not None else '不可比较')+'''。</p><p class="small">端口比较不以晶圆截面是否相同作为单独阻断条件；仍须检查压力定义、工况与收敛。</p></section>
<section><h2>可信度与限制</h2>'''+reasons+'''</section><section><h2>来源与完整明细</h2><details><summary>两份重新计算的完整结构化结果（展开）</summary><pre>'''+details+'''</pre></details></section></main></body></html>''')


def main():
    p=argparse.ArgumentParser(description='Recalculate two real CFF saves on one explicit geometry cut')
    p.add_argument('--before',required=True,help='Before .cas.h5 or .dat.h5')
    p.add_argument('--after',required=True,help='After .cas.h5 or .dat.h5')
    p.add_argument('--before-data',default=None)
    p.add_argument('--after-data',default=None)
    p.add_argument('--config',required=True)
    p.add_argument('--output-dir',required=True)
    a=p.parse_args()
    try:
        from af_model_config import load_model_config
        r=compare_saved_pairs_common_cut(a.before,a.after,load_model_config(a.config),
            before_data=a.before_data,after_data=a.after_data)
        folder=Path(a.output_dir).expanduser().resolve();folder.mkdir(parents=True,exist_ok=True)
        name='common_cut_'+uuid.uuid4().hex[:10]
        jp=folder/(name+'.json');hp=folder/(name+'.html')
        with jp.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2,allow_nan=False)
        with hp.open('x',encoding='utf-8') as f:f.write(render_common_cut_html(r))
        from af_progress import update
        update(100,'统一截面对比输出完成','真实数据已重新计算，仍未独立认证收敛',force=True)
        print(json.dumps({'status':r['status'],'same_cut_candidate':r['comparison']['same_cut_candidate'],
            'json_file':str(jp),'html_file':str(hp),'warning':'Real model acceptance and convergence still pending'},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status':'error','stage':'common_cut_recomputation',
                          'reason':str(exc)[:650]},ensure_ascii=False))
        raise SystemExit(1)

if __name__=='__main__':main()
