# -*- coding: utf-8 -*-
"""Compare genuine saved-H5 native face results, without false qualification.

All outputs remain provisional until geometry, boundary settings, sign
conventions and convergence have been independently verified.
"""
import json
import math


def _unpack(data):
    if data.get('schema')=='autofluent.offline_case_data.v1':
        inner=data.get('native_direct',{}).get('metrics')
        if not inner:
            raise ValueError('Offline saved-file index contains no native physical result')
        return _unpack(inner)
    schema=data.get('schema')
    if schema=='autofluent.adaptive_native.v1':
        layers=data['layers']
        ports=data['port_flux']
        return {'method':'adaptive_native_cff_v1',
                'case':data.get('provenance',{}).get('case_path'),
                'inlet':ports['inlet_kg_s'], 'outlet':ports['outlet_kg_s'],
                'imbalance_pct':ports['mass_imbalance_pct'],
                'pressure_drop_pa':data.get('pressure',{}).get('pressure_drop_pa'),
                'geometry':data.get('geometry'),
                'port_zones':ports.get('boundary_zones',[]),
                'pressure_definition':data.get('pressure',{}).get('definition'),
                'pressure_status':data.get('pressure',{}).get('status'),
                'baffle':data.get('baffle',{}),
                'layers':{k:{'channels':v['channel_count'],'cv_pct':v['cv_pct'],
                             'share_pct':v['share_inlet_pct'],
                             'bypass':v['partition_net_kg_s'],
                             'directional':v.get('partition_directional_kg_s',{})}
                          for k,v in layers.items()}}
    if schema=='autofluent.native_flux_metrics.v1':
        return {'method':'strict_11_9_reference',
                'case':data.get('case_file'),
                'inlet':data['inlet_net_kg_s'], 'outlet':data['outlet_net_kg_s'],
                'imbalance_pct':data['mass_imbalance_pct'],
                'pressure_drop_pa':data.get('pressure_drop_pa_low_speed_approx'),
                'geometry':None,
                'port_zones':[], 'pressure_definition':'11.9 reference low-speed proxy',
                'pressure_status':'legacy_reference', 'baffle':{},
                'layers':{k:{'channels':v['channel_count'],'cv_pct':v['cv_pct'],
                             'share_pct':v['share_inlet_pct'],
                             'bypass':v['partition_net_kg_s'],
                             'directional':v.get('partition_directional_kg_s',{})}
                          for k,v in data['layers'].items()}}
    raise ValueError('Unsupported physical result schema: '+str(schema))


def _layout_signature(geo):
    if not geo:return None
    coords=[]
    for layer in geo.get('layers',[]):
        rows=[]
        for row in layer['rows']:
            rows.append({'lo':[round(x,7) for x in row['face_positions_lo_m']],
                         'hi':[round(x,7) for x in row['face_positions_hi_m']],
                         'z':[round(row['z_min_m'],7),round(row['z_max_m'],7)]})
        coords.append({'rows':rows,'ycut':round(layer['y_cut_m'],7),
                       'overlap_split_z_m':round(layer['overlap_split_z_m'],7) if layer.get('overlap_split_z_m') is not None else None,
                       'central_bypass':layer.get('central_bypass','auto')})
    return json.dumps(coords,sort_keys=True,separators=(',',':'))


