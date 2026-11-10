"""Analysis foundation: data-only, no pretend live Fluent data.

Input is an exported, verified mass-flux partition; not an interpolation of velocity.
"""
import json
import math
import statistics
import re
from pathlib import Path
from af_codex_import import import_saved_report

def flux_metrics(record):
    if record.get('source') not in ('fluent-native-face-flux','verified-RT0'):
        raise ValueError('缺少经核验的原生面通量或 RT0 数据来源')
    m_in=record.get('inlet_mass_kg_s')
    if isinstance(m_in,bool) or not isinstance(m_in,(int,float)) or not math.isfinite(m_in) or m_in<=0:
        raise ValueError('入口质量流量必须为正数')
    out=record.get('outlet_net_mass_kg_s')
    if out is not None and (isinstance(out,bool) or not isinstance(out,(int,float)) or not math.isfinite(out)):
        raise ValueError('出口净质量流量必须为有限数值')
    raw_layers=record.get('layers',{})
    if not isinstance(raw_layers,dict) or not raw_layers:
        raise ValueError('必须提供至少一层真实通道流量')
    layers={}
    for lname,channels in raw_layers.items():
        if not isinstance(channels,list) or not channels or not all(not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v) for v in channels):
            raise ValueError('通道数据缺失或非数值')
        mean=statistics.fmean(channels)
        layers[lname]={'channels':len(channels),'net_mass_kg_s':sum(channels),
            'fraction_of_inlet_pct':sum(channels)/m_in*100,
            'cv_pct':statistics.pstdev(channels)/mean*100 if mean>0 else None,
            'minimum_kg_s':min(channels),'maximum_kg_s':max(channels),
            'mean_kg_s':mean}
    m_out=out
    balance=None if m_out is None else abs(m_in-m_out)/m_in*100
    prov=record.get('provenance',{})
    if not isinstance(prov,dict): prov={}
    required=('model_id','condition_template_sha256','measurement_definition','partition_rule_version','case_data_identity')
    missing=[x for x in required if not isinstance(prov.get(x),str) or not prov[x].strip()]
    if prov.get('condition_template_sha256') and not re.fullmatch('[0-9a-fA-F]{64}',str(prov['condition_template_sha256'])):
        missing.append('condition_template_sha256_format')
    qualified=not missing
    bypass = {}
    for lname, regions in record.get('bypass', {}).items():
        if lname not in layers or not isinstance(regions, dict): continue
        bypass[lname] = {}
        for name, values in regions.items():
            if not isinstance(values, dict): continue
            fwd, rev, net = (values.get(k) for k in ('forward_kg_s','reverse_kg_s','net_kg_s'))
            if any(isinstance(v, bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (fwd,rev,net)):
                raise ValueError('旁路流量字段错误：%s' % name)
            if abs(fwd-rev-net)>max(2e-13,abs(net)*5e-7):
                raise ValueError('旁路正反向不闭合：%s' % name)
            bypass[lname][name] = dict(forward_kg_s=fwd,reverse_kg_s=rev,net_kg_s=net,
                                       fraction_of_inlet_pct=net/m_in*100)
    return {'source':record['source'],'layers':layers,'bypass':bypass,
        'verification':'external_report_not_independently_verified' if record.get('source_claim_is_external') else 'input_file_only',
        'source_warnings':record.get('warnings',[]),
        'mass_imbalance_pct':balance,'basis':'total inlet mass flow',
        'provenance':{x:prov.get(x) for x in required},
        'provenance_status':'metadata_present_not_independently_verified' if qualified else 'incomplete',
        'missing_provenance':sorted(set(missing)),
        'warning':'来源标记和元数据由输入文件提供，软件未独立验算 Fluent 原始 Case/Data；不同串联截面片间占比不能相加，流量分区闭合须按真实几何核验。'}

def main():
    import sys
    if len(sys.argv)!=2:
        raise SystemExit('usage: python af_metrics.py <verified_export.json>')
    print(json.dumps(flux_metrics(import_saved_report(sys.argv[1])),
                     ensure_ascii=False,indent=2))
if __name__=='__main__':main()
