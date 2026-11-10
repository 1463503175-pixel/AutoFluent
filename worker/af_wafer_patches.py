# -*- coding: utf-8 -*-
"""Measured wafer face patches. No expected channel or wafer count is manufactured.

Patch identity is plane (X) plus connected YZ triangle footprint. Different
row faces at the SAME X station remain distinct objects. This is deliberately
conservative: ambiguous component connectivity / thickness pairings are reported
as geometry errors, not silently assigned.
"""
from collections import defaultdict
import numpy as np


class PatchRecognitionError(ValueError):
    """Fail closed with a machine-readable layer stage and candidate evidence."""
    def __init__(self, message, *, stage='wafer_pairing', diagnostics=None):
        super().__init__(message)
        self.stage=stage
        self.diagnostics=dict(diagnostics or {})


def _record(diag, **data):
    if diag is not None:
        diag.update(data)


def extract_patches(triangles, *, tol, y_range, x_range=None, min_area=None, diagnostics=None):
    """Keep X-normal triangles and cluster connected panels per X station.

    Working copies are bounded by one X-station at a time. Exact/snap-rounded
    shared projected vertices define connectivity; disjoint coplanar rows stay
    separate even when their X coordinates coincide.
    """
    verts=np.asarray(triangles,dtype=np.float64)
    if verts.ndim!=3 or verts.shape[1:]!=(3,3):
        raise PatchRecognitionError('Wall triangle array shape must be (N,3,3)')
    if not np.isfinite(verts).all():
        raise PatchRecognitionError('Non-finite wafer wall coordinates')
    _record(diagnostics, raw_wall_triangle_count=int(len(verts)), stage='wall_candidate_screening')
    ctr=verts.mean(axis=1)
    flat=np.ptp(verts[:,:,0],axis=1)<=tol
    mask=flat & (ctr[:,1]>=y_range[0]) & (ctr[:,1]<=y_range[1])
    if x_range is not None:
        mask &= (ctr[:,0]>=x_range[0]-tol)&(ctr[:,0]<=x_range[1]+tol)
    _record(diagnostics, x_normal_triangle_count=int(flat.sum()),
            spatially_filtered_triangle_count=int(mask.sum()))
    verts=verts[mask]
    if not len(verts):raise PatchRecognitionError('No X-normal wafer candidates in configured layer/X search ranges',
                                                  stage='wall_candidate_screening',diagnostics=diagnostics)
    centers=ctr[mask]
    normals=np.cross(verts[:,1]-verts[:,0],verts[:,2]-verts[:,0]);area=np.linalg.norm(normals,axis=1)*.5
    good=np.isfinite(area)&(area>0)
    verts=verts[good];centers=centers[good];area=area[good]
    if not len(verts):raise PatchRecognitionError('Candidate wafer faces have no measurable area',
                                                  stage='wall_candidate_screening',diagnostics=diagnostics)
    _record(diagnostics, nondegenerate_triangle_count=int(len(verts)))
    bins=np.rint(centers[:,0]/tol).astype(np.int64)
    order=np.argsort(bins,kind='stable');sorted_bins=bins[order]
    limits=np.r_[0,np.flatnonzero(np.diff(sorted_bins))+1,len(sorted_bins)]
    patches=[]
    merged_fragments=0
    for a,b in zip(limits[:-1],limits[1:]):
        indices=order[a:b];v=verts[indices];w=area[indices];ctr_station=centers[indices]
        # A single shared vertex is NOT a mesh edge. Two adjacent wafers can
        # touch at a node or have interleaved discretisations at the same X;
        # merge only triangles sharing an entire projected mesh edge.
        yz=np.rint(v[:,:,1:].reshape(-1,2)/tol).astype(np.int64)
        _,vertex_ids=np.unique(yz,axis=0,return_inverse=True)
        parents=np.arange(len(indices),dtype=np.int32)
        def root(i):
            while parents[i]!=i:
                parents[i]=parents[parents[i]];i=int(parents[i])
            return i
        def union(i,j):
            i=root(i);j=root(j)
            if i!=j:parents[j]=i
        shared={}
        for tri_i,vertices in enumerate(vertex_ids.reshape(-1,3)):
            for u,vv in ((0,1),(1,2),(2,0)):
                first,second=sorted((int(vertices[u]),int(vertices[vv])))
                if first==second:continue
                edge=(first,second)
                prev=shared.get(edge)
                if prev is None:shared[edge]=tri_i
                else:union(tri_i,prev)
        groups=defaultdict(list)
        for i in range(len(indices)):groups[root(i)].append(i)
        # Small mesh islands may be pieces of one panel when nonconforming
        # triangles leave a microscopic seam. Reattach only a uniquely matched
        # *boundary* fragment: same X, edge-aligned projected bounding boxes,
        # practically touching, and a parent panel much larger than the island.
        # Do not join two complete disjoint wafer rows even if their boxes meet.
        components=[list(group) for group in groups.values()]
        if 1<len(components)<=512:
            def footprint(ids):
                nodes=v[np.asarray(ids,dtype=np.int64)].reshape(-1,3)
                return (float(nodes[:,1].min()),float(nodes[:,1].max()),
                        float(nodes[:,2].min()),float(nodes[:,2].max()))
            areas=[float(w[group].sum()) for group in components]
            bounds=[footprint(group) for group in components]
            max_area=max(areas)
            small=[i for i,weight in enumerate(areas) if weight<max_area*.12]
            mains=[i for i,weight in enumerate(areas) if weight>=max_area*.50]
            taken=set()
            for fi in small:
                fa,fb,fc,fd=bounds[fi]
                matches=[]
                for pi in mains:
                    ya,yb,za,zb=bounds[pi]
                    overlap_y=max(0,min(fb,yb)-max(fa,ya))
                    overlap_z=max(0,min(fd,zb)-max(fc,za))
                    gap_y=max(0,ya-fb,fa-yb)
                    gap_z=max(0,za-fd,fc-zb)
                    # Only a seam on one edge with substantial common extent
                    # along the other direction is convincing geometric proof.
                    seam=((gap_y<=tol*5 and overlap_y<=tol*3 and
                           overlap_z>=.70*min(fd-fc,zb-za)) or
                          (gap_z<=tol*5 and overlap_z<=tol*3 and
                           overlap_y>=.70*min(fb-fa,yb-ya)))
                    enlarged_y=max(yb,fb)-min(ya,fa)
                    enlarged_z=max(zb,fd)-min(za,fc)
                    if (seam and enlarged_y<=(yb-ya)*1.25 and
                            enlarged_z<=(zb-za)*1.25):
                        matches.append(pi)
                if len(matches)==1:
                    components[matches[0]].extend(components[fi]);taken.add(fi)
                    merged_fragments+=1
            components=[c for i,c in enumerate(components) if i not in taken]
        for component in components:
            sub=np.asarray(component,dtype=np.int64)
            vv=v[sub];ar=w[sub];cc=ctr_station[sub]
            ext=vv.reshape(-1,3)
            ay=np.ptp(ext[:,1]);az=np.ptp(ext[:,2])
            if min_area is not None and ar.sum()<min_area:continue
            if ay<=tol*2 or az<=tol*2:continue
            patches.append({'x':float(np.average(cc[:,0],weights=ar)),
                            'area':float(ar.sum(dtype=np.float64)),
                            'y':(float(ext[:,1].min()),float(ext[:,1].max())),
                            'z':(float(ext[:,2].min()),float(ext[:,2].max())),
                            'triangles':int(len(sub)),
                            'z_centers':cc[:,2].copy(), 'area_weights':ar.copy(),
                            'centroid_yz_m':[float(np.average(cc[:,1],weights=ar)),
                                             float(np.average(cc[:,2],weights=ar))],
                            'projection_extent_yz_m':[float(ay),float(az)]})
        # Do not reject patches simply because bounding boxes overlap.
        # Non-convex/disconnected physical panels can have overlapping boxes;
        # actual front/back pairing and row ownership must resolve them.
        # The raw overlapping candidates are retained for explicit review.
        if diagnostics is not None:
            diagnostics['x_station_count']=int(len(limits)-1)
    _record(diagnostics, merged_boundary_fragment_count=int(merged_fragments),
            connected_patch_count=int(len(patches)),
            connected_patches_per_x_station={str(round(float(x),8)):int(n) for x,n in
                sorted(__import__('collections').Counter(round(float(p['x']),8) for p in patches).items())[:12]},
            first_patch_evidence=[{'x_m':p['x'],'area_m2':p['area'],
                                   'y_range_m':list(p['y']),'z_range_m':list(p['z']),
                                   'centroid_yz_m':p['centroid_yz_m']} for p in patches[:10]],
            stage='physical_patch_pairing',
            first_patch_x_m=[float(p['x']) for p in patches[:8]])
    if not patches:raise PatchRecognitionError('No connected X-normal wafer wall patches survived layer screening',
                                                stage='connected_patch_extraction',diagnostics=diagnostics)
    return patches


