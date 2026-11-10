# -*- coding: utf-8 -*-
"""Bounded, read-only discovery of existing Codex Fluent result reports.

Source files are never modified. Historical reports are NOT independently
verified against Fluent Case/Data and cannot be used for official ranking.
"""
import argparse
import html
import json
import os
from pathlib import Path
import re
import time
import uuid
from af_codex_import import import_saved_report
from af_metrics import flux_metrics

MAX_FILES=120
MAX_DIRS=1400
MAX_DEPTH=8
MAX_SIZE=32*1024*1024


def candidates(root):
    base=Path(root).resolve()
    if not base.is_dir(): raise ValueError('请指定包含历史统计报告的工程文件夹')
    count_dir=0
    files=[]
    for folder, dirs, names in os.walk(base,followlinks=False):
        count_dir+=1
        if count_dir>MAX_DIRS: break
        depth=len(Path(folder).relative_to(base).parts)
        if depth>=MAX_DEPTH: dirs[:]=[]
        dirs[:]=sorted([d for d in dirs if not d.startswith(('.','__')) and d.lower() not in ('node_modules','.git','build','dist')])
        for name in names:
            if name.lower().startswith(('codex_library_','h5_inventory_','h5_index_')): continue
            if Path(name).suffix.lower() not in ('.html','.htm','.json'): continue
            path=Path(folder)/name
            try: size=path.stat().st_size
            except OSError:continue
            if not 80<=size<=MAX_SIZE:continue
            try:
                with path.open('rb') as f: head=f.read(128*1024)
            except OSError:continue
            # Filter out web app HTML and arbitrary JSON before parsing it.
            if not ((b'\xe6\x99\xb6\xe5\x9c\x86\xe9\x80\x9a\xe9\x81\x93' in head or b'\xe5\x85\xa5\xe5\x8f\xa3\xe5\x90\x88\xe8\xae\xa1' in head or b'"layers"' in head)
                    and (b'Fluent' in head or b'fluent' in head or b'\xe7\x89\x87\xe9\x97\xb4' in head or b'\xe6\x99\xb6\xe5\x9c\x86' in head)):
                continue
            files.append(path)
            if len(files)>=MAX_FILES:break
        if len(files)>=MAX_FILES:break
    return sorted(files,key=lambda x:x.stat().st_mtime,reverse=True)


def analyze_library(root):
    seen=set(); records=[]; skipped=[]
    for source in candidates(root):
        try:
            row=import_saved_report(source)
            metric=flux_metrics(row)
            digest=row.get('provenance',{}).get('report_sha256')
            if digest and digest in seen:continue
            if digest:seen.add(digest)
            records.append({'path':str(source),'model_id':row['provenance'].get('model_id',''),
                            'source_sha256':digest, 'layers':metric['layers'],
                            'bypass':metric['bypass'],
                            'mass_imbalance_pct':metric['mass_imbalance_pct'],
                            'provenance_status':metric['provenance_status'],
                            'measurement_definition':metric['provenance'].get('measurement_definition'),
                            'partition_rule_version':metric['provenance'].get('partition_rule_version'),
                            'condition_template_sha256':metric['provenance'].get('condition_template_sha256'),
                            'warnings':row.get('warnings',[])})
        except (ValueError,KeyError,TypeError,UnicodeError, OSError) as exc:
            skipped.append({'path':str(source),'reason':str(exc)[:180]})
    return {'schema':'autofluent.saved_report_library.v1','root':str(Path(root).resolve()),
            'verification':'EXTERNAL_HISTORICAL_REPORTS_ONLY',
            'comparison_qualification':'NOT_QUALIFIED_FOR_FORMAL_RANKING',
            'records':records, 'skipped':skipped,
            'warning':'仅复用已有 Codex 报告；未重算 Fluent 场，也未验证网格、工况与收敛。模型仅供初步查看，不做优劣排名。'}


def fmt_number(x, digits=3):
    if x is None:return '未取得'
    return ('%.'+str(digits)+'f') % x


def html_summary(result):
    e=html.escape
    rows=[]
    for record in result['records']:
        layers=record['layers']
        cells=[]
        for layer in ('upper','lower'):
            item=layers.get(layer)
            if item:
                cells.append('<td>%d</td><td>%s%%</td><td>%s%%</td>' %
                             (item['channels'],fmt_number(item['fraction_of_inlet_pct']),fmt_number(item['cv_pct'])))
            else:cells.append('<td colspan="3">未取得</td>')
        rows.append('<tr><td>%s</td><td><code>%s</code></td>%s<td>%s%%</td><td>未独立核验</td></tr>' %
                    (e(str(record['model_id'])),e(record['path']),''.join(cells),fmt_number(record['mass_imbalance_pct'])))
    if not rows:rows=['<tr><td colspan="10">未发现符合格式的历史质量通量统计报告</td></tr>']
    th=''.join('<th>'+k+'</th>' for k in ('模型','真实文件路径','上层通道','上层占入口','上层CV','下层通道','下层占入口','下层CV','质量不平衡','核验'))
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AutoFluent 历史模型数据</title>
<style>body{font:15px/1.6 'Microsoft Yahei',system-ui,sans-serif;margin:36px;background:#f6f8fa;color:#182a3c}h1{font-size:25px}p{max-width:1150px}table{border-collapse:collapse;width:100%%;background:white;font-size:14px}th,td{padding:12px 10px;border-bottom:1px solid #e1e6ec;text-align:left}th{background:#e9f4f2}code{word-break:break-all;font-size:12px}footer{font-size:12px;margin-top:20px;color:#566}.notice{background:white;border-left:4px solid #b57612;padding:12px}</style>
<h1>AutoFluent · 历史 Codex 统计汇总</h1><p class="notice">所有数据均来自既有 HTML/JSON 文件，尚未独立验证 Case/Data、收敛、工况及测量口径。不同模型仅作初步并列展示，不进行性能排名。上下层为串联位置，不应相加。</p>
<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table><footer>源目录：%s · 文件数：%d · 未能解析：%d</footer></html>''' % (th, ''.join(rows), e(result['root']),len(result['records']),len(result['skipped']))


def save_library(result, output):
    root=Path(output).resolve(); root.mkdir(parents=True,exist_ok=True)
    stem='codex_library_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:7]
    out_json=root/(stem+'.json');out_html=root/(stem+'.html')
    out_json.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    out_html.write_text(html_summary(result),encoding='utf-8')
    return {'status':'library_imported' if result['records'] else 'no_supported_reports',
            'record_count':len(result['records']),'skipped_count':len(result['skipped']),
            'json_file':str(out_json),'html_file':str(out_html),
            'models':[r['model_id'] for r in result['records']],
            'verification':'external_reports_only_not_formal_comparison'}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--output-dir',required=True)
    args=ap.parse_args()
    print(json.dumps(save_library(analyze_library(args.root),args.output_dir),ensure_ascii=False))
