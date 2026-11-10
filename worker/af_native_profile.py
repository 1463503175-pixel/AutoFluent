# -*- coding: utf-8 -*-
"""Read-only bridge to the user's *executed* Fluent CFF native-flux method.

Crucial separation:
    native signed-face conservation = general mathematical operation;
    11.9 plate/hole geometry = a *specific calibration*, not universal.

This module leaves the supplied, previously validated 11.9 script unchanged,
invokes it in the SAME process (so UI cancellation does not orphan a child
reader), checks its outputs, and emits machine-readable metrics.  A rejected
geometry is a calibration request, never a zero-valued analysis.
"""
import contextlib
import csv
import hashlib
import io
import json
import math
import re
import runpy
import sys
import time
import uuid
from pathlib import Path

SCRIPT = Path(__file__).with_name('af_native_11_9_reference.py')
PROFILE = 'ald_11_9_native_v1'


def _numeric(row):
    raw = row.get('净流量或绝对误差 (kg/s)', '').strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError('Missing net mass flux on row '+str(row.get('区域')))


def _cv_note(row):
    match = re.search(r'净流量 CV\s+([0-9]+(?:\.[0-9]+)?)%', row.get('说明', ''))
    if not match:
        raise ValueError('Native source did not report measured channel CV for '+row['区域'])
    return float(match.group(1))


