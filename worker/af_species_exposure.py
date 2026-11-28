"""Read-only species coverage; snapshot concentration is never a saturation dose."""


import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import html

import re

import numpy as np





def species_names(settings):

    domain=re.search(r'\(domains\s+.*?\(material\s+\.\s+([^\s()]+)\)',settings,re.S)

    if not domain:raise ValueError('未确认流体域混合物名称')

    name=domain.group(1)

    match=re.search(r'\('+re.escape(name)+r'\s+mixture\b',settings)

    if not match:raise ValueError('未找到当前流体域的混合物定义')

    depth=0;quoted=False;escaped=False;end=None

    for i in range(match.start(),len(settings)):

        ch=settings[i]

        if escaped:escaped=False;continue

        if ch=='\\' and quoted:escaped=True;continue

        if ch=='"':quoted=not quoted

        if quoted:continue

        if ch=='(':depth+=1

        elif ch==')':

            depth-=1

            if depth==0:end=i+1;break

    m=re.search(r'\(species\s+\(names\s+\(([^()]*)\)',settings[match.start():end])

    if not m:raise ValueError('当前混合物缺少明确物种顺序')

    names=re.findall(r'"([^"]+)"|([^\s()]+)',m.group(1))

    names=[a or b for a,b in names]

    if len(names)<2 or len(set(names))!=len(names):raise ValueError('物种列表不完整或重复')

    return names





def mass_fractions(values,n):

    a=np.asarray(values,dtype=float)

    if a.ndim==1:a=a[:,None]

    if a.ndim!=2 or a.shape[1] not in (n,n-1):raise ValueError('SV_Y 列数与混合物物种数量不符')

    if not np.isfinite(a).all() or a.min() < -1e-8 or a.max()>1+1e-8:raise ValueError('SV_Y 含非法质量分数')

    if a.shape[1]==n-1:a=np.column_stack((a,1-a.sum(axis=1)))

    if a.min() < -1e-8 or np.max(abs(a.sum(axis=1)-1))>1e-6:raise ValueError('物种质量分数不闭合')

    return np.clip(a,0,1)





def statistics(values,weights):

    if not len(values):return None

    total=float(weights.sum())

    if total<=0:raise ValueError('无有效体积权重')

    order=np.argsort(values);v=values[order];w=np.cumsum(weights[order])/total

    return {'cells':int(len(values)),'volume_m3':total,'mean':float(np.dot(values,weights)/total),

            'min':float(v[0]),'p05':float(np.interp(.05,w,v)),'max':float(v[-1])}





def read_field(group,key,n,indexed):

    if key not in group:raise ValueError('保存解缺少 '+key)

    blocks=indexed(group[key],total=n,context='species/'+key)

    return np.concatenate([ds[()] for _,_,ds in blocks],axis=0)





def face_fractions(mesh,ids,n,indexed):

    blocks=indexed(mesh['fields']['SV_Y'],total=mesh['nf'],allow_partial=True,context='face species')

    values=np.full((len(ids),n),np.nan)

    for lo,hi,ds in blocks:

        mask=(ids>=lo)&(ids<hi)

        if mask.any():

            indices=ids[mask]-lo;first=int(indices.min());last=int(indices.max())+1

            values[mask]=mass_fractions(ds[first:last][indices-first],n)

    if not np.isfinite(values).all():raise ValueError('选定表面的保存物种场未完整覆盖')

    return values





