# -*- coding: utf-8 -*-
"""Import REAL saved Codex/Fluent tables; never invent or reconstruct face flux.

Supports the compact '当前解只读统计.html' table exported in November 2026.
The numbers are read from an external report, not independently checked against Case/Data.
"""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

MAX_BYTES = 32 * 1024 * 1024


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.in_cell = [], None, None, False
        self.text_parts, self.in_table = [], False

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.in_table = True
        if tag == 'tr' and self.in_table: self.row = []
        if tag in ('th', 'td') and self.row is not None:
            self.cell, self.in_cell = [], True

    def handle_data(self, data):
        if self.in_cell and self.cell is not None: self.cell.append(data)
        else: self.text_parts.append(data)

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.in_cell:
            self.row.append(''.join(self.cell).strip())
            self.in_cell, self.cell = False, None
        if tag == 'tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row = None
        if tag == 'table': self.in_table = False


def number(value, context):
    try: v = float(str(value).strip())
    except (ValueError, TypeError): raise ValueError('非数字字段 %s: %r' % (context, value))
    import math
    if not math.isfinite(v): raise ValueError('非有限数值 %s' % context)
    return v


def import_saved_report(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('统计文件不存在或超过 32 MB')
    data = path.read_bytes()
    if path.suffix.lower() == '.json':
        obj = json.loads(data.decode('utf-8-sig'))
        if not isinstance(obj, dict) or 'layers' not in obj:
            raise ValueError('JSON 不是逐通道统计文件；不能根据汇总数据编造通道值')
        return obj
    if path.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('只支持 Codex 统计 HTML 或逐通道 JSON')
    html = data.decode('utf-8-sig')
    parser = TableReader()
    parser.feed(html)
    header_ok = any(r and '区域' in r[0] for r in parser.rows)
    if not header_ok:
        raise ValueError('无法识别 Codex 原生面通量表头')
    channels = {'upper': {}, 'lower': {}}
    other = {'upper': {}, 'lower': {}}
    sections = {}
    ports = {'inlet': [], 'outlet': []}
    notes = []
    for row in parser.rows:
        if len(row) < 6: continue
        name = row[0].strip()
        if not name: continue
        m = re.fullmatch(r'(上层|下层)晶圆通道\s*(\d+)', name)
        if m:
            side = 'upper' if m.group(1) == '上层' else 'lower'
            idx = int(m.group(2))
            if idx in channels[side]: raise ValueError('重复的通道 %s' % name)
            fwd, rev, net = (number(row[i], name) for i in (3, 4, 5))
            if fwd < 0 or rev < 0 or abs(fwd - rev - net) > max(2e-13, abs(net)*5e-7):
                raise ValueError('正反向/净流量不闭合：'+name)
            channels[side][idx] = net
            continue
        m = re.fullmatch(r'入口(\d+)|出口(\d+)', name)
        if m:
            portkind = 'inlet' if m.group(1) else 'outlet'
            ports[portkind].append((int(m.group(1) or m.group(2)), number(row[5], name)))
            continue
        if name in ('入口合计', '出口合计'):
            ports['inlet_total' if name == '入口合计' else 'outlet_total'] = number(row[5], name)
            continue
        if name.startswith(('上层', '下层')):
            side = 'upper' if name.startswith('上层') else 'lower'
            if any(k in name for k in ('中央旁路', '外围旁路')):
                other[side][name] = dict(forward_kg_s=number(row[3], name), reverse_kg_s=number(row[4], name), net_kg_s=number(row[5], name))
            elif '原生面全截面' in name:
                sections[side] = dict(net_kg_s=number(row[5],name), description=row[6] if len(row)>6 else '')
    if not channels['upper'] and not channels['lower']:
        raise ValueError('未读取到任何晶圆通道，不能生成结果')
    for layer, index_map in channels.items():
        if not index_map: continue
        expected = list(range(1,len(index_map)+1))
        if sorted(index_map) != expected:
            raise ValueError('%s 通道编号缺失或不从1开始：%s' % (layer, sorted(index_map)[:9]))
    inlet = ports.get('inlet_total')
    if inlet is None: inlet = sum(v for _,v in ports['inlet'])
    if inlet <= 0: raise ValueError('缺少入口净质量流量')
    if ports['inlet'] and abs(inlet-sum(v for _,v in ports['inlet'])) > max(1e-13, inlet*1e-5):
        raise ValueError('入口分组与入口合计不闭合')
    outlet = ports.get('outlet_total')
    if outlet is None and ports['outlet']: outlet = sum(v for _,v in ports['outlet'])
    # A file-supplied text label is not independent provenance of solver fields.
    toptext = ' '.join(parser.text_parts)
    match = re.search(r'来源为\s*([^\s的。]+)\s*的\s*FFF', toptext)
    model_id = match.group(1) if match else path.stem
    output = {
        'source': 'fluent-native-face-flux',
        'source_claim_is_external': True,
        'inlet_mass_kg_s': inlet,
        'outlet_net_mass_kg_s': outlet,
        'layers': {k:[v[i] for i in sorted(v)] for k,v in channels.items() if v},
        'bypass':other,
        'sections': sections,
        'port_flows': ports,
        'provenance':{
            'model_id':model_id,
            'case_data_identity':'unverified_saved_html_sha256:'+hashlib.sha256(data).hexdigest(),
            'measurement_definition':'reported-native-mesh-face-partition',
            'partition_rule_version':'unverified_codex_html',
            'condition_template_sha256':'',
            'report_sha256':hashlib.sha256(data).hexdigest(),
            'report_path':str(path),
        },
        'verification':'EXTERNAL_REPORT_ONLY',
        'warnings':['由已有 Codex HTML 数据提取；未重新访问 Fluent Case/Data 或独立验证 SV_FLUX。',
                    '上下层截面为串联位置，片间流量占入口百分比不可相加。',
                    '历史报告可能有截面闭合误差，必须结合原始报告判断。']
    }
    for layer, sec in sections.items():
        total = sum(channels[layer].values()) + sum(v['net_kg_s'] for v in other[layer].values())
        if abs(sec['net_kg_s']-total)>max(1e-13, abs(sec['net_kg_s'])*1e-5):
            notes.append('%s: 片间+旁路与报告全截面不闭合，差额 %.8g kg/s' % (layer, sec['net_kg_s']-total))
    output['warnings'].extend(notes)
    return output


if __name__=='__main__':
    import sys
    if len(sys.argv) != 2: raise SystemExit('usage: python af_codex_import.py <historical-export.html|json>')
    print(json.dumps(import_saved_report(sys.argv[1]), ensure_ascii=False, indent=2))