def compare_native_saved(a,b):
    x,y=_unpack(a),_unpack(b)
    if x['inlet']<=0 or y['inlet']<=0:raise ValueError('Invalid inlet mass flux in source data')
    reasons=['两份 H5 的工况模板、真实求解收敛与测量截面法向尚未独立统一核验']
    # Equal CV alone cannot establish an improvement if gas throughput or
    # the set of measured layers changed. Compare these conditions explicitly
    # and keep the numerical deltas as provisional observations only.
    inlet_relative_delta=abs(y['inlet']-x['inlet'])/max(x['inlet'],y['inlet'])
    if inlet_relative_delta>0.001:
        reasons.append('入口总净流量差异超过 0.1%，不能直接将流量变化归因于腔体几何')
    if any(v>1.0 for v in (x['imbalance_pct'],y['imbalance_pct'])):
        reasons.append('至少一份保存解入口/出口不平衡超过 1%，不得直接解读 CV/旁路优劣')
    if x['case'] and y['case'] and x['case']==y['case']:
        reasons.append('两份记录的 Case 路径相同：可能是相同模型不同快照，不可误报为不同几何')
    if x['method']!=y['method']:
        reasons.append('原生数据采用不同几何识别方案；指标仅供方向性对照')
    ga,gb=_layout_signature(x['geometry']),_layout_signature(y['geometry'])
    if ga and gb and ga!=gb:
        reasons.append('检测到晶圆阵列或切面位置变化；不能视作相同空间测量截面')
    # Percentage-point *changes* are meaningful only for a shared physical
    # partition. Two separately measured percentages remain displayable in a
    # provisional table, but may not be promoted into a numerical improvement.
    common_cut = (bool(ga) and bool(gb) and ga==gb and x['method']==y['method']
                  and inlet_relative_delta<=0.001
                  and x['imbalance_pct']<=1.0 and y['imbalance_pct']<=1.0
                  and set(x['layers'])==set(y['layers']))
    if not common_cut:
        reasons.append('测量区域、通道布局或基础条件不具备严格同口径证据，差值字段保持未取得')
    layers={}
    shared=set(x['layers'])&set(y['layers'])
    if set(x['layers'])!=set(y['layers']):
        reasons.append('两份模型的层级集合不同；缺失层不参与比较，也不能以现有层代表完整模型')
    if not shared:reasons.append('未找到共同层级，不能对比片间指标')
    for key in sorted(shared):
        p=x['layers'][key];q=y['layers'][key]
        if p['channels']!=q['channels']:
            reasons.append(key+' 通道数量不同，只进行聚合指标比较，禁止逐通道对应')
        comparable = common_cut and p['channels']==q['channels']
        row={'channel_count':[p['channels'],q['channels']],
             'channel_cv_pct':[p['cv_pct'],q['cv_pct']],
             'cv_delta_pp':q['cv_pct']-p['cv_pct'] if comparable else None,
             'mass_fraction_pct':[p['share_pct'],q['share_pct']],
             'fraction_delta_pp':q['share_pct']-p['share_pct'] if comparable else None,
             'delta_scope':'unverified_same_cut_candidate_only' if comparable else 'not_comparable',
             'bypass_fraction_pct':{},
             'bypass_positive_fraction_pct':{},
             'bypass_reverse_fraction_pct':{}}
        for name in sorted(set(p['bypass'])&set(q['bypass'])-{'wafer'}):
            row['bypass_fraction_pct'][name]=[100*p['bypass'][name]/x['inlet'],
                                              100*q['bypass'][name]/y['inlet']]
        for name in sorted((set(p.get('directional',{})) & set(q.get('directional',{})))-{'wafer'}):
            a_dir=p['directional'][name];b_dir=q['directional'][name]
            row['bypass_positive_fraction_pct'][name]=[
                100*a_dir['positive_kg_s']/x['inlet'],100*b_dir['positive_kg_s']/y['inlet']]
            row['bypass_reverse_fraction_pct'][name]=[
                100*a_dir['negative_magnitude_kg_s']/x['inlet'],
                100*b_dir['negative_magnitude_kg_s']/y['inlet']]
        layers[key]=row
    pressure=[x['pressure_drop_pa'],y['pressure_drop_pa']]
    # Pressure ports may remain comparable even when wafer cuts differ.
    # Names/kinds/weighting and mass flow are independent from wafer position.
    def port_signature(model):
        zone_list=model.get('port_zones',[])
        if not zone_list:return None
        return sorted((z.get('zone','').casefold(),z.get('kind')) for z in zone_list)
    psig_a=port_signature(x);psig_b=port_signature(y)
    port_pressure_candidate=(psig_a is not None and psig_a==psig_b and
        x.get('pressure_definition') is not None and
        x.get('pressure_definition')==y.get('pressure_definition') and
        x.get('pressure_status','').startswith('computed_') and
        y.get('pressure_status','').startswith('computed_') and
        all(v is not None for v in pressure) and
        inlet_relative_delta<=.001 and x['imbalance_pct']<=1. and y['imbalance_pct']<=1.)
    if port_pressure_candidate and not common_cut:
        reasons.append('压降仅按同一端口和加权口径形成待核对数值差；晶圆截面不一致不影响这项端口比较')
    elif not port_pressure_candidate:
        reasons.append('端口名/压力定义/入口质量流量/守恒证据不足，压降差值不可比较')
    def baffle_signature(model):
        b=model.get('baffle') or {}
        if b.get('status')!='geometry_candidate_holes_native_flux_computed':return None
        return sorted((round(h['center_m'][0],7),round(h['center_m'][2],7)) for h in b.get('holes',[]))
    bsig_a=baffle_signature(x);bsig_b=baffle_signature(y)
    ba=x.get('baffle') or {};bb=y.get('baffle') or {}
    baffle_compare={'hole_counts':[ba.get('hole_count'),bb.get('hole_count')],
                    'all_hole_cv_pct':[ba.get('all_hole_cv_pct'),bb.get('all_hole_cv_pct')],
                    'position_match':bsig_a is not None and bsig_a==bsig_b,
                    'hole_cv_delta_pp':None,
                    'hole_net_delta_by_position_kg_s':None}
    if baffle_compare['position_match']:
        if ba.get('all_hole_cv_pct') is not None and bb.get('all_hole_cv_pct') is not None:
            baffle_compare['hole_cv_delta_pp']=bb['all_hole_cv_pct']-ba['all_hole_cv_pct']
        qa=sorted(ba['holes'],key=lambda h:(round(h['center_m'][0],7),round(h['center_m'][2],7)))
        qb=sorted(bb['holes'],key=lambda h:(round(h['center_m'][0],7),round(h['center_m'][2],7)))
        baffle_compare['hole_net_delta_by_position_kg_s']=[v['net_kg_s']-u['net_kg_s'] for u,v in zip(qa,qb)]
    return {'schema':'autofluent.native_saved_compare.v1',
            'status':'provisional_only_not_qualified',
            'input_models':[x['case'],y['case']],
            'methods':[x['method'],y['method']],
            'layers':layers,
            'global_mass_imbalance_pct':[x['imbalance_pct'],y['imbalance_pct']],
            'inlet_flow_kg_s':[x['inlet'],y['inlet']],
            'inlet_flow_relative_difference_pct':100*inlet_relative_delta,
            'pressure_drop_pa':pressure,
            'pressure_drop_delta_pa':pressure[1]-pressure[0] if port_pressure_candidate else None,
            'pressure_comparison_scope':'provisional_port_only' if port_pressure_candidate else 'not_comparable',
            'port_signatures_equal':psig_a==psig_b if psig_a and psig_b else None,
            'baffle_comparison':baffle_compare,
            'geometry_signature_equal':ga==gb if ga and gb else None,
            'same_cut_candidate':common_cut,
            'reasons':list(dict.fromkeys(reasons)),
            'warning':'Candidate cross-model comparison only, never an automatic performance ranking'}