def wafer_surfaces(mesh,row,split=None,ri=1):

    """Actual X-normal inner wafer faces; never substitute a box surface."""

    lo=np.asarray(row['face_positions_lo_m']);hi=np.asarray(row['face_positions_hi_m'])

    gaps=np.asarray(row['regular_gap_index'],dtype=int)

    planes=np.sort(np.r_[hi[gaps],lo[gaps+1]])

    if not len(planes):return np.array([],dtype=int),np.array([]),np.empty((0,3))

    ids=[];weights=[];positions=[]

    for start in range(0,len(mesh['wall_ids']),131072):

        wall=mesh['wall_ids'][start:start+131072]

        v=mesh['xyz'][mesh['conn'][wall]];center=v.mean(axis=1)

        ix=np.searchsorted(planes,center[:,0]);a=np.clip(ix-1,0,len(planes)-1);b=np.clip(ix,0,len(planes)-1)

        distance=np.minimum(abs(center[:,0]-planes[a]),abs(center[:,0]-planes[b]))

        mask=(np.ptp(v[:,:,0],axis=1)<1e-7)&(distance<1e-6)&(center[:,1]>=row['y_min_m']-1e-7)&(center[:,1]<=row['y_max_m']+1e-7)&(center[:,2]>=row['z_min_m']-1e-7)&(center[:,2]<=row['z_max_m']+1e-7)

        if split is not None:mask&=(center[:,2]<split) if ri==1 else (center[:,2]>=split)

        if mask.any():

            ids.append(wall[mask]);positions.append(center[mask]);weights.append(np.linalg.norm(np.cross(v[mask,1]-v[mask,0],v[mask,2]-v[mask,0]),axis=1)/2)

    return (np.concatenate(ids),np.concatenate(weights),np.concatenate(positions)) if ids else (np.array([],dtype=int),np.array([]),np.empty((0,3)))





def area_statistics(values,areas):

    result=statistics(values,areas)

    if result is not None:

        result['area_m2']=result.pop('volume_m3');result['faces']=result.pop('cells')

    return result





def cell_volumes(mesh,centers):

    volume=np.zeros(mesh['nc']+1)

    owner=mesh['owner'];inside=mesh['inside'];neighbor=mesh['neighbors']

    for lo in range(0,mesh['nf'],131072):

        hi=min(lo+131072,mesh['nf'])

        v=mesh['xyz'][mesh['conn'][lo:hi]]

        fc=v.mean(axis=1);normal=np.cross(v[:,1]-v[:,0],v[:,2]-v[:,0])/2

        q=abs(np.einsum('ij,ij->i',normal,fc-centers[owner[lo:hi]]))/3

        np.add.at(volume,owner[lo:hi],q)

        a=np.searchsorted(inside,lo);b=np.searchsorted(inside,hi)

        ids=inside[a:b]

        q=abs(np.einsum('ij,ij->i',normal[ids-lo],fc[ids-lo]-centers[neighbor[a:b]]))/3

        np.add.at(volume,neighbor[a:b],q)

    if not np.isfinite(volume[1:]).all() or np.any(volume[1:]<=0):raise ValueError('四面体体积验证失败')

    return volume[1:]