def parse_native_csv(csv_path):
    """Prove that reference source's regions partition both native cuts.

    May be called on yesterday's real CSV as an independent parser test; does
    not pretend that parsing the CSV is recomputing CFD from H5.
    """
    with Path(csv_path).open(encoding='utf-8-sig', newline='') as inp:
        rows = list(csv.DictReader(inp))
    by_name = {r.get('区域'):r for r in rows}
    # Section-offset checks intentionally repeat the same label at ±1 mm.
    needed = ['入口合计','出口合计','整体质量守恒','进出口总压降']
    for layer in ('上层','下层'):
        needed += [layer+s for s in ('晶圆片间合计','中央旁路','左外围旁路（低 Z）',
                                      '右外围旁路（高 Z）','原生面全截面')]
    if any(sum(r.get('区域')==k for r in rows)!=1 for k in needed):
        raise ValueError('Reference output is missing required region totals: '+
                         ', '.join(k for k in needed if sum(r.get('区域')==k for r in rows)!=1))
    inlet, outlet = _numeric(by_name['入口合计']),_numeric(by_name['出口合计'])
    if not (inlet>0 and outlet>0):
        raise ValueError('Invalid port directions: positive inlet/outlet required')
    # The unchanged source script reports absolute wall flux but does not
    # assert that it is negligible. A missing-wall or unlabelled opening can
    # make an inlet/outlet-only imbalance deceptively good.
    wall_row=by_name.get('壁面通量检查')
    if wall_row is None:
        raise ValueError('Missing reference wall-flux audit; cannot certify ports-only mass balance')
    wall_note=wall_row.get('说明','')
    wall_match=re.search(r'壁面绝对通量合计\s*([0-9.+\-eE]+)\s*kg/s',wall_note)
    if not wall_match:
        raise ValueError('Unparseable native wall absolute-flux audit')
    wall_abs=float(wall_match.group(1))
    if not math.isfinite(wall_abs) or wall_abs<0 or wall_abs>max(1e-12,inlet*1e-4):
        raise ValueError('Significant wall/unnamed-boundary flux; inlet/outlet balance is not sufficient')
    if abs(abs(inlet-outlet)-_numeric(by_name['整体质量守恒']))>1e-12:
        raise ValueError('Inlet/outlet balance is inconsistent')
    layers={}
    for layer in ('上层','下层'):
        channel_rows=[]
        subrows={}
        for z_label in ('低 Z 排','高 Z 排'):
            prefix=layer+z_label+'晶圆通道 '
            selected=[r for r in rows if r.get('区域','').startswith(prefix)]
            selected.sort(key=lambda r:int(r['区域'][len(prefix):]))
            channel_numbers=[int(r['区域'][len(prefix):]) for r in selected]
            if not selected or channel_numbers!=list(range(1,len(selected)+1)):
                raise ValueError('Missing or repeated '+layer+' '+z_label+' channel indices')
            nums=[_numeric(r) for r in selected]
            group_total=_numeric(by_name[layer+z_label+'晶圆片间合计'])
            if abs(sum(nums)-group_total)>max(1e-12,abs(group_total)*1e-5):
                raise ValueError('Channel sums do not agree with '+layer+z_label)
            subrows[z_label]={'channel_count':len(nums),'net_kg_s':group_total,
                               'channels_net_kg_s':nums,
                               'cv_pct':_cv_note(by_name[layer+z_label+'晶圆片间合计'])}
            channel_rows.extend(nums)
        wafer=_numeric(by_name[layer+'晶圆片间合计'])
        full=_numeric(by_name[layer+'原生面全截面'])
        parts={
            'wafer':wafer,
            'central':_numeric(by_name[layer+'中央旁路']),
            'outer_low_z':_numeric(by_name[layer+'左外围旁路（低 Z）']),
            'outer_high_z':_numeric(by_name[layer+'右外围旁路（高 Z）']),
        }
        if abs(sum(channel_rows)-wafer)>max(1e-12,abs(wafer)*1e-5):
            raise ValueError(layer+' rows do not sum to wafer partition')
        if abs(sum(parts.values())-full)>max(1e-12,abs(full)*1e-5):
            raise ValueError(layer+' exclusive regions do not sum to cut flux')
        if len(channel_rows)!=188:
            raise ValueError('Not a validated 11.9 reference: expected 188 channels per layer')
        mean=sum(channel_rows)/len(channel_rows)
        if abs(mean)<1e-25:
            raise ValueError(layer+' near-zero channel mean: CV undefined')
        cv=100*math.sqrt(sum((v-mean)**2 for v in channel_rows)/len(channel_rows))/abs(mean)
        reported=_cv_note(by_name[layer+'晶圆片间合计'])
        if abs(cv-reported)>.001:
            raise ValueError(layer+' native CSV CV cross-check failed')
        layers['upper' if layer=='上层' else 'lower']={
            'channel_count':len(channel_rows),'channels_net_kg_s':channel_rows,
            'net_kg_s':wafer,'share_inlet_pct':100*wafer/inlet,
            'cv_pct':cv,'partition_net_kg_s':parts,
            'whole_cut_net_kg_s':full,'rows':subrows,
            'closure_error_kg_s':sum(parts.values())-full,
        }
    if sum(x['channel_count'] for x in layers.values())!=376:
        raise ValueError('11.9 reference channel count is not 376')
    hole_row_re=re.compile(r'^挡板孔排 \d+（(\d+) 孔）$')
    hole_count=sum(int(hole_row_re.match(x['区域']).group(1)) for x in rows if hole_row_re.match(x['区域']))
    if hole_count!=245:
        raise ValueError('11.9 reference count is not 245 baffle holes')
    match=re.search(r'^([0-9.eE+-]+) Pa',by_name['进出口总压降']['说明'])
    if not match:
        raise ValueError('Pressure-drop summary missing')
    # Additional measurements already computed by the native source: parse
    # only explicitly reported quantities, never infer empty values as zero.
    hole_cv_match=re.search(r'孔净流量 CV\s+([0-9.eE+\-]+)%',
                            by_name.get('挡板全部孔',{}).get('说明',''))
    plenum_note=by_name.get('底部集气腔静压',{}).get('说明','')
    plenum_match=re.search(r'表压最小\s+([0-9.eE+\-]+) Pa，最大\s+([0-9.eE+\-]+) Pa，极差\s+([0-9.eE+\-]+) Pa',plenum_note)
    # This is a Fluent metadata record, not certified actual snapshot iterations.
    data_note=by_name.get('数据来源',{}).get('说明','')
    wb_count=re.search(r'residuals/wb-iteration-count\s+([0-9]+)',data_note)
    return {
        'schema':'autofluent.native_flux_metrics.v1',
        'profile':PROFILE,
        'measurement':'original SV_FLUX, native staircase faces, 11.9 geometric rules',
        'channel_count':376,'wafer_count':384,'baffle_holes':hole_count,
        'inlet_net_kg_s':inlet,'outlet_net_kg_s':outlet,
        'mass_imbalance_pct':100*abs(inlet-outlet)/inlet,
        'pressure_drop_pa_low_speed_approx':float(match.group(1)),
        'baffle_hole_cv_pct':float(hole_cv_match.group(1)) if hole_cv_match else None,
        'plenum_gauge_min_pa':float(plenum_match.group(1)) if plenum_match else None,
        'plenum_gauge_max_pa':float(plenum_match.group(2)) if plenum_match else None,
        'plenum_gauge_range_pa':float(plenum_match.group(3)) if plenum_match else None,
        'wb_residual_iteration_count_metadata':int(wb_count.group(1)) if wb_count else None,
        'layers':layers,
        'checks':{'pair_partition_per_layer':'pass','channel_to_layer_sums':'pass',
                  'csv_cv_recomputed':'pass','global_mass_balance':'pass',
                  'wall_absolute_flux_kg_s':wall_abs,'wall_flux_audit':'pass'},
        'limitations':['11.9 axis and wafer/baffle geometry assumptions',
                       'no proof of solver convergence from Case/Data alone',
                       'native-face staircase partition assigns a crossing face by centroid',
                       'upper/lower cuts are in series; never add their shares'],
    }


