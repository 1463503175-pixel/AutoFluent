# -*- coding: utf-8 -*-
"""Case-only physical geometry preflight, without loading solution fields.

Runs the same connected-patch wafer recognizer as the physical engine, but
streams wall-zone face/node connectivity and never allocates an all-faces map.
A valid config is not itself a valid geometry preflight.
"""
from pathlib import Path
import json


def precheck_case_geometry(case_path, *, config=None, max_candidate_triangles=1_500_000,
                           memory_budget_mb=3072):
    import h5py
    import numpy as np
    from af_progress import update as progress
    progress(8,'模型几何预检','仅打开 Case，不读取 Data 物理场')
    from af_adaptive_native import (_indexed_datasets, _face_node_blocks,
                                    _face_zones, UnsupportedCFF)
    from af_adaptive_geometry import (identify_from_configured_wall_triangles,
                                      identify_from_wall_triangles, GeometryAmbiguous)
    case=Path(case_path).expanduser().resolve()
    if not 512 <= memory_budget_mb <= 16384:
        raise ValueError('Geometry precheck budget must be 512..16384 MiB')
    report={'schema':'autofluent.geometry_case_precheck.v1',
            'case_path':str(case), 'mode':'configured' if config else 'automatic',
            'status':'blocked', 'geometry':None,
            'note':'Case-only wall geometry; no SV_FLUX, flow, pressure, convergence or physical partition is certified'}
    try:
        with h5py.File(case,'r') as cf:
            if 'meshes' not in cf or len(cf['meshes'])!=1:
                raise UnsupportedCFF('Geometry precheck supports a single CFF mesh only')
            mesh=next(iter(cf['meshes'].values()))
            if 'nodes/coords' not in mesh or 'faces/c0' not in mesh:
                raise UnsupportedCFF('Case lacks node coordinates or authoritative face count')
            nodes=_indexed_datasets(mesh['nodes/coords'],context='nodes/coords')
            nf=_indexed_datasets(mesh['faces/c0'],context='faces/c0')[-1][1]
            node_count=nodes[-1][1]
            zones=_face_zones(mesh,nf)
            wall_ranges=[(name,a,b) for name,a,b in zones if 'wall' in name.lower() or '壁' in name]
            report.update(mesh_faces=int(nf),node_count=int(node_count),
                          wall_zone_ranges=[{'zone':n,'face_count':int(b-a)} for n,a,b in wall_ranges],
                          named_zones=[{'name':n,'face_count':int(b-a)} for n,a,b in zones])
            if not wall_ranges:
                raise UnsupportedCFF('No explicit wall face zones; cannot reliably infer wafer candidates from Case alone')
            node_bytes=node_count*3*8
            estimate=node_bytes+max_candidate_triangles*3*3*8*1.6+256*1024**2
            if estimate>memory_budget_mb*1024**2:
                raise UnsupportedCFF('Geometry precheck estimate %.2f GiB exceeds budget %.2f GiB; nodes=%d, candidate_cap=%d'
                                     %(estimate/1024**3,memory_budget_mb/1024,node_count,max_candidate_triangles))
            progress(20,'实际网格坐标','检查节点区间并读取必要坐标')
            xyz=np.empty((node_count,3),np.float64)
            for lo,hi,ds in nodes:
                for a in range(lo,hi,131072):
                    b=min(a+131072,hi)
                    xyz[a:b]=ds[a-lo:b-lo]
            if not np.isfinite(xyz).all():
                raise UnsupportedCFF('Node coordinates contain NaN or infinity')
            node_blocks=_face_node_blocks(mesh,nf)
            tol=max(2e-8,float(np.linalg.norm(np.ptp(xyz,axis=0)))*2e-7)
            bands=[(layer['y_range_m'],layer.get('wafer_x_range_m')) for layer in config['layers']] if config else []
            progress(35,'壁面候选筛选','只扫描壁面区域及 X 法向真实面片')
            candidates=[];total=0;scanned=0
            # Only candidate wall regions, only their H5 slices; do not allocate the
            # nfaces-by-3 face connectivity or all-face neighbour/flux vectors.
            for zone_name,zone_lo,zone_hi in wall_ranges:
                for blo,bhi,face_nodes,nnodes in node_blocks:
                    start=max(zone_lo,blo);end=min(zone_hi,bhi)
                    if start>=end:continue
                    for a in range(start,end,32768):
                        b=min(end,a+32768)
                        offsets_a=a-blo;offsets_b=b-blo
                        if not np.all(nnodes[offsets_a:offsets_b]==3):
                            raise UnsupportedCFF('Nontriangular wall face at face %d'% (a+1))
                        indices=np.asarray(face_nodes[3*offsets_a:3*offsets_b],np.int64)-1
                        if np.any(indices<0) or np.any(indices>=node_count):
                            raise UnsupportedCFF('Wall face/node index outside known mesh at face %d'%(a+1))
                        pts=xyz[indices.reshape(-1,3)]; scanned+=len(pts)
                        keep=np.ptp(pts[:,:,0],axis=1)<=tol
                        if bands and np.any(keep):
                            ctr=pts.mean(axis=1);spatial=np.zeros(len(ctr),dtype=bool)
                            for (y0,y1),xr in bands:
                                selected=(ctr[:,1]>=y0)&(ctr[:,1]<=y1)
                                if xr is not None:selected &= (ctr[:,0]>=xr[0]-tol)&(ctr[:,0]<=xr[1]+tol)
                                spatial |= selected
                            keep &= spatial
                        count=int(np.count_nonzero(keep))
                        if total+count>max_candidate_triangles:
                            raise UnsupportedCFF('X-normal wall candidates %d exceed precheck cap %d; tighten Y/X model search ranges'
                                                 %(total+count,max_candidate_triangles))
                        if count:candidates.append(pts[keep].copy());total+=count
            report.update(scanned_wall_faces=int(scanned),x_normal_candidates=int(total))
            if total<50:raise GeometryAmbiguous('Only %d candidate wafer wall triangles; check Case zones and wafer search bands'%total)
            triangles=np.concatenate(candidates)
            progress(75,'晶圆配对与分排','按几何连通、厚度和排对应判定真实片间通道')
            geometry=(identify_from_configured_wall_triangles(triangles,config) if config else
                      identify_from_wall_triangles(triangles))
            report['geometry']=geometry
            report['layers']=[{'id':layer.get('configured_id',layer['id']),
                               'wafer_count':sum(int(row['plate_count']) for row in layer['rows']),
                               'channel_count':int(layer['channels']),
                               'cut_y_m':layer['y_cut_m'],
                               'rows':[{'id':row.get('id'), 'wafer_count':int(row['plate_count']),
                                        'regular_channel_count':len(row['regular_gap_index']),
                                        'first_x_m':row['face_positions_lo_m'][0],
                                        'last_x_m':row['face_positions_hi_m'][-1]}
                                       for row in layer['rows']]}
                              for layer in geometry['layers']]
            report['status']='geometry_candidates_identified_requires_flux_validation'
    except (OSError,KeyError,ValueError,IndexError,MemoryError) as exc:
        report['blocker']={'stage':getattr(exc,'stage',None) or 'case_geometry_precheck',
                           'type':type(exc).__name__,'reason':str(exc)[:800],
                           'diagnostics':getattr(exc,'diagnostics',{})}
    progress(100 if report['status']!='blocked' else 90,
             'Case 几何预检完成' if report['status']!='blocked' else '晶圆壁面配对待复核',
             report['status'],force=True)
    return report


