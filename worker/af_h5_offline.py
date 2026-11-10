# -*- coding: utf-8 -*-
"""Read-only offline preflight for a saved Fluent *.cas.h5 / *.dat.h5 pair.

Uses HDF5 dataset metadata without materializing large field arrays. If PyFluent
is available, indexes the zone names and face/cell variable names via its
supported offline reader; optionally calculates per-zone original face flux
when that variable is present. This is NOT a wafer-gap RT0 solver. Never
silently substitute interpolated velocity for a conservative face flux.
"""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time

CAS_SUFFIX = '.cas.h5'
DAT_SUFFIX = '.dat.h5'
H5_SIGNATURE = b'\x89HDF\r\n\x1a\n'
FIELD_TOKENS = ('FLUX', 'VELOCITY', 'PRESSURE', 'SV_U', 'SV_V', 'SV_W', 'SV_P', 'SV_T', 'DENSITY')
MAX_INDEXED_DATASETS = 140000
MAX_ZONE_FLUX_FACES = 300000


def resolve_pair(selected, *, explicit_case=None, explicit_data=None):
    from af_pairing import resolve_saved_pair
    return resolve_saved_pair(selected,explicit_case=explicit_case,explicit_data=explicit_data)


def signature(path):
    with path.open('rb') as fp:
        return fp.read(8)