def _preflight(case,data):
    """Avoid running the 11.9 script on obviously unrelated H5 layouts."""
    import h5py
    with h5py.File(case,'r') as c,h5py.File(data,'r') as d:
        cff=('meshes/1/faces/zoneTopology','meshes/1/nodes/coords/4',
             'meshes/1/faces/nodes/1','meshes/1/faces/c0/1',
             'meshes/1/faces/c1/1','meshes/1/cells/zoneTopology/maxId')
        missing=[s for s in cff if s not in c]
        if missing:
            return False, 'CFF fields unavailable: '+', '.join(missing)
        if 'results/1/phase-1/faces/SV_FLUX/1' not in d:
            return False, 'Original full-range face SV_FLUX is unavailable'
        if 'settings/Case File' not in d:
            return False, 'Data has no internal Case File reference'
        raw=d['settings/Case File'][0]
        named=raw.decode(errors='replace') if isinstance(raw,bytes) else str(raw)
        if Path(named.replace('\\','/')).name!=case.name:
            return False, 'Data internal Case File name does not match input Case'
        raw_names=c['meshes/1/faces/zoneTopology/name'][0]
        names=(raw_names.decode(errors='replace') if isinstance(raw_names,bytes) else str(raw_names)).split(';')
        if not {'inlet','outlet'}.issubset(names):
            return False, '11.9 reference expects original inlet and outlet zone names'
        field=d['results/1/phase-1/faces/SV_FLUX/1']
        if len(field)!=len(c['meshes/1/faces/c0/1']):
            return False, 'Face flux and original c0 face arrays do not align'
    return True,''


def try_reference_native(case,data,output_dir):
    """Try exact, strict 11.9 physical workflow; keep failures as evidence.

    Internal assertion failure means wrong profile/geometry, NOT zero flux.
    Both source files are always opened read-only; outputs never overwrite.
    """
    case,data=Path(case).resolve(),Path(data).resolve()
    if sys.flags.optimize:
        return {'status':'assertion_checks_disabled','detail':'The reference requires normal Python with assertions enabled (no -O / PYTHONOPTIMIZE).'}
    if not SCRIPT.is_file():
        return {'status':'reference_script_missing','detail':'11.9 native script was not packaged'}
    try:
        ok,reason=_preflight(case,data)
        if not ok:
            return {'status':'reference_profile_inapplicable','detail':reason}
    except Exception as exc:
        return {'status':'reference_preflight_failed','detail':str(exc)[:400]}
    output_dir=Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    ident=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    name=re.sub(r'[^\w.-]+','_',case.name[:-7])[:35]
    prefix=output_dir/('native_11_9_'+name+'_'+ident)
    original_argv=sys.argv
    # An unchanged source script still needs an outer guard against Fluent
    # rewriting a large saved snapshot during our independent analysis.
    def _identity(path):
        st=path.stat()
        return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    input_identity=(_identity(case),_identity(data))
    capture=io.StringIO()
    try:
        sys.argv=[str(SCRIPT),str(case),str(data),'--output',str(prefix)]
        with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
            runpy.run_path(str(SCRIPT),run_name='__main__')
        metrics=parse_native_csv(str(prefix)+'.csv')
        if input_identity!=(_identity(case),_identity(data)):
            raise RuntimeError('Case/Data changed during 11.9 native script execution; the result must be discarded')
        metrics['input_files_unchanged_during_analysis']=True
        metrics['case_file']=str(case)
        metrics['data_file']=str(data)
        metrics['verification_basis']='Source 11.9 geometry assertions + CSV independent sum/CV checks, not cross-model validation'
        metrics['reference_script_sha256']=hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        path=Path(str(prefix)+'.json')
        with path.open('x',encoding='utf-8') as f:
            json.dump(metrics,f,ensure_ascii=False,indent=2,allow_nan=False)
        # Publish a reader-friendly overview while preserving the trusted
        # reference HTML and the original user-supplied script unmodified.
        from af_report_summary import create_reference_overview
        overview = create_reference_overview(str(prefix)+'.html', metrics)
        # The GUI must open the 2.0-inspired, structured standalone report,
        # NOT the historical .summary.html. Keep both legacy files for audit.
        from af_report_v3 import render_reference_report
        upgraded_html = render_reference_report(metrics, str(prefix)+'.csv')
        report_path = Path(str(prefix)+'.analysis.html')
        with report_path.open('x',encoding='utf-8') as report_file:
            report_file.write(upgraded_html)
        return {'status':'native_11_9_profile_computed',
                'metrics':metrics,'csv_file':str(prefix)+'.csv',
                'html_file':str(report_path),'legacy_summary_html_file':overview,'detail_html_file':str(prefix)+'.html',
                'json_file':str(path),
                'source_stdout':capture.getvalue()[-500:]}
    except BaseException as exc:
        # Only discard incomplete outputs generated for this attempt, never
        # modify source Case/Data or any existing report.
        for suffix in ('.csv','.html','.summary.html','.analysis.html','.json'):
            p=Path(str(prefix)+suffix)
            if p.exists():
                p.unlink()
        return {'status':'requires_geometry_calibration',
                'profile':PROFILE,'failure_type':type(exc).__name__,
                'detail':str(exc)[:450] or capture.getvalue()[-400:],
                'note':'11.9 reference cannot safely be applied to this geometry; no physical result was inferred'}
    finally:
        sys.argv=original_argv