def _footprint_similarity(a,b):
    ratios=[]
    for axis in ('y','z'):
        a0,a1=a[axis];b0,b1=b[axis]
        overlap=max(0.,min(a1,b1)-max(a0,b0))
        # Symmetric overlap: an unrelated smaller support located inside a
        # wafer bounding box must not count as a full matching wafer face.
        length=max(a1-a0,b1-b0)
        ratios.append(overlap/max(length,1e-30))
    area_ratio=min(a['area'],b['area'])/max(a['area'],b['area'])
    return min(ratios),area_ratio


def _exclusive_evidence(patch,row_ranges):
    weights=patch['area_weights'];z=patch['z_centers']
    counts=[]
    for i,(lo,hi) in enumerate(row_ranges):
        mask=(z>=lo)&(z<=hi)
        for j,(ol,oh) in enumerate(row_ranges):
            if i!=j:mask &= (z<ol)|(z>oh)
        counts.append(float(weights[mask].sum()))
    return np.asarray(counts)


def _paired_row_by_center(front, back, row_ranges, tolerance):
    """Geometric fallback only when the Z-row centers are distinguishable.

    A row's projection can overlap another row without a wafer becoming a
    member of both. The center is measured on the *paired* physical object.
    Close centers or a central patch remain ambiguous instead of guessed.
    """
    if len(row_ranges)==1:
        return 0
    z=float((front['centroid_yz_m'][1]+back['centroid_yz_m'][1])*.5)
    centers=np.asarray([.5*(lo+hi) for lo,hi in row_ranges],dtype=float)
    distances=np.abs(centers-z)
    order=np.argsort(distances)
    if len(order)<2:return int(order[0])
    width=min(front['z'][1]-front['z'][0],back['z'][1]-back['z'][0])
    if (distances[order[1]]-distances[order[0]] <= max(tolerance*5,width*.08)
            or abs(centers[order[1]]-centers[order[0]]) <= max(tolerance*10,width*.10)):
        return None
    if not (row_ranges[order[0]][0]-tolerance<=z<=row_ranges[order[0]][1]+tolerance):
        return None
    return int(order[0])