def stat_record(path):
    st = path.stat()
    # No full-file SHA by default: a multi-GB CFD result must not be read twice
    # just to display the summary. Hash only if explicitly requested.
    return {'path': str(path), 'bytes': st.st_size,
            'mtime_ns': st.st_mtime_ns,
            'hdf5_signature': signature(path) == H5_SIGNATURE}


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as fp:
        for chunk in iter(lambda: fp.read(4*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def h5_index(path):
    try:
        import h5py
    except ImportError:
        return {'reader': 'not_available', 'required_package': 'h5py',
                'datasets': [], 'dataset_count': None,
                'warning': 'Python 环境缺少 h5py；可改用有 PyFluent 的现有 Python 或安装一次依赖'}
    count = 0
    limited = False
    relevant = []
    top = []
    with h5py.File(str(path), 'r') as f:
        top = sorted(list(f.keys()))
        def inspect(name, obj):
            nonlocal count, limited
            if not isinstance(obj, h5py.Dataset):
                return
            count += 1
            if count > MAX_INDEXED_DATASETS:
                limited = True
                return
            upper = name.upper()
            if any(token in upper for token in FIELD_TOKENS) or upper.endswith(('/ID','/NAME','/ZONEID')):
                # Record shape/dtype only. Never read the dataset into RAM.
                relevant.append({'path': name, 'shape': list(obj.shape), 'dtype': str(obj.dtype)})
        f.visititems(inspect)
    return {'reader':'h5py', 'root_groups':top, 'datasets':relevant[:450],
            'dataset_count':count, 'index_limit_reached':limited,
            'matching_field_count':len(relevant)}


def _safe_str(x):
    if isinstance(x, bytes):
        return x.decode('utf-8', 'replace')
    return str(x)


def extract_native_boundary_flux(mesh, df, phases, fields, zones):
    """Extract real saved SV_FLUX on named port zones only, if available.

    No gap/bypass values are invented. An in/out mass-balance based only on
    zone name or sign would be unsafe, so report signed sums without labeling
    them as physical inward/outward flow until surface orientation is checked.
    """
    import numpy as np
    selected = []
    for zone in zones:
        label = zone['name'].lower().strip()
        if re.search(r'(^|[\s_\-])(inlet|outlet)([\s_\-]|$)', label):
            selected.append(zone)
    if not phases:
        return {'status':'no_phases','zones':[]}
    phase = phases[0]
    face_names = fields.get(phase, {}).get('face_variables', [])
    if 'SV_FLUX' not in face_names:
        return {'status':'no_saved_sv_flux','zones':[],
                'reason':'DataFile does not expose the original SV_FLUX face variable; no reconstructed flux was calculated'}
    if len(selected) > 12:
        return {'status':'too_many_named_ports','zones':[],
                'reason':'More than 12 port zones detected; stopped before allocating large arrays'}
    results=[]
    for zone in selected:
        sid = zone['id']
        try:
            lo, hi = mesh.get_surface_locs(sid)
            # Fluent mesh face location is usually an inclusive 1-based range.
            estimated = int(hi) - int(lo) + 1
            if estimated < 0 or estimated > MAX_ZONE_FLUX_FACES:
                results.append({'zone':zone,'status':'zone_too_large','face_count_estimate':estimated})
                continue
            values = np.asarray(df.get_face_scalar_field_data(phase, 'SV_FLUX', [sid])).reshape(-1)
            if values.size != estimated:
                results.append({'zone':zone,'status':'face_count_mismatch','expected':estimated,'actual':int(values.size)})
                continue
            if not np.all(np.isfinite(values)):
                results.append({'zone':zone,'status':'nonfinite_data'})
                continue
            positive = float(np.sum(values[values>0],dtype=np.float64))
            negative_magnitude = float(-np.sum(values[values<0],dtype=np.float64))
            item={'zone':zone,'status':'raw_face_flux_extracted',
                  'face_count':int(values.size),
                  'positive_signed_kg_s':positive,
                  'negative_signed_magnitude_kg_s':negative_magnitude,
                  'signed_sum_kg_s':float(np.sum(values,dtype=np.float64)),
                  'orientation':'unverified_fluent_face_normals'}
            # Some saved solutions expose total pressure at faces. These are
            # purely measured previews; without verified native normals,
            # direction-sensitive pressure drop remains unqualified.
            pressure_name = next((x for x in ('SV_TOTAL_PRESSURE','SV_PT','TOTAL_PRESSURE')
                                  if x in face_names),None)
            if pressure_name:
                try:
                    ps=np.asarray(df.get_face_scalar_field_data(phase,pressure_name,[sid])).reshape(-1)
                    if ps.size==values.size and np.all(np.isfinite(ps)):
                        total_weight=np.abs(values).sum(dtype=np.float64)
                        if total_weight>0:
                            item['mass_magnitude_weighted_total_pressure_pa']=float(
                                np.dot(ps,np.abs(values))/total_weight)
                            item['pressure_field_name']=pressure_name
                except Exception as exc:
                    item['pressure_unavailable_reason']=str(exc)[:140]
            results.append(item)
        except Exception as exc:
            results.append({'zone':zone,'status':'read_failed','detail':str(exc)[:220]})
    return {'status':'partial_raw_zone_flux','phase':phase,'zones':results,
            'verification':'native_saved_flux_only_if_available__no_wafer_gap_or_bypass_results'}

def pyfluent_fields(case, data, *, full_cff_face_count=None):
    try:
        try:
            from ansys.fluent.core.file_reader.case_file import CaseFile
            from ansys.fluent.core.file_reader.data_file import DataFile
        except ImportError:
            from ansys.fluent.core.offline import CaseFile, DataFile
    except (ImportError, ModuleNotFoundError):
        return {'status':'not_installed', 'detail':'没有可用的 PyFluent 离线读取包',
                'note':'H5 元数据仍可正常检查；不需要打开 Fluent/Workbench'}
    try:
        # Some PyFluent builds log reader details on stdout. Keep the CLI's
        # stdout strictly one JSON object so the native UI can parse it.
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            cf = CaseFile(case_file_name=str(case))
            df = DataFile(data_file_name=str(data), case_file_handle=cf)
        phases = [_safe_str(p) for p in df.get_phases()]
        fields = {}
        for p in phases[:8]:
            face = [_safe_str(v) for v in df.get_face_variables(p)]
            cell = [_safe_str(v) for v in df.get_cell_variables(p)]
            fields[p] = {'face_variables':face[:2000], 'cell_variables':cell[:2000],
                         'face_variable_count':len(face),'cell_variable_count':len(cell)}
        meshes = cf.get_mesh()
        ids = list(meshes.get_surface_ids())
        names = list(meshes.get_surface_names())
        zones = [{'id':int(i),'name':_safe_str(n)} for i,n in zip(ids,names)]
        native_flux = extract_native_boundary_flux(meshes, df, phases, fields, zones)
        global_flux_evidence = {'status':'not_attempted_unverified_cff_topology'}
        cell_flux_evidence = {'status':'not_attempted_without_validated_global_flux'}
        if (full_cff_face_count and phases and
                'SV_FLUX' in fields.get(phases[0],{}).get('face_variables',[])):
            # One bounded, read-only sweep of all ORIGINAL face flux values.
            # No face is assigned to a cell until global Face ID coverage passes.
            from af_native_flux_map import native_global_face_flux, check_native_cell_closure
            try:
                field_array, global_flux_evidence = native_global_face_flux(
                    meshes, df, phases[0], zones, full_cff_face_count)
                if field_array is not None:
                    cell_flux_evidence = check_native_cell_closure(case, field_array)
                del field_array
            except Exception as exc:
                # A global map failure must not discard successful partial
                # named-surface results or the H5 metadata already obtained.
                global_flux_evidence = {'status':'global_face_map_failed_gracefully',
                                        'detail':str(exc)[:320]}
        # Named *measurement* surfaces are not the wafer walls. Read genuine
        # original face flux only if a real, explicitly named cut exists.
        if phases and 'SV_FLUX' in fields.get(phases[0],{}).get('face_variables',[]):
            from af_saved_analysis import extract_named_measurement_flux
            native_measures = extract_named_measurement_flux(meshes, df, phases[0],
                                                              [(z['id'],z['name']) for z in zones])
        else:
            native_measures = {'status':'no_saved_sv_flux','zones':[]}
        return {'status':'indexed','phases':phases,'fields':fields,'zones':zones[:2000],
                'zone_count':len(zones), 'native_boundary_flux':native_flux,
                'global_native_sv_flux':global_flux_evidence,
                'native_cell_flux_balance':cell_flux_evidence,
                'native_measurements':native_measures,
                'face_flux_available':any('SV_FLUX' in f['face_variables'] for f in fields.values())}
    except Exception as exc:
        return {'status':'reader_failed', 'detail':str(exc)[:500],
                'note':'PyFluent 无法解析当前 CFF 文件；未修改或替换原始文件'}


def inspect_case_data(selected, *, use_pyfluent=True, full_hash=False):
    case, data = resolve_pair(selected)
    cr, dr = stat_record(case), stat_record(data)
    if not cr['hdf5_signature'] or not dr['hdf5_signature']:
        raise ValueError('文件扩展名为 .h5，但文件头不是 HDF5；请检查是否损坏或配对错误')
    if cr['bytes'] < 100 or dr['bytes'] < 100:
        raise ValueError('文件过小，不能用于 Fluent 场分析')
    result = {'schema':'autofluent.offline_case_data.v1',
              'status':'pair_headers_verified',
              'physical_analysis_status':'not_yet_computed',
              'case':cr,'data':dr,
              'h5_case':h5_index(case), 'h5_data':h5_index(data),
              'model_id':case.name[:-len(CAS_SUFFIX)],
              'reader_mode':'saved_files_only_no_workbench_no_fluent',
              'verification':{
                  'paired_by_same_stem':True,'hdf5_file_headers':True,
                  'internal_mesh_field_consistency':'not_verified',
                  'native_face_flux_integrals':'not_computed',
                  'wafer_gap_geometry':'not_identified',
                  'x_z_bypass_geometry':'not_identified',
                  'data_convergence':'not_verified'},
              'warnings':['配对文件与 H5 数据集结构检查成功，不代表工况、网格与保存解已物理核验。',
                          '片间流量、CV、X/Z 旁路须先取得 Fluent 原生面通量和真实分区；此步骤绝不凭文件名编造数值。',
                          '不同模型需重新识别间隙和旁路区域；历史固定坐标不可直接沿用。']}
    # CFF topology determines whether absolute face IDs can safely be used.
    from af_cff_topology import inspect_case_topology
    result['cff_topology'] = inspect_case_topology(case)
    result['verification']['mesh_topology_metadata'] = result['cff_topology'].get('status','not_available')
    if result['cff_topology'].get('status') != 'geometry_arrays_consistent':
        result['warnings'].append('CFF 网格拓扑尚未完成严格验证；原生面编号及 RT0 不能据此直接建立。')
    good_topology = result['cff_topology'].get('status') == 'geometry_arrays_consistent'
    face_count = (result['cff_topology'].get('arrays',{}).get('face_nnodes',{}).get('entries')
                  if good_topology else None)
    if use_pyfluent:
        result['pyfluent'] = pyfluent_fields(case, data, full_cff_face_count=face_count)
    if result['h5_case']['reader'] == 'h5py' and result['h5_data']['reader'] == 'h5py':
        result['status'] = 'pair_verified_structure_indexed'
        result['verification']['hdf5_metadata_indexed'] = True
    else:
        result['verification']['hdf5_metadata_indexed'] = False
        result['warnings'].append('缺少 h5py，尚未读取 HDF5 内部元数据；仅验证文件头。')
    # Native fields may support partial real measurements; index success alone
    # is *never* equivalent to a wafer/bypass solver having run.
    from af_saved_analysis import analyze_native_flux
    result['derived_metrics'] = analyze_native_flux(result.get('pyfluent',{}))
    if result['derived_metrics']['status'] == 'candidate_only_not_geometry_verified':
        result['physical_analysis_status'] = 'named_native_flux_candidates_not_geometry_verified'
    c=(result.get('pyfluent') or {}).get('native_cell_flux_balance') or {}
    if c.get('status')=='candidate_fvm_cell_balance_from_original_sv_flux':
        result['verification']['global_original_sv_flux_face_id_mapping']='complete'
        result['verification']['native_cell_flux_closure']='candidate_not_certified'
        if result['physical_analysis_status']=='not_yet_computed':
            result['physical_analysis_status']='global_face_and_cell_balance_candidate_only'
        result['warnings'].append('原生面通量已映射至全局 Face ID 并进行单元守恒自检；这仍不是晶圆片间 CV、旁路测量。')
    if full_hash:
        result['case']['sha256'] = checksum(case)
        result['data']['sha256'] = checksum(data)
    return result


def safe_save(data, directory):
    dest = Path(directory).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^\w.\-]+','_', data['model_id'])[:70] or 'model'
    out = dest / ('h5_index_%s_%s_%s.json' % (name,time.strftime('%Y%m%d_%H%M%S'),os.urandom(3).hex()))
    # Exclusively create output: never overwrite existing scans or Fluent saves.
    with out.open('x', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    return out


def main():
    p = argparse.ArgumentParser(description='Read-only Fluent CFF offline pair inspection')
    p.add_argument('--file', required=False, help='*.cas.h5 OR *.dat.h5')
    p.add_argument('--case-file',default='',help='Explicit Case override when selecting Data')
    p.add_argument('--data-file',default='',help='Explicit Data snapshot when selecting Case')
    p.add_argument('--output-dir',default='')
    p.add_argument('--skip-pyfluent',action='store_true',help='Skip optional PyFluent package only; native H5 analysis remains enabled')
    p.add_argument('--index-only',action='store_true',help='Only index data; do not compute any physical native flux metrics')
    p.add_argument('--prefer-11-9-reference',action='store_true',help='Force strict 11.9 source-script attempt when geometry was moved outside 11.9 folder')
    p.add_argument('--full-hash',action='store_true')
    p.add_argument('--config',default='',help='JSON physical geometry constraints; skips 11.9 reference path')
    p.add_argument('--check',action='store_true',help='Validate --config before opening any large H5 datasets')
    p.add_argument('--geometry-check',action='store_true',help='Case-only physical geometry precheck, no Fluent data or SV_FLUX read')
    args = p.parse_args()
    try:
        # JSON constraints are validated before opening a single HDF5 array.
        # --check works with no Case/Data file for fast operator review.
        config=None
        if args.config:
            from af_model_config import load_model_config
            config=load_model_config(args.config)
        if args.check:
            if config is None:
                raise ValueError('--check requires --config')
            if args.file:
                resolve_pair(args.file)  # filenames only; no H5 reads
            print(json.dumps({'schema':'autofluent.geometry_config_preflight.v1',
                              'status':'config_validated_before_large_h5_reads',
                              'configured_layers':len(config['layers']),
                              'configured_rows':sum(len(x['rows']) for x in config['layers']),
                              'explicit_mode':True,
                              'note':'Geometry/count/flux checks require actual Case/Data analysis'},
                             ensure_ascii=False))
            return
        if args.geometry_check:
            if not args.file:
                raise ValueError('--geometry-check requires --file with a Fluent Case or Data')
            if args.file.lower().endswith(CAS_SUFFIX):
                actual_case=Path(args.file).expanduser().resolve()
                if not actual_case.is_file():raise FileNotFoundError(str(actual_case))
            else:
                actual_case,_=resolve_pair(args.file,explicit_case=args.case_file or None)
            from af_geometry_precheck import precheck_case_geometry,write_precheck_report
            budget=((config or {}).get('resources') or {}).get('memory_budget_mb',3072)
            report=precheck_case_geometry(actual_case,config=config,memory_budget_mb=budget)
            if args.output_dir:report['artifacts']=write_precheck_report(report,args.output_dir)
            print(json.dumps(report,ensure_ascii=False))
            return
        if not args.file:
            raise ValueError('--file is required except for --check')
        from af_progress import update as progress
        progress(2, '配对保存解', '检查 Case/Data 路径和配置，不修改输入')
        result = None
        native_attempt = None
        input_case, input_data = resolve_pair(args.file,explicit_case=args.case_file or None,explicit_data=args.data_file or None)
        reference_hint = config is None and args.prefer_11_9_reference
        if args.output_dir and not args.index_only and reference_hint:
            # Fast path: execute the real, user-supplied 11.9 native CFF
            # analyzer unchanged. This uses only h5py/numpy, no live Solver
            # and no PyFluent gRPC. All geometric assumptions remain strict.
            from af_native_profile import try_reference_native
            case, data = input_case, input_data
            progress(8, '11.9 参考引擎', '按已验证几何方法读取原生面通量；当前阶段无法精确预测耗时')
            native_attempt = try_reference_native(case,data,str(Path(args.output_dir).parent/'native_reports'))
            progress(78, '11.9 统计返回', '开始审核原始通道与输出的完整性')
            if native_attempt.get('status') == 'native_11_9_profile_computed':
                record = native_attempt['metrics']
                result = {
                    'schema':'autofluent.offline_case_data.v1',
                    'status':'pair_verified_structure_indexed',
                    'physical_analysis_status':'native_11_9_profile_computed',
                    'model_id':case.name[:-len(CAS_SUFFIX)],
                    'case':stat_record(case),'data':stat_record(data),
                    'reader_mode':'saved_h5_native_face_flux_no_workbench_or_fluent',
                    'pyfluent':{'status':'not_required_for_h5py_native'},
                    'native_direct':native_attempt,
                    'derived_metrics':{
                        'status':'native_11_9_profile_computed',
                        'layers':{
                            layer:{'status':'native_staircase_verified_for_11_9_profile',
                                   'cv_pct':values['cv_pct'],
                                   'channel_count':values['channel_count'],
                                   'net_kg_s':values['net_kg_s'],
                                   'share_inlet_pct':values['share_inlet_pct']}
                            for layer,values in record['layers'].items()},
                        'mass_imbalance_pct':record['mass_imbalance_pct'],
                        'pressure_drop_pa_low_speed_approx':record['pressure_drop_pa_low_speed_approx'],
                    },
                    'verification':{
                        'native_face_flux_and_c0_c1':'11.9 reference script assertions passed',
                        'wafer_gap_geometry':'11.9 profile passed 376-channel partition checks',
                        'x_z_bypass_geometry':'11.9 profile passed partition-closure checks',
                        'data_convergence':'not_verified_from_saved_snapshot',
                        'generality':'not_certified_for_other_geometry'},
                    'warnings':['仅适用于通过 11.9 几何条件审核的保存解，不意味着已自动识别任意新模型。',
                                '原生网格阶梯截面按面心唯一归属；上、下层串联，流量不得相加。',
                                '单凭 Case/Data 不能证明收敛，需要日志及监控历史。'],
                    'analysis_json':native_attempt['json_file'],
                    'html_file':native_attempt['html_file'],
                    'native_csv':native_attempt['csv_file'],
                }
        # Generalized native staircase path: only after the unchanged 11.9
        # reference cannot certify this geometry. No Codex report is input.
        adaptive_attempt=None
        if result is None and args.output_dir and not args.index_only:
            try:
                from af_adaptive_native import try_adaptive_native
                case, data = input_case, input_data
                progress(8, '通用 H5 引擎', '识别数据组、网格拓扑与物理截面')
                adaptive_attempt=try_adaptive_native(case,data,
                    str(Path(args.output_dir).parent/'native_reports'),config=config)
                if (adaptive_attempt.get('status')=='adaptive_native_candidate_computed' or
                    (adaptive_attempt.get('status')=='computed_export_failed' and
                     adaptive_attempt.get('computed_status')=='adaptive_native_candidate_computed')):
                    record=adaptive_attempt['metrics']
                    layers=record['layers']
                    result={
                        'schema':'autofluent.offline_case_data.v1',
                        'status':'pair_verified_structure_indexed',
                        'physical_analysis_status':'adaptive_native_candidate_computed',
                        'model_id':case.name[:-len(CAS_SUFFIX)],
                        'case':stat_record(case),'data':stat_record(data),
                        'reader_mode':'saved_h5_native_face_flux_geometry_autodetected',
                        'pyfluent':{'status':'not_required_for_h5py_native'},
                        'native_direct':adaptive_attempt,
                        'derived_metrics':{'status':'adaptive_native_candidate_computed',
                            'layers':{key:{'status':'geometry_auto_inferred_needs_independent_review',
                                           'cv_pct':v['cv_pct'],'channel_count':v['channel_count'],
                                           'net_kg_s':v['net_kg_s'],
                                           'share_inlet_pct':v['share_inlet_pct']}
                                      for key,v in layers.items()},
                            'mass_imbalance_pct':record['port_flux']['mass_imbalance_pct'],
                            'pressure_drop_pa_low_speed_approx':record['pressure'].get('pressure_drop_pa')},
                        'verification':{'native_face_flux_and_c0_c1':'candidate mapping validated',
                            'wafer_gap_geometry':'auto inferred; independent verification required',
                            'x_z_bypass_geometry':'4 exclusive partitions close per cut',
                            'data_convergence':'not_verified_from_saved_snapshot',
                            'generality':'single-block tetra / axis-aligned ALD only'},
                        'warnings':['本次直接从 H5 计算，但几何区域是自动推断的候选结果；不同几何须核查识别轮廓。',
                            '阶梯截面按面心划分，正反向分别统计；串联上下层不可相加。',
                            '质量控制体代数闭合不等于求解收敛。'],
                        'analysis_json':adaptive_attempt['json_file'],
                        'html_file':adaptive_attempt.get('html_file'),
                    'report_save_status':adaptive_attempt.get('report_save_status','completed'),
                    'export_error':adaptive_attempt.get('export_error'),
                        'native_csv':adaptive_attempt['csv_file'],
                    }
                elif (adaptive_attempt.get('status')=='partial_physical_metrics' or
                      (adaptive_attempt.get('status')=='computed_export_failed' and
                       adaptive_attempt.get('computed_status')=='partial_physical_metrics')):
                    partial=adaptive_attempt['metrics']
                    port=partial['port_flux']
                    pressure=partial['pressure']
                    result={
                        'schema':'autofluent.offline_case_data.v1',
                        'status':'partial_physical_metrics',
                        'physical_analysis_status':'partial_physical_metrics',
                        'model_id':case.name[:-len(CAS_SUFFIX)],
                        'case':stat_record(case),'data':stat_record(data),
                        'reader_mode':'read_only_native_port_subset',
                        'module_results':partial['modules'],
                        'first_full_analysis_blocker':partial['first_full_analysis_blocker'],
                        'partial_native':partial,
                        'adaptive_native_attempt':{'status':'partial_physical_metrics',
                            'detail':adaptive_attempt.get('detail','')},
                        'pyfluent':{'status':'not_required_for_native_port_subset'},
                        'derived_metrics':{'status':'partial_physical_metrics',
                            'port_flux':port,
                            'mass_imbalance_pct':port.get('mass_imbalance_pct'),
                            'pressure_drop_pa_low_speed_approx':pressure.get('pressure_drop_pa')},
                        'verification':{'native_face_flux':'full indexed Face ID coverage',
                            'global_port_mass_balance':port.get('external_audit',port.get('external_face_audit')),
                            'wafer_gap_geometry':'blocked_not_computed',
                            'data_convergence':'not_verified'},
                        'analysis_json':adaptive_attempt.get('json_file'),
                        'html_file':adaptive_attempt.get('html_file'),
                        'report_save_status':adaptive_attempt.get('report_save_status','completed'),
                        'export_error':adaptive_attempt.get('export_error'),
                        'native_csv':adaptive_attempt.get('csv_file'),
                        'warnings':['端口、可用总压降和已核验挡板可独立交付；晶圆配对未完成，不发布片间 CV 或旁路。',
                                    '质量守恒不等于求解收敛，需独立核验。']}
            except Exception as exc:
                adaptive_attempt={'status':'adaptive_engine_error',
                                  'detail':str(exc)[:400],'no_physical_result':True}
        if result is None and config is not None:
            # Explicit configuration never downgrades into an automatic or
            # 11.9-specific physical report after a failed configured run.
            result={'schema':'autofluent.offline_case_data.v1',
                    'status':'configured_analysis_requires_review',
                    'physical_analysis_status':'not_computed',
                    'model_id':input_case.name[:-len(CAS_SUFFIX)],
                    'case':stat_record(input_case),'data':stat_record(input_data),
                    'config_mode':'explicit_only',
                    'configured_analysis':adaptive_attempt or {
                        'status':'not_run','detail':'Specify --output-dir for physical analysis'},
                    'verification':{'geometry':'not_verified',
                                    'data_convergence':'not_verified',
                                    'no_automatic_fallback':True},
                    'warnings':['显式配置模式未完成真实几何/通量核验；不回退历史 11.9 或自动识别路径。']}
        if result is None:
            result = inspect_case_data(input_data, use_pyfluent=not args.skip_pyfluent, full_hash=args.full_hash)
            if native_attempt is not None:
                result['native_reference_attempt'] = native_attempt
            if adaptive_attempt is not None:
                result['adaptive_native_attempt'] = adaptive_attempt
                if adaptive_attempt.get('status')!='adaptive_native_candidate_computed':
                    result.setdefault('warnings',[]).append('自适应原生分区未通过：'+str(adaptive_attempt.get('detail','未取得有效面通量或几何'))[:250])
                if native_attempt is not None and native_attempt.get('status')=='requires_geometry_calibration':
                    result.setdefault('warnings',[]).append('参考 11.9 的晶圆/旁路识别规则不适用于此几何，需要按当前壁面重新标定；拒绝伪造通道 CV。')
        if config is not None:
            result['config_mode']='explicit_only'
            result['config_file']=str(Path(args.config).expanduser().resolve())
        # Pairing, geometry and export are distinct facts. A blocked geometry
        # result must not imply a corrupt Case/Data pair. Preserve status in
        # the indexed JSON and make it available to native UI/logs.
        physical_state=result.get('physical_analysis_status','not_computed')
        wafer_stage=((result.get('module_results') or {}).get('wafer_geometry') or {})
        blocker=result.get('first_full_analysis_blocker') or {}
        export_state=result.get('report_save_status','not_executed')
        result['stage_statuses']={
            'file_pairing':{'status':'completed','case':str(input_case),'data':str(input_data),
                            'note':'paired file paths resolved; shared mesh identity still depends on native index checks'},
            'wafer_geometry':{'status':wafer_stage.get('status',
                'completed' if physical_state in ('native_11_9_profile_computed','adaptive_native_candidate_computed')
                else 'not_verified'),
                'stage':blocker.get('stage'), 'reason':wafer_stage.get('reason',blocker.get('reason',''))},
            'report_publication':{'status':'failed' if export_state=='failed' else
                 'completed' if result.get('html_file') else 'not_executed',
                 'reason':((result.get('export_error') or {}).get('reason',''))}}
        if args.output_dir and result.get('physical_analysis_status') in ('native_11_9_profile_computed','adaptive_native_candidate_computed'):
            result['index_file'] = str(safe_save(result,args.output_dir))
        elif args.output_dir:
            # On geometry mismatch, provide bounded *candidate* wall-plane
            # positions to help calibrate the next model, not fake a CV.
            if config is None and result.get('physical_analysis_status')!='partial_physical_metrics' and ((native_attempt is not None and native_attempt.get('status') in (
                    'reference_profile_inapplicable','requires_geometry_calibration')) or
                    (adaptive_attempt is not None and adaptive_attempt.get('status')!='adaptive_native_candidate_computed')):
                try:
                    from af_geometry_scout import save_geometry_candidates
                    case_for_scout,_ = input_case,input_data
                    candidate_path,candidates=save_geometry_candidates(
                        case_for_scout,str(Path(args.output_dir).parent/'geometry_candidates'))
                    result['geometry_candidate_file']=candidate_path
                    result['geometry_candidate_status']=candidates.get('status')
                    result.setdefault('warnings',[]).append(
                        '晶圆面站点候选仅供几何重新标定；未自动认证通道/旁路。')
                except Exception as exc:
                    result.setdefault('warnings',[]).append('壁面候选扫描未完成: '+str(exc)[:180])
            result['index_file'] = str(safe_save(result,args.output_dir))
            # Tiny local schema probe helps build a real face-ID/SV_FLUX adapter
            # without needing to transfer multi-GB Case/Data files.
            if result.get('verification',{}).get('hdf5_metadata_indexed'):
                try:
                    from af_h5_schema import save_pair_schema
                    result['schema_probe_file'] = save_pair_schema(result['case']['path'],result['data']['path'],args.output_dir)
                except Exception as exc:
                    result.setdefault('warnings',[]).append('小型 H5 结构诊断未完成：'+str(exc)[:140])
            if result.get('physical_analysis_status') not in ('native_11_9_profile_computed','adaptive_native_candidate_computed','partial_physical_metrics'):
                from af_saved_analysis import write_saved_report
                result.update(write_saved_report(result, str(Path(args.output_dir).parent/'saved_reports')))
        from af_progress import update as progress
        if result.get('physical_analysis_status') in ('native_11_9_profile_computed','adaptive_native_candidate_computed'):
            progress(100, '原生结果与新版报告已输出', '统计任务完成；几何、收敛与对比可信度仍须核验',force=True)
        elif result.get('physical_analysis_status')=='partial_physical_metrics':
            progress(100, '部分物理结果已发布', '端口与可用压降已经计算；晶圆/挡板仍有阻塞，报告明确区分',force=True)
        else:
            progress(100, '仅完成保存解结构诊断', '物理指标未算出；请查看报告的失败原因与 H5 结构样本',force=True)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        from af_progress import fail
        fail('任务未完成', str(exc)[:120])
        print(json.dumps({'schema':'autofluent.offline_case_data.v1' ,'status':'error',
                          'error':str(exc),'physical_analysis_status':'not_computed'},ensure_ascii=False))
        sys.exit(1)

if __name__ == '__main__':
    main()