def compare_native_metrics(a,b):
    """Compare two true H5-derived native outputs with explicit caveats.

    The very same 11.9 measurement profile is necessary but is not enough to
    prove geometric/physical equivalence or convergence.
    """
    if a.get('schema')!='autofluent.native_flux_metrics.v1' or b.get('schema')!='autofluent.native_flux_metrics.v1':
        raise ValueError('Native comparison requires two H5-derived native metrics JSON files')
    if a.get('profile')!=b.get('profile') or a.get('profile')!=PROFILE:
        raise ValueError('Incompatible native geometry profiles')
    if not a.get('reference_script_sha256') or not b.get('reference_script_sha256'):
        raise ValueError('Missing original native algorithm SHA256 provenance')
    if a.get('reference_script_sha256')!=b.get('reference_script_sha256'):
        raise ValueError('Native source algorithms differ; do not compare without revalidation')
    layers={}
    for layer in ('upper','lower'):
        l=a['layers'][layer];r=b['layers'][layer]
        if l['channel_count']!=r['channel_count'] or len(l['channels_net_kg_s'])!=len(r['channels_net_kg_s']):
            raise ValueError('Channel index sets differ: '+layer)
        layers[layer]={
            'channel_count':l['channel_count'],
            'share_inlet_pct':[l['share_inlet_pct'],r['share_inlet_pct']],
            'share_delta_pp':r['share_inlet_pct']-l['share_inlet_pct'],
            'cv_pct':[l['cv_pct'],r['cv_pct']],
            'cv_delta_pp':r['cv_pct']-l['cv_pct'],
            'bypass_percent_of_inlet':{
                key:[100*l['partition_net_kg_s'][key]/a['inlet_net_kg_s'],
                     100*r['partition_net_kg_s'][key]/b['inlet_net_kg_s']]
                for key in ('central','outer_low_z','outer_high_z')},
        }
    return {'schema':'autofluent.native_compare.v1',
            'status':'provisional_same_calibration_unverified_case_identity',
            'models':[a.get('case_file'),b.get('case_file')],
            'profile':PROFILE,'layers':layers,
            'pressure_drop_pa_low_speed_approx':[
                a['pressure_drop_pa_low_speed_approx'],b['pressure_drop_pa_low_speed_approx']],
            'mass_imbalance_pct':[a['mass_imbalance_pct'],b['mass_imbalance_pct']],
            'requirements_before_formal_ranking':[
                'Check current geometry, wafer count and zoning equivalence',
                'Check same measurement section and face-normal convention',
                'Check solver convergence and saved-snapshot time consistency'],
            'warning':'A matching 11.9 profile is not proof of convergence or exact mesh/geometry identity'}