def analyze_species(cf,df,mesh,centers,geometry,layers,inlet_ids=None):

    from af_adaptive_native import _indexed_datasets

    raw=cf['settings/Rampant Variables'][0]

    settings=raw.decode('utf-8') if isinstance(raw,bytes) else str(raw)

    names=species_names(settings)

    estimated=mesh.get('projected_peak_memory_mb_not_measured',0)+mesh['nc']*(len(names)*24+128)/1024**2

    if estimated>mesh.get('memory_budget_mb',6144):

        raise ValueError('组分分析内存预检超出当前预算，保留流量结果；未分配大组分矩阵')

    cells=df['results/'+str(mesh['result_group'])+'/'+str(mesh['phase_group'])+'/cells']

    fractions=mass_fractions(read_field(cells,'SV_Y',mesh['nc'],_indexed_datasets),len(names))

    variables=df['settings/Data Variables'][0].decode('utf-8')

    time=re.search(r'\(flow-time\s+([^()\s]+)',variables)

    reaction=re.search(r'\(rp-react\?\s+(#[tf])',settings)

    result={'status':'identified','species':names,'flow_time_s':float(time.group(1)) if time else None,

            'reaction_enabled':reaction.group(1)=='#t' if reaction else None,

            'saturation_status':'无法判定：单时刻质量分数不是暴露剂量；需局部分压时间积分与本工况饱和判据或表面覆盖率。',

            'dose_pa_s':None,'snapshot_count':1,'time_history_available':False,'species_revision':2,

            'source_case':str(cf.filename),'source_data':str(df.filename),'regions':[],'channels':[],

            'weighting':'四面体体积加权；单元中心位于识别出的规则片间隙及各排Y/Z边界框内。边界框不是精确圆形晶圆投影。',

            'spatial_axes':'沿Y分上下游层、沿Z分排；坐标与原AF几何模块一致，不按截图推断上下。'}

    volumes=cell_volumes(mesh,centers)
    stats={}
    total_volume=float(volumes.sum())
    for i,name in enumerate(names):
        if name not in ('alme3','h2o'):
            continue
        field=fractions[:,i]
        peak=int(np.argmax(field))
        stats[name]={'volume_mean':float(np.dot(volumes,field)/total_volume),
                     'max':float(field[peak]),'peak_xyz_m':centers[peak+1].tolist()}
    result['gas_inventory_stats']=stats

    try:

        density=read_field(cells,'SV_DENSITY',mesh['nc'],_indexed_datasets).reshape(-1)

        if density.shape!=volumes.shape or not np.isfinite(density).all() or np.any(density<=0):

            raise ValueError('密度字段无效')

        gas_mass=density*volumes

        inventory={name:float(np.dot(gas_mass,fractions[:,i])) for i,name in enumerate(names)

                   if name in ('alme3','h2o')}

        if not all(np.isfinite(v) and v>=0 for v in inventory.values()):

            raise ValueError('气相库存积分无效')

        result['gas_inventory_kg']=inventory

    except (ValueError,KeyError,IndexError,TypeError):

        result['gas_inventory_kg']=None

    if not geometry or not geometry.get('layers'):

        result.update(status='partial',reason='已识别物种，但晶圆几何未取得，未推断片间区域')

        return result

    centers=centers[1:]

    try:

        speed=np.sqrt(sum(read_field(cells,k,mesh['nc'],_indexed_datasets).reshape(-1)**2 for k in ('SV_U','SV_V','SV_W')))

    except (ValueError,KeyError):speed=None

    inlet_y=None;inlet_center=None

    try:

        parts=[];weights=[];positions=[]

        scopes=[np.asarray(inlet_ids)] if inlet_ids is not None else [np.arange(a,b) for name,a,b in mesh['zones'] if re.search(r'(^|[-_\s])inlet($|[-_\s\d])',name.lower())]

        for ids in scopes:

            values=face_fractions(mesh,ids,len(names),_indexed_datasets)

            w=np.maximum(-mesh['flux'][ids],0)

            if w.sum()>0 and np.isfinite(values[w>0]).all():

                take=w>0

                parts.append(values[take]);weights.append(w[take])

                positions.append(mesh['xyz'][mesh['conn'][ids[take]]].mean(axis=1))

        if weights and sum(w.sum() for w in weights)>0:

            inlet_y=np.average(np.concatenate(parts),axis=0,weights=np.concatenate(weights))

            inlet_center=np.average(np.concatenate(positions),axis=0,weights=np.concatenate(weights))

    except (ValueError,KeyError,IndexError):pass

    result['inlet_mass_fractions']={n:float(inlet_y[i]) for i,n in enumerate(names)} if inlet_y is not None else {}

    result['mass_fraction_max_closure_error']=float(np.max(abs(fractions.sum(axis=1)-1)))

    all_masks=[]

    for layer in geometry['layers']:

        key=layer.get('configured_id') or (('lower' if layer['id']==1 else 'upper') if geometry['total_layers']==2 else 'single')

        channels=layers.get(key,{}).get('channels',[])

        flows=[c['net_kg_s'] for c in channels]

        low_limit=float(np.quantile(flows,.1)) if flows else None

        for ri,row in enumerate(layer['rows'],1):

            base=(centers[:,1]>=row['y_min_m'])&(centers[:,1]<=row['y_max_m'])&(centers[:,2]>=row['z_min_m'])&(centers[:,2]<=row['z_max_m'])

            split=layer.get('overlap_split_z_m')

            if split is not None:base&=(centers[:,2]<split) if ri==1 else (centers[:,2]>=split)

            lo=np.asarray(row['face_positions_lo_m']);hi=np.asarray(row['face_positions_hi_m'])

            ix=np.searchsorted(lo[1:],centers[:,0],side='right');safe=np.minimum(ix,len(lo)-2)

            mask=base&(ix<len(lo)-1)&(centers[:,0]>=hi[safe])&(centers[:,0]<=lo[safe+1])&np.isin(ix,row['regular_gap_index'])

            if not mask.any():raise ValueError('已识别片间区域没有物种单元')

            if any(np.any(mask&m) for m in all_masks):raise ValueError('物种片间区域重复归属')

            all_masks.append(mask)

            low_indices=[c['gap_index'] for c in channels if c['row']==ri and low_limit is not None and c['net_kg_s']<=low_limit]

            low=mask&np.isin(ix,low_indices)

            rear=None

            if inlet_center is not None:

                yc=(row['y_min_m']+row['y_max_m'])/2

                edge=yc+(.25 if yc>inlet_center[1] else -.25)*(row['y_max_m']-row['y_min_m'])

                rear=mask&((centers[:,1]>edge) if yc>inlet_center[1] else (centers[:,1]<edge))

            region={'layer':key,'row':ri,'y_range_m':[row['y_min_m'],row['y_max_m']], 'z_range_m':[row['z_min_m'],row['z_max_m']],

                    'species':{},'low_flow_channel_count':len(low_indices),

                    'low_flow_definition':'本层原生净质量流量最低10%分位阈值以下的通道，不以低速度冒充低流量'}

            surface_ids,surface_areas,surface_positions=wafer_surfaces(mesh,row,split,ri)

            surface_values=None

            if len(surface_ids):

                try:surface_values=face_fractions(mesh,surface_ids,len(names),_indexed_datasets)

                except (ValueError,KeyError,IndexError) as exc:region['surface_reason']=str(exc)

            else:region['surface_reason']='未识别到朝向规则片间隙的晶圆壁面'

            region['low_flow_threshold_kg_s']=low_limit

            if speed is not None:

                threshold=float(np.quantile(speed[mask],.1));slow=mask&(speed<=threshold)

                region.update(low_speed_threshold_m_s=threshold)

            for si,name in enumerate(names):

                y=fractions[:,si]

                st={'bulk':statistics(y[mask],volumes[mask]),'far_edge':statistics(y[rear],volumes[rear]) if rear is not None else None,

                    'low_flow':statistics(y[low],volumes[low]),'low_speed':statistics(y[slow],volumes[slow]) if speed is not None else None}

                selected=np.flatnonzero(mask);st['minimum_position_m']=centers[selected[np.argmin(y[mask])]].tolist()

                st['surface']=area_statistics(surface_values[:,si],surface_areas) if surface_values is not None else None

                surface_rear=((surface_positions[:,1]>edge) if yc>inlet_center[1] else (surface_positions[:,1]<edge)) if inlet_center is not None else None

                st['surface_far_edge']=area_statistics(surface_values[surface_rear,si],surface_areas[surface_rear]) if surface_values is not None and surface_rear is not None else None

                st['wall_adjacent']=area_statistics(y[mesh['owner'][surface_ids]-1],surface_areas) if len(surface_ids) else None

                if surface_values is not None:

                    st['surface_minimum_position_m']=surface_positions[np.argmin(surface_values[:,si])].tolist()

                if inlet_y is not None and inlet_y[si]>0:

                    st.update(mean_inlet_ratio=float(st['bulk']['mean']/inlet_y[si]),

                              below_half_inlet_volume_pct=float(np.clip(100*volumes[mask&(y<inlet_y[si]*.5)].sum()/volumes[mask].sum(),0,100)),

                              below_90pct_inlet_volume_pct=float(np.clip(100*volumes[mask&(y<inlet_y[si]*.9)].sum()/volumes[mask].sum(),0,100)))

                region['species'][name]=st

            result['regions'].append(region)

            selected=np.flatnonzero(mask)

            order=np.argsort(ix[selected],kind='stable')

            member=selected[order]

            gap_ids=ix[member]

            for gi in row['regular_gap_index']:

                start,stop=np.searchsorted(gap_ids,[gi,gi+1])

                gm=member[start:stop]

                native=next((c for c in channels if c['row']==ri and c['gap_index']==gi),{})

                result['channels'].append({'layer':key,'row':ri,'gap_index':int(gi),'x_range_m':[float(hi[gi]),float(lo[gi+1])],

                                           'native_net_kg_s':native.get('net_kg_s'),'low_flow':int(gi) in low_indices,

                                           'species':{name:statistics(fractions[gm,si],volumes[gm]) for si,name in enumerate(names)}})

    result['status']='computed'

    return result





def render_section(result):

    from af_species_report import render_section as render

    return render(result)

