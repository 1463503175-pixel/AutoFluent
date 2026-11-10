"""Compare two locally imported flux exports without pretending to read Fluent."""
import json
import sys
from pathlib import Path
from af_metrics import flux_metrics
from af_codex_import import import_saved_report
from af_saved_compare import compare_saved_results
from af_native_profile import compare_native_metrics
from af_adaptive_compare import compare_native_saved

def compare_records(a,b):
    ra,rb=flux_metrics(a),flux_metrics(b)
    pa,pb=ra['provenance'],rb['provenance']
    reasons=[]
    if ra['provenance_status']!='metadata_present_not_independently_verified' or rb['provenance_status']!='metadata_present_not_independently_verified':
        reasons.append('模型标识、工况/测量模板或 Case/Data 身份元数据不完整')
    for key in ('condition_template_sha256','measurement_definition','partition_rule_version'):
        if pa.get(key)!=pb.get(key): reasons.append('两模型不满足同口径条件：'+key)
    shared=sorted(set(ra['layers']).intersection(rb['layers']))
    if not shared: reasons.append('没有可对应的上/下层通道分组')
    indicators={}
    for layer in shared:
        aa,bb=ra['layers'][layer],rb['layers'][layer]
        indicators[layer]={'channels':[aa['channels'],bb['channels']],
            'mass_fraction_pct':[aa['fraction_of_inlet_pct'],bb['fraction_of_inlet_pct']],
            'fraction_delta_pp':bb['fraction_of_inlet_pct']-aa['fraction_of_inlet_pct'],
            'cv_pct':[aa['cv_pct'],bb['cv_pct']],
            'cv_delta_pp':None if aa['cv_pct'] is None or bb['cv_pct'] is None else bb['cv_pct']-aa['cv_pct']}
    bypass_comparison={}
    for layer in shared:
        same_bypasses = sorted(set(ra['bypass'].get(layer,{})) & set(rb['bypass'].get(layer,{})))
        if same_bypasses:
            bypass_comparison[layer] = {name: [ra['bypass'][layer][name]['fraction_of_inlet_pct'],
                                                rb['bypass'][layer][name]['fraction_of_inlet_pct']]
                                         for name in same_bypasses}
    return {'status':'not_qualified' if reasons else 'logical_preliminary_only',
            'reasons':reasons,'bypass_comparison_pct':bypass_comparison,
            'models':[pa.get('model_id'),pb.get('model_id')],
            'shared_layers':shared,'indicators':indicators,
            'mass_imbalance_pct':[ra['mass_imbalance_pct'],rb['mass_imbalance_pct']],
            'same_template_sha256':pa['condition_template_sha256'],
            'warning':'元数据一致性只证明输入声明口径相同，尚未独立核验 Case/Data、几何和实际收敛；禁止正式排名。'}

def read_limited(path):
    f=Path(path)
    if not f.is_file() or f.stat().st_size>32*1024*1024:raise ValueError('文件不存在或超过32MB')
    if f.suffix.lower()=='.json':
        try:
            raw=json.loads(f.read_text(encoding='utf-8-sig'))
            if isinstance(raw,dict) and raw.get('schema') in ('autofluent.offline_case_data.v1','autofluent.native_flux_metrics.v1','autofluent.adaptive_native.v1'):
                return raw
        except (UnicodeError,ValueError):
            pass
    return import_saved_report(f)

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('usage: af_compare.py modelA.json modelB.json')
    a,b=read_limited(sys.argv[1]),read_limited(sys.argv[2])
    if a.get('schema') in ('autofluent.native_flux_metrics.v1','autofluent.adaptive_native.v1') or b.get('schema') in ('autofluent.native_flux_metrics.v1','autofluent.adaptive_native.v1'):
        if a.get('schema')==b.get('schema')=='autofluent.native_flux_metrics.v1':
            result=compare_native_metrics(a,b)
        else:
            result=compare_native_saved(a,b)
    elif a.get('schema')=='autofluent.offline_case_data.v1' or b.get('schema')=='autofluent.offline_case_data.v1':
        if a.get('native_direct',{}).get('metrics') and b.get('native_direct',{}).get('metrics'):
            result=compare_native_saved(a,b)
        else:
            result=compare_saved_results(a,b)
    else:
        result=compare_records(a,b)
    print(json.dumps(result,ensure_ascii=False,indent=2))
