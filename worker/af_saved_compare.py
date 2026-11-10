# -*- coding: utf-8 -*-
"""Read-only provisional comparison of native H5-derived AutoFluent JSON records.

A matching list of named channels is not proof of geometry equivalence. Never
promote this to a qualified physical comparison without independent validation.
"""

def compare_saved_results(a,b):
    expected='autofluent.offline_case_data.v1'
    if a.get('schema')!=expected or b.get('schema')!=expected:
        raise ValueError('Both inputs must be AutoFluent saved-case analysis JSON')
    x=a.get('derived_metrics') or {}; y=b.get('derived_metrics') or {}
    reasons=[]; layers={}
    if a.get('model_id')==b.get('model_id'):
        reasons.append('两个保存解模型标识相同；仍需检查是否是不同时间的真实结果')
    if x.get('status')!='candidate_only_not_geometry_verified' or y.get('status')!='candidate_only_not_geometry_verified':
        reasons.append('至少一个模型未读到完整的已命名原生测量面通量')
    for layer in ('upper','lower'):
        p=x.get('layers',{}).get(layer,{})
        q=y.get('layers',{}).get(layer,{})
        if p.get('status') != 'candidate_native_flux_cv_orientation_unverified' or q.get('status') != 'candidate_native_flux_cv_orientation_unverified':
            reasons.append('%s 缺少可比的两个以上原生通道'%layer)
            continue
        if p.get('channel_indices') != q.get('channel_indices'):
            reasons.append('%s 通道编号不一致，拒绝计算 CV 差值'%layer)
            continue
        layers[layer]={'channel_count':p['channel_count'],
                       'cv_pct':[p['cv_pct'],q['cv_pct']],
                       'cv_delta_pp':q['cv_pct']-p['cv_pct'],
                       'scope':'原生已命名面通量 CV 候选值；真实切面/法向尚未通过核验'}
    return {'schema':'autofluent.saved_compare.v1',
            'status':'not_qualified' if reasons else 'provisional_unverified_geometry',
            'models':[a.get('model_id'),b.get('model_id')],
            'layers':layers,'reasons':reasons,
            'not_compared':['X/Z 旁路物理占比','完整压降','通道物理截面守恒','收敛资格'],
            'warning':'不得据此进行正式性能排名；几何身份、测量截面以及法向均未独立验收'}
