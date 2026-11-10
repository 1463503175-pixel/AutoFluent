# -*- coding: utf-8 -*-
"""Read-only bounded inventory of Fluent saved Case/Data pairs.
No CFD field extraction or physical metrics are inferred from filenames.
"""
import argparse
import html
import json
import os
from pathlib import Path
import time
import uuid
from af_h5_offline import CAS_SUFFIX, DAT_SUFFIX, stat_record, resolve_pair

MAX_DIRS = 1000
MAX_DEPTH = 6
MAX_FILES = 120


def discover_pairs(directory):
    """Enumerate *each* saved Data snapshot, not one arbitrary Data per Case.

    File-list mode uses the same authoritative Data settings/Case File lookup
    as single-file mode, while retaining explicit errors for orphan files.
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('请选择存在的工程目录')
    pairs, unmatched = [], []
    dir_count = 0
    limited = False
    for folder_name, dirs, names in os.walk(root, followlinks=False):
        dir_count += 1
        if dir_count > MAX_DIRS:
            limited = True
            break
        folder = Path(folder_name)
        depth = len(folder.relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() not in
                         ('node_modules', '__pycache__', 'build', 'dist', '.git'))
        if depth >= MAX_DEPTH: dirs[:] = []
        dat_names=sorted(n for n in names if n.casefold().endswith(DAT_SUFFIX))
        cas_names=sorted(n for n in names if n.casefold().endswith(CAS_SUFFIX))
        paired_cases=set()
        for name in dat_names:
            data=folder/name
            try:
                case,resolved_data=resolve_pair(data)
                pairs.append((case,resolved_data))
                paired_cases.add(case.name.casefold())
            except (OSError,FileNotFoundError,ValueError) as exc:
                # Preserve missing_data's legacy meaning: a Case with no Data.
                # Orphan Data can be inspected independently by selecting it.
                pass
            if len(pairs)+len(unmatched)>=MAX_FILES:
                limited=True;break
        if limited:break
        for name in cas_names:
            if name.casefold() not in paired_cases:
                unmatched.append('%s: no matching Data snapshot with a verified Case pointer or identical basename'%(folder/name))
            if len(pairs)+len(unmatched)>=MAX_FILES:
                limited=True;break
        if limited:break
    pairs.sort(key=lambda p: -max(p[0].stat().st_mtime_ns, p[1].stat().st_mtime_ns))
    return root,pairs,unmatched,limited


def inventory(directory):
    root, pairs, unmatched, limited = discover_pairs(directory)
    results = []
    for case, data in pairs:
        try:
            c, d = stat_record(case), stat_record(data)
            results.append({'model_id': case.name[:-len(CAS_SUFFIX)],
                            'snapshot_name':data.name,
                            'snapshot_mtime_ns':data.stat().st_mtime_ns,
                            'case': c, 'data': d,
                            'hdf5_headers_ok': c['hdf5_signature'] and d['hdf5_signature'],
                            'pairing': 'data_case_reference_or_same_stem_fallback',
                            'mesh_and_solution_consistency': 'not_verified',
                            'physical_metrics': 'not_computed'})
        except OSError as exc:
            unmatched.append('%s: %s' % (case, exc))
    return {'schema': 'autofluent.h5_inventory.v1', 'root': str(root),
            'status': 'paired_candidates_found' if results else 'no_pairs',
            'pairs': results, 'missing_data': unmatched, 'search_limited': limited,
            'warning': '仅发现保存的 Case/Data 文件对并检查 HDF5 文件头；未独立计算片间流量、旁路或 CV。'}


def save_inventory(report, output):
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = 'h5_inventory_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:7]
    json_path, html_path = output / (stem+'.json'), output / (stem+'.html')
    with json_path.open('x', encoding='utf-8') as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    rows = []
    for p in report['pairs']:
        status = '文件头有效' if p['hdf5_headers_ok'] else '文件头不符'
        items = (p['model_id'], p['case']['path'], p['data']['path'], status)
        rows.append('<tr>%s</tr>' % ''.join('<td>%s</td>' % html.escape(str(x)) for x in items))
    content = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AutoFluent H5 保存解清单</title>
<style>body{font:15px/1.55 'Microsoft Yahei',sans-serif;color:#183147;margin:30px;background:#f6f8fa}
table{width:100%%;border-collapse:collapse;background:white}th,td{border-bottom:1px solid #dce3e7;padding:10px;text-align:left;word-break:break-all}
th{background:#e3f4f0}.notice{background:white;padding:12px;border-left:3px solid #aa7e2c}</style>
<h1>AutoFluent · H5 保存解清单</h1><p class="notice">仅离线发现同目录同名 Case/Data；未进行场数据计算，不是完整分析报告。</p>
<table><thead><tr><th>模型</th><th>Case</th><th>Data</th><th>检查</th></tr></thead><tbody>%s</tbody></table>
<p>找到 %d 对，缺少 Data 的 Case %d 项；达到检索上限：%s</p></html>''' % (
        ''.join(rows), len(report['pairs']), len(report['missing_data']), str(report['search_limited']))
    with html_path.open('x', encoding='utf-8') as fp:
        fp.write(content)
    return {'status': 'inventory_saved', 'pairs': len(report['pairs']),
            'unpaired': len(report['missing_data']), 'json_file': str(json_path),
            'html_file': str(html_path), 'physical_analysis_status': 'not_computed'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--output-dir', required=True)
    a = p.parse_args()
    try:
        print(json.dumps(save_inventory(inventory(a.root), a.output_dir), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