def _infer_thickness(patches,tol):
    """Candidate thickness from spatially matching planes, not global X sort."""
    proposals=[]
    ordered=sorted(patches,key=lambda p:p['x'])
    for i,p in enumerate(ordered):
        for q in ordered[i+1:i+12]:
            delta=q['x']-p['x']
            if delta<=tol:continue
            overlap,area_ratio=_footprint_similarity(p,q)
            if overlap>.85 and area_ratio>.65:proposals.append(delta);break
    if len(proposals)<4:
        raise PatchRecognitionError('Cannot infer wafer thickness from repeated spatially paired wall faces')
    t=float(np.median(np.sort(proposals)[:max(4,len(proposals)//2)]))
    return (max(t*.70,tol*3),t*1.30)


def pair_wafer_patches(patches, *, thickness_range, row_ranges, min_plates=5,
                       regular_gap_range=None, x_range=None, diagnostics=None):
    """One-to-one physical wafer pairing, row assignment after pairing.

    An isolated end-wall station is excluded if it lies outside the dominant
    repeated row sequence; an interior unpaired wafer-sized face is an error.
    """
    _record(diagnostics, connected_patch_count=int(len(patches)),
            minimum_required_patches=int(2*min_plates*len(row_ranges)),stage='physical_patch_pairing')
    if len(patches)<2*min_plates*len(row_ranges):
        raise PatchRecognitionError('Too few connected wall patches for wafer pairing: found %d, require >=%d' %
                                    (len(patches),2*min_plates*len(row_ranges)),
                                    stage='physical_patch_pairing',diagnostics=diagnostics)
    a=np.asarray([p['area'] for p in patches]);med=float(np.median(a))
    # Reject broad chamber end-walls and tiny independent supports by area.
    keep=(a>=med*.23)&(a<=med*3.5)
    excluded=[{'x_m':p['x'],'area_m2':p['area'],'y_range_m':list(p['y']),
               'z_range_m':list(p['z']),'reason':'non_repeating_patch_area'}
              for p,k in zip(patches,keep) if not k]
    p=[p for p,k in zip(patches,keep) if k]
    _record(diagnostics, area_median_m2=med,area_acceptance_m2=[med*.23,med*3.5])
    _record(diagnostics, patch_count_after_area_filter=int(len(p)),
            area_filter_excluded_count=len(excluded),
            area_filter_first_excluded=excluded[:8])
    tlo,thi=thickness_range
    p.sort(key=lambda patch:patch['x'])
    edges=defaultdict(list)
    for i,first in enumerate(p):
        for j in range(i+1,len(p)):
            delta=p[j]['x']-first['x']
            if delta>thi+1e-8:break
            if delta<tlo-1e-8:continue
            footprint,ratio=_footprint_similarity(first,p[j])
            if footprint>=.80 and ratio>=.55:
                edges[i].append(j);edges[j].append(i)
    _record(diagnostics, thickness_candidate_edge_count=int(sum(len(x) for x in edges.values())//2),
            thickness_bounds_m=[float(tlo),float(thi)])
    for i,neighbours in edges.items():
        if len(neighbours)>1:
            distances=[abs(p[j]['x']-p[i]['x']) for j in neighbours]
            _record(diagnostics, first_conflict_x_m=float(p[i]['x']),
                    competing_thickness_m=[float(x) for x in distances],
                    competing_patch_areas_m2=[float(p[j]['area']) for j in neighbours],
                    candidate_patch_area_m2=float(p[i]['area']),
                    rejected_by='nonunique_thickness_spatial_pairing',
                    paired_wafer_count=int(len(pairs)) if 'pairs' in locals() else 0)
            raise PatchRecognitionError('Multiple thickness-compatible front/back wall patches near X=%.9g m; possible thicknesses %s m' %
                                        (p[i]['x'],[round(x,9) for x in distances]),
                                        stage='physical_patch_pairing',diagnostics=diagnostics)
    used=set();pairs=[]
    for i in sorted(edges):
        if i in used:continue
        j=edges[i][0]
        if j in used:continue
        first,second=p[min(i,j)],p[max(i,j)]
        ev=_exclusive_evidence(first,row_ranges)+_exclusive_evidence(second,row_ranges)
        if len(row_ranges)==1:
            row=0
        elif ev.max()<=max(first['area'],second['area'])*.002:
            row=_paired_row_by_center(first,second,row_ranges,max(tlo*.03,1e-8))
            if row is None:
                _record(diagnostics,first_conflict_x_m=float(first['x']),
                        candidate_patch_area_m2=[first['area'],second['area']],
                        competing_row_centers_z_m=[float((lo+hi)*.5) for lo,hi in row_ranges],
                        rejected_by='no_unique_row_evidence')
                raise PatchRecognitionError('No unique Z row evidence for paired wafer at X=%.9g m' %first['x'],
                                            stage='row_assignment',diagnostics=diagnostics)
        elif np.count_nonzero(ev>ev.max()*.08)>1:
            _record(diagnostics,first_conflict_x_m=float(first['x']),
                    candidate_patch_area_m2=[first['area'],second['area']],
                    exclusive_row_area_m2=ev.tolist(),rejected_by='contradictory_row_evidence')
            raise PatchRecognitionError('Contradictory Z-row exterior evidence at X=%.9g m' %first['x'],
                                        stage='row_assignment',diagnostics=diagnostics)
        else:row=int(np.argmax(ev))
        pairs.append({'xlo':first['x'],'xhi':second['x'],'row':row,
                      'ylo':max(first['y'][0],second['y'][0]),
                      'yhi':min(first['y'][1],second['y'][1]),
                      'zlo':max(first['z'][0],second['z'][0]),
                      'zhi':min(first['z'][1],second['z'][1])})
        used.update((i,j))
        _record(diagnostics,paired_wafer_count=int(len(pairs)),
                interim_row_wafer_counts=[sum(q['row']==ri for q in pairs) for ri in range(len(row_ranges))])
    # Two physically distinct wafers must not occupy the same X thickness
    # interval *and* overlapping YZ footprint. This catches genuinely
    # coincident solid panels while allowing aligned rows with disjoint Z.
    for i,first in enumerate(pairs):
        for second in pairs[i+1:]:
            x_overlap=min(first['xhi'],second['xhi'])-max(first['xlo'],second['xlo'])
            y_overlap=min(first['yhi'],second['yhi'])-max(first['ylo'],second['ylo'])
            z_overlap=min(first['zhi'],second['zhi'])-max(first['zlo'],second['zlo'])
            smaller=min(first['zhi']-first['zlo'],second['zhi']-second['zlo'])
            if x_overlap>tlo*.35 and y_overlap>0 and z_overlap>max(tlo*.02,smaller*.025):
                _record(diagnostics,first_conflict_x_m=float(first['xlo']),
                        first_conflict_z_overlap_m=float(z_overlap),
                        rejected_by='physically_overlapping_wafer_panels')
                raise PatchRecognitionError('both overlapping rows contain physically coincident wafer panels at X=%.9g m (YZ overlap %.9g m)' %
                                            (first['xlo'],z_overlap),stage='row_assignment',diagnostics=diagnostics)
    _record(diagnostics, paired_wafer_count=int(len(pairs)),stage='row_assignment')
    if len(pairs)<min_plates*len(row_ranges):
        unmatched=[(i,c) for i,c in enumerate(p) if i not in used]
        first=unmatched[0][1] if unmatched else None
        _record(diagnostics,paired_wafer_count=len(pairs),
                unmatched_candidate_count=len(unmatched),
                first_conflict_x_m=float(first['x']) if first is not None else None,
                candidate_patch_area_m2=float(first['area']) if first is not None else None,
                rejected_by='insufficient_unique_thickness_pairs')
        raise PatchRecognitionError('Confirmed wafer pairs below per-row minimum; found %d, unmatched=%d' %
                                    (len(pairs),len(unmatched)),
                                    stage='physical_patch_pairing',diagnostics=diagnostics)
    rows=[]
    for row in range(len(row_ranges)):
        plate=sorted((q for q in pairs if q['row']==row),key=lambda q:q['xlo'])
        if len(plate)<min_plates:
            _record(diagnostics,first_conflict_row=row+1,paired_wafer_count=len(pairs),
                    identified_row_wafer_counts=[sum(q['row']==ri for q in pairs) for ri in range(len(row_ranges))],
                    rejected_by='insufficient_row_wafers')
            raise PatchRecognitionError('Row %d has only %d physical wafer pairs' % (row+1,len(plate)),
                                        stage='row_assignment',diagnostics=diagnostics)
        spacing=np.diff([q['xlo'] for q in plate])
        core=float(np.median(spacing)) if len(spacing) else 0.
        # Disconnected one-off structural planes are NOT appended as wafers.
        # Preserve legitimate central wide gaps (up to 2.5 regular pitches).
        groups=[];start=0
        for k,delta in enumerate(spacing):
            if core>0 and delta>max(core*2.5, float(np.quantile(spacing,.80))*1.65):
                groups.append(plate[start:k+1]);start=k+1
        groups.append(plate[start:])
        valid=[g for g in groups if len(g)>=min_plates]
        if not valid:
            raise PatchRecognitionError('No repeated wafer run in row %d; candidate patches are isolated' %(row+1))
        if len(valid)>1:
            raise PatchRecognitionError('Multiple plausible repeated wafer arrays in row %d; narrow wafer_x_range_m' %(row+1))
        chosen=valid[0]
        _record(diagnostics,identified_row_wafer_counts=[sum(q['row']==ri for q in pairs) for ri in range(len(row_ranges))])
        dropped=[q for q in plate if q not in chosen]
        for q in dropped:excluded.append({'x_m':q['xlo'],'reason':'isolated_paired_patch_outside_repeated_array'})
        for q in chosen:
            if q['ylo']>=q['yhi'] or q['zlo']>=q['zhi']:
                raise PatchRecognitionError('Front/back wall has no common geometric footprint at X=%.9g m' %q['xlo'])
        lo=np.asarray([q['xlo'] for q in chosen]);hi=np.asarray([q['xhi'] for q in chosen])
        gap=lo[1:]-hi[:-1]
        if not len(gap) or np.any(gap<=0):
            raise PatchRecognitionError('Overlapping wafers or missing positive gap in row %d' %(row+1))
        gap_ref=float(np.median(gap))
        if regular_gap_range is None:
            regular=(gap>=gap_ref*.65)&(gap<=gap_ref*1.4)
        else:
            regular=(gap>=regular_gap_range[0])&(gap<=regular_gap_range[1])
        if int(regular.sum())<max(2,int(len(gap)*.55)):
            _record(diagnostics,first_conflict_row=row+1,
                    first_conflict_x_m=float(lo[np.flatnonzero(~regular)[0]]) if np.any(~regular) else None,
                    rejected_by='too_few_regular_same_row_gaps')
            raise PatchRecognitionError('Row %d has too few regular measured wafer gaps' % (row+1),
                                        stage='channel_generation',diagnostics=diagnostics)
        if np.any(gap<gap_ref*.65):
            gap_index=int(np.argmin(gap))
            _record(diagnostics,first_conflict_row=row+1,
                    first_conflict_x_m=float(lo[gap_index]),
                    conflicting_gap_m=float(gap[gap_index]),
                    rejected_by='narrow_same_row_gap')
            raise PatchRecognitionError('Row %d contains narrow unclassified physical gap at X=%.9g m' %(row+1,lo[gap_index]),
                                        stage='channel_generation',diagnostics=diagnostics)
        rows.append({'face_positions_lo_m':lo.tolist(),'face_positions_hi_m':hi.tolist(),
                     'plate_count':len(lo),'plate_thickness_m':float(np.median(hi-lo)),
                     'gap_width_m':gap.tolist(),'regular_gap_index':np.flatnonzero(regular).tolist(),
                     'wide_gap_index':np.flatnonzero(gap>gap_ref*1.4).tolist(),
                     'narrow_gap_index':np.flatnonzero(gap<gap_ref*.65).tolist(),
                     'reference_gap_m':gap_ref,
                     'z_min_m':min(q['zlo'] for q in chosen),'z_max_m':max(q['zhi'] for q in chosen),
                     'y_min_m':min(q['ylo'] for q in chosen),'y_max_m':max(q['yhi'] for q in chosen),
                     'median_face_x_m':float(np.median(lo))})
    # An in-range wafer-sized orphan is evidence of a missing wafer wall and
    # must never be removed simply to satisfy an expected count.
    for i,patch in enumerate(p):
        if i not in used:
            within=any((r['face_positions_lo_m'][0]-tlo)<=patch['x']<=(r['face_positions_hi_m'][-1]+tlo)
                       and patch['z'][1]>=r['z_min_m'] and patch['z'][0]<=r['z_max_m'] for r in rows)
            # An orphan occupying the predicted immediately adjacent wafer
            # station is more likely a missing front/back face than a support.
            # Do not discard it just because it lies outside the first/last
            # successfully paired wafer.
            for r in rows:
                pitch=r['reference_gap_m']+r['plate_thickness_m']
                predicted=(r['face_positions_lo_m'][0]-pitch,
                           r['face_positions_lo_m'][0]-pitch+r['plate_thickness_m'],
                           r['face_positions_lo_m'][-1]+pitch,
                           r['face_positions_lo_m'][-1]+pitch+r['plate_thickness_m'])
                same_z=patch['z'][1]>=r['z_min_m'] and patch['z'][0]<=r['z_max_m']
                if same_z and any(abs(patch['x']-expect)<max(tlo*.30,1e-7) for expect in predicted):
                    within=True
            if within:
                _record(diagnostics,first_conflict_x_m=float(patch['x']),
                        candidate_patch_area_m2=float(patch['area']),
                        rejected_by='unpaired_patch_inside_repeated_array',
                        unmatched_candidate_count=len(p)-len(used))
                raise PatchRecognitionError('Unpaired wafer-sized wall patch near identified array at X=%.9g m; missing front/back face is plausible' %patch['x'],
                                            stage='physical_patch_pairing',diagnostics=diagnostics)
            excluded.append({'x_m':patch['x'],'reason':'unpaired_outside_repeated_array'})
    if x_range:
        for patch in p:
            if not x_range[0]-1e-8<=patch['x']<=x_range[1]+1e-8:
                raise PatchRecognitionError('Wafer patch fell outside supplied X range after filtering')
    _record(diagnostics, identified_row_wafer_counts=[int(x['plate_count']) for x in rows],
            identified_row_channel_counts=[len(x['regular_gap_index']) for x in rows],
            excluded_candidate_count=int(len(excluded)),excluded_candidates=excluded[:12],stage='complete')
    return rows,{'raw_patch_count':len(patches),'paired_wafer_count':len(pairs),
                 'excluded_candidate_count':len(excluded),
                 'first_excluded_candidates':excluded[:8],
                 'method':'same_X_disjoint_connected_panels_then_footprint_thickness_pairs_then_row_evidence',
                 'thickness_bounds_m':[float(tlo),float(thi)]}