def write_precheck_report(report, directory):
    import html
    import uuid
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    name='geometry_precheck_'+uuid.uuid4().hex[:10]
    jsonfile=folder/(name+'.json');htmlfile=folder/(name+'.html')
    with jsonfile.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    esc=lambda x:html.escape(str(x),quote=True)
    rows=''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(
        esc(row['id']),esc(row['wafer_count']),esc(row['channel_count']),esc(row['cut_y_m']))
        for row in report.get('layers',[]))
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Case 几何预检</title>
    <style>body{font:15px/1.6 system-ui;max-width:950px;margin:35px auto;color:#193643}
    header{background:#133644;color:white;padding:28px;border-radius:12px}section{background:#f1f5f7;padding:24px;border-radius:12px;margin:16px 0}
    table{width:100%%;border-collapse:collapse}td,th{border-bottom:1px solid #ccc;padding:9px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
    <header><h1>模型几何预检</h1><p>%s</p></header><section><p>Case：%s</p>
    <p>扫描壁面 %s；X 法向候选 %s</p><p>此报告不包含求解流量或压力，不等于完整物理分析。</p></section>
    <section><h2>分层分排</h2><table><tr><th>层</th><th>片数</th><th>通道</th><th>Y 截面 (m)</th></tr>%s</table></section>
    <section><h2>检查详情</h2><pre>%s</pre></section></html>'''%(
        esc(report['status']),esc(report['case_path']),esc(report.get('scanned_wall_faces','未取得')),
        esc(report.get('x_normal_candidates','未取得')),rows,esc(json.dumps(report,ensure_ascii=False,indent=2)))
    with htmlfile.open('x',encoding='utf-8') as f:f.write(page)
    return {'json_file':str(jsonfile),'html_file':str(htmlfile)}
