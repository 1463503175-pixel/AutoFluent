# -*- coding: utf-8 -*-
"""Regenerate an offline report from stored physical JSON without H5 rereads."""
import argparse
import json
from pathlib import Path
import uuid


def render_stored_json(source):
    from af_report_v3 import render_native_report,render_inspection_report,normalize_reference_metrics,render_reference_report
    raw=json.loads(Path(source).read_text(encoding='utf-8-sig'))
    schema=raw.get('schema')
    if schema=='autofluent.adaptive_native.v1':
        if raw.get('status')=='partial_physical_metrics':
            return render_inspection_report({'schema':'autofluent.offline_case_data.v1',
                'physical_analysis_status':'partial_physical_metrics','partial_native':raw,
                'module_results':raw.get('modules',{}),
                'first_full_analysis_blocker':raw.get('first_full_analysis_blocker'),
                'model_id':Path(raw.get('provenance',{}).get('case_path','model')).name})
        return render_native_report(raw)
    if schema=='autofluent.offline_case_data.v1':
        return render_inspection_report(raw)
    if schema=='autofluent.common_cut_recomputed.v1':
        from af_common_cut_compare import render_common_cut_html
        return render_common_cut_html(raw)
    raise ValueError('Unsupported stored JSON schema for lossless report regeneration: '+str(schema))


def main():
    p=argparse.ArgumentParser(description='Create shareable HTML from saved JSON, no CFF reread')
    p.add_argument('--json',required=True);p.add_argument('--output-dir',required=True)
    a=p.parse_args()
    try:
        html=render_stored_json(a.json)
        folder=Path(a.output_dir).expanduser().resolve();folder.mkdir(parents=True,exist_ok=True)
        path=folder/('rendered_'+uuid.uuid4().hex[:10]+'.html')
        with path.open('x',encoding='utf-8') as f:f.write(html)
        print(json.dumps({'status':'report_rendered_from_json','html_file':str(path),'no_h5_reread':True},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status':'error','reason':str(exc)[:500]},ensure_ascii=False));raise SystemExit(1)
if __name__=='__main__':main()
