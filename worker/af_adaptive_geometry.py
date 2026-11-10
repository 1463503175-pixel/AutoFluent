# -*- coding: utf-8 -*-
"""Conservative, candidate-first auto recognition of axis-aligned ALD wafer arrays.

Input is *only* wall-face triangles and their true Fluent vertex coordinates.
No historic wafer counts, dimensions, x-stations or y layer limits are read.
Return an auditable geometry profile or fail closed. This first implementation
supports wafer normals X, flow Y, one/two separated rows along Z, and one/two
non-overlapping layers along Y. Staggered Z rows may have different X stations.
"""
import math
from dataclasses import dataclass


class GeometryAmbiguous(ValueError):
    def __init__(self, message, *, stage=None, diagnostics=None):
        super().__init__(message)
        self.stage=stage
        self.diagnostics=dict(diagnostics or {})


def _bins_minimum_split(values, weights, *, n_bins=160, imbalance_limit=.32):
    """Find an empty/low-area gap between two well populated axial bands.

    If two layers touch with no trough, do not invent a separation.
    """
    import numpy as np
    y=np.asarray(values,dtype=float); w=np.asarray(weights,dtype=float)
    if len(y)<12 or len(y)!=len(w) or not np.all(np.isfinite(y)):
        return None
    low,high=np.percentile(y,[.3,99.7]);span=high-low
    if span<=0:return None
    hist,edges=np.histogram(y,bins=n_bins,range=(low,high),weights=w)
    smooth=np.convolve(hist,np.ones(5)/5,mode='same')
    # Bands need substantial area on *both* sides, not just a single outlier.
    cumulative=np.cumsum(hist); total=cumulative[-1]
    if total<=0:return None
    eligible=np.flatnonzero((cumulative>=imbalance_limit*total)&
                            (cumulative<=(1-imbalance_limit)*total))
    eligible=eligible[(eligible>=8)&(eligible<n_bins-8)]
    if len(eligible)==0:return None
    # An empty geometric separation can be much clearer than a density notch,
    # especially for coarsely triangulated wafer rectangles.
    populated=np.flatnonzero(hist>0)
    gaps=np.diff(populated)
    if len(gaps)>2:
        k=int(np.argmax(gaps))
        if gaps[k]>=max(9,2.0*float(np.median(gaps))):
            at=(populated[k]+populated[k+1])//2
            if imbalance_limit*total <= cumulative[at] <= (1-imbalance_limit)*total:
                return float((edges[populated[k]]+edges[populated[k+1]])/2)
    ranked=eligible[np.argsort(smooth[eligible])]
    for ix in ranked:
        # A shallow notch in a uniform surface distribution is not evidence of a layer.
        window=np.r_[smooth[max(0,ix-35):max(0,ix-10)],smooth[min(n_bins,ix+10):min(n_bins,ix+35)]]
        typical=float(np.median(window)) if len(window) else 0.
        if typical>0 and smooth[ix] <= .48*typical:
            return float((edges[ix]+edges[ix+1])/2)
    return None


def _station_groups(positions, weights, tol):
    import numpy as np
    pos=np.asarray(positions,float);w=np.asarray(weights,float)
    if len(pos)==0:return (np.array([]),np.array([]))
    index=np.rint(pos/tol).astype(np.int64)
    unique,reversed_idx=np.unique(index,return_inverse=True)
    areas=np.bincount(reversed_idx,weights=w,minlength=len(unique))
    weighted=np.bincount(reversed_idx,weights=pos*w,minlength=len(unique))
    return weighted/areas,areas


def _decode_pair_stations(xpos, areas, *, min_plates=5):
    """Detect paired wafer front/back planes and current nonuniform gaps.

    Reject unrelated X wall stations rather than silently discarding an outlier.
    """
    import numpy as np
    x=np.asarray(xpos,float);a=np.asarray(areas,float)
    if len(x)<min_plates*2:raise GeometryAmbiguous('Too few repeated X wall stations for wafer front/back pairing')
    order=np.argsort(x);x=x[order];a=a[order]
    # Restrict to repeated, similarly sized wafer faces; isolate broad end walls.
    med=float(np.median(a))
    keep=(a>=.28*med)&(a<=3.8*med)
    x=x[keep];a=a[keep]
    if len(x)<min_plates*2:raise GeometryAmbiguous('Wafer station areas are not sufficiently repetitive')
    dx=np.diff(x)
    if np.any(dx<=0):raise GeometryAmbiguous('Duplicate/invalid wafer wall stations')
    thin=float(np.median(np.sort(dx)[:max(2,len(dx)//3)]))
    thick=float(np.quantile(dx,.75))
    if not 0<thin<thick*.82:
        raise GeometryAmbiguous('Cannot distinguish plate thickness from open wafer gaps')
    # Each x0/x1 pair is the two physical faces of *one* wafer. A single
    # missing face breaks strict pairing and requires geometry review.
    close=np.isclose(dx,thin,rtol=.26,atol=max(1e-9,thin*.025))
    if len(x)%2:
        raise GeometryAmbiguous('Odd count of repeated wall-face stations')
    if not np.all(close[::2]) or np.any(close[1::2]):
        raise GeometryAmbiguous('Wafer front/back station order is not alternating')
    lo=x[::2]; hi=x[1::2]; gaps=lo[1:]-hi[:-1]
    if len(gaps)==0 or np.any(gaps<=0):raise GeometryAmbiguous('Missing/negative wafer gaps')
    regular_median=float(np.median(gaps))
    # Wide gaps may be central voids. Narrow gaps might be staggered features
    # or manufacturing exceptions: do not call them regular by fiat.
    regular=(gaps>=.65*regular_median)&(gaps<=1.40*regular_median)
    if int(regular.sum())<max(2,int(len(gaps)*.55)):
        raise GeometryAmbiguous('Too many irregular gaps; a single wafer pitch is not defensible')
    if np.any(gaps<regular_median*.65):
        raise GeometryAmbiguous('Unclassified narrow wafer gaps; manual geometry review required')
    return {'face_positions_lo_m':lo.tolist(),'face_positions_hi_m':hi.tolist(),
            'plate_count':len(lo),'plate_thickness_m':float(np.median(hi-lo)),
            'gap_width_m':gaps.tolist(),'regular_gap_index':np.flatnonzero(regular).tolist(),
            'wide_gap_index':np.flatnonzero(gaps>regular_median*1.40).tolist(),
            'narrow_gap_index':np.flatnonzero(gaps<regular_median*.65).tolist(),
            'reference_gap_m':regular_median}


def _wall_geometry(xyz, face_nodes, wall_ids, *, max_wall_faces=2500000):
    """Build only bounded wall triangle arrays; never materialize full face meshes."""
    import numpy as np
    ids=np.asarray(wall_ids,dtype=np.int64)
    if len(ids)<50 or len(ids)>max_wall_faces:
        raise GeometryAmbiguous('Wall-face count outside safe identification budget')
    v=np.asarray(xyz,dtype=float); conn=np.asarray(face_nodes,dtype=np.int64)
    if conn.ndim!=2 or conn.shape[1]!=3:raise GeometryAmbiguous('Only triangular wall faces can be recognized')
    if np.any(ids<0) or ids.max()>=len(conn) or conn[ids].min()<0 or conn[ids].max()>=len(v):
        raise GeometryAmbiguous('Wall-face node index mismatch')
    triangles=v[conn[ids]]
    bbox=np.ptp(v,axis=0);scale=float(np.linalg.norm(bbox))
    tol=max(2e-8,scale*2e-7)
    xflat=(np.ptp(triangles[:,:,0],axis=1)<tol)
    if xflat.sum()<50:raise GeometryAmbiguous('No repetitive planar wall faces normal to X')
    t=triangles[xflat];center=t.mean(axis=1)
    area=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1)*.5
    valid=np.isfinite(area)&(area>0)
    if valid.sum()<50:raise GeometryAmbiguous('Planar wall area is unavailable')
    return t[valid],center[valid],area[valid],tol


def _patch_centroid_split(patches, *, axis, min_panels, tol):
    """Detect two repeated spatial bands without weighting broad support walls.

    Use a genuine gap between connected patch centroids and require many panels
    on either side; the separation must be material compared to panel width.
    """
    import numpy as np
    index=0 if axis=='y' else 1
    vals=np.asarray([p['centroid_yz_m'][index] for p in patches],float)
    if len(vals)<2*min_panels:return None
    order=np.argsort(vals)
    sorted_vals=vals[order]
    gaps=np.diff(sorted_vals)
    if not len(gaps):return None
    width=np.median([p[axis][1]-p[axis][0] for p in patches])
    eligible=np.arange(min_panels-1,len(gaps)-min_panels+1)
    if not len(eligible):return None
    k=int(eligible[np.argmax(gaps[eligible])])
    if gaps[k]<=max(tol*10,width*.4):return None
    return float((sorted_vals[k]+sorted_vals[k+1])*.5)


def identify_from_wall_triangles(triangles, *, min_plates=5, require_two_layers=False):
    """Auto-recognize genuine connected wafer panels, not X positions alone.

    Separate panel centroids define candidate rows and exclusive cut ownership.
    """
    import numpy as np
    from af_wafer_patches import (extract_patches,pair_wafer_patches,_infer_thickness,
                                  PatchRecognitionError)
    v=np.asarray(triangles,dtype=float)
    if v.ndim!=3 or v.shape[1:]!=(3,3) or len(v)<32 or not np.isfinite(v).all():
        raise GeometryAmbiguous('Expected real, finite X-normal wall triangles')
    scale=float(np.linalg.norm(np.ptp(v.reshape(-1,3),axis=0)))
    tol=max(2e-8,scale*2e-7)
    flat=np.ptp(v[:,:,0],axis=1)<tol
    v=v[flat];ctr=v.mean(axis=1)
    area=np.linalg.norm(np.cross(v[:,1]-v[:,0],v[:,2]-v[:,0]),axis=1)*.5
    good=np.isfinite(area)&(area>0)
    v=v[good];ctr=ctr[good];area=area[good]
    if len(v)<min_plates*4:
        raise GeometryAmbiguous('Too few measured X-normal wafer wall triangles')
    # Patch identities, rather than unrelated triangle counts, provide the
    # layer/row hypotheses. A broad X-normal chamber end wall must not shift
    # the histogram or enlarge the measured wafer array's Z bounds.
    search_diag={'mode':'automatic'}
    try:
        physical_patches=extract_patches(v,tol=tol,y_range=(-float('inf'),float('inf')),
                                         diagnostics=search_diag)
    except PatchRecognitionError as exc:
        raise GeometryAmbiguous('Automatic candidate extraction: '+str(exc),
                                stage=exc.stage,diagnostics=exc.diagnostics) from exc
    patch_areas=np.asarray([p['area'] for p in physical_patches])
    reference_area=float(np.median(patch_areas))
    panel_size=np.asarray([[p[ax][1]-p[ax][0] for ax in ('y','z')] for p in physical_patches])
    median_size=np.median(panel_size,axis=0)
    same_size=np.all((panel_size>=median_size*.65)&(panel_size<=median_size*1.40),axis=1)
    plausible=[p for p,keep in zip(physical_patches,same_size)
               if keep and reference_area*.55<=p['area']<=reference_area*1.65]
    search_diag['area_rejected_patch_count']=len(physical_patches)-len(plausible)
    search_diag['candidate_patch_count']=len(plausible)
    if len(plausible)<2*min_plates:
        raise GeometryAmbiguous('No repeating wafer-sized wall patches after broad-wall filter',
                                stage='wall_candidate_screening',diagnostics=search_diag)
    py=np.asarray([p['centroid_yz_m'][0] for p in plausible]);pa=np.asarray([p['area'] for p in plausible])
    split_y=_patch_centroid_split(plausible,axis='y',min_panels=min_plates*2,tol=tol)
    if require_two_layers and split_y is None:
        raise GeometryAmbiguous('Unable to distinguish two wafer layers from connected panels',
                                stage='layer_assignment',diagnostics=search_diag)
    ybands=[(float('-inf'),split_y),(split_y,float('inf'))] if split_y is not None else [(float('-inf'),float('inf'))]
    layers=[]
    for ya,yb in ybands:
        diagnostic={'layer_id':len(layers)+1,
                    'y_range_m':[float(v) if math.isfinite(v) else None for v in (ya,yb)],
                    'global_patch_screening':search_diag}
        patches=[p for p in plausible if ya<=p['centroid_yz_m'][0]<yb]
        diagnostic['connected_patch_count']=len(patches)
        if len(patches)<min_plates*2:
            raise GeometryAmbiguous('Auto layer has too few connected wall panels',
                                    stage='layer_assignment',diagnostics=diagnostic)
        row_patches=patches
        if not row_patches:
            raise GeometryAmbiguous('Auto layer contains no repeating wafer-sized panels',
                                    stage='row_assignment',diagnostics=diagnostic)
        # Only measured, plausible panel centers contribute to the two-row
        # hypothesis; a separate chamber end wall cannot expand a Z bound.
        zmid=np.asarray([p['centroid_yz_m'][1] for p in row_patches])
        za=np.asarray([p['area'] for p in row_patches])
        split_z=_patch_centroid_split(row_patches,axis='z',min_panels=min_plates*2,tol=tol)
        flux_split=None
        if split_z is None:
            widths=np.asarray([p['z'][1]-p['z'][0] for p in row_patches])
            m=float(np.median(widths))
            median=float(np.median(zmid));low=zmid[zmid<median];high=zmid[zmid>median]
            if len(low)>=min_plates*2 and len(high)>=min_plates*2:
                if float(np.median(high)-np.median(low))>m*.40:
                    raise GeometryAmbiguous('Auto row projections overlap or have no unique Z split; provide row ranges and overlap_split_z_m',
                                            stage='row_assignment',diagnostics=diagnostic)
            row_ranges=[(float(min(p['z'][0] for p in row_patches)),
                         float(max(p['z'][1] for p in row_patches)))]
        else:
            lower=[p for p in row_patches if p['centroid_yz_m'][1]<split_z]
            upper=[p for p in row_patches if p['centroid_yz_m'][1]>=split_z]
            if len(lower)<min_plates*2 or len(upper)<min_plates*2:
                raise GeometryAmbiguous('Auto Z split has too few real wall panels',
                                        stage='row_assignment',diagnostics=diagnostic)
            lo=(min(p['z'][0] for p in lower),max(p['z'][1] for p in lower))
            hi=(min(p['z'][0] for p in upper),max(p['z'][1] for p in upper))
            if lo[1]>=hi[0]:
                if not hi[0]<split_z<lo[1]:
                    raise GeometryAmbiguous('Row split is outside the observed overlap',
                                            stage='row_assignment',diagnostics=diagnostic)
                flux_split=split_z
            row_ranges=[lo,hi]
        diagnostic['hypothesized_row_z_ranges_m']=[[float(a),float(b)] for a,b in row_ranges]
        try:
            thickness=_infer_thickness(patches,tol)
            rows,evidence=pair_wafer_patches(patches,thickness_range=thickness,
                                             row_ranges=row_ranges,min_plates=min_plates,
                                             diagnostics=diagnostic)
        except PatchRecognitionError as exc:
            raise GeometryAmbiguous('Auto layer wafer pairing: '+str(exc),
                                    stage=exc.stage,diagnostics={**diagnostic,**exc.diagnostics}) from exc
        ylo=min(r['y_min_m'] for r in rows);yhi=max(r['y_max_m'] for r in rows)
        layers.append({'id':len(layers)+1,'rows':rows,'y_cut_m':(ylo+yhi)/2.,
                       'overlap_split_z_m':flux_split,
                       'y_min_m':ylo,'y_max_m':yhi,
                       'channels':sum(len(r['regular_gap_index']) for r in rows),
                       'wafer_pairing_diagnostics':{**evidence,'pipeline':diagnostic}})
    if len(layers)==2:
        low,high=layers
        if not low['y_cut_m']+tol*4<split_y<high['y_cut_m']-tol*4:
            raise GeometryAmbiguous('Auto layer measurement heights are not separated by the measured centroid split',
                                    stage='layer_assignment',
                                    diagnostics={'split_y_m':split_y,'candidate_cuts_y_m':[low['y_cut_m'],high['y_cut_m']]})
        if low['y_max_m']>high['y_min_m']+tol*4:
            for layer in layers:
                layer['wafer_pairing_diagnostics']['vertical_projection_overlap']=True
    count=sum(sum(r['plate_count'] for r in l['rows']) for l in layers)
    channels=sum(l['channels'] for l in layers)
    if channels<2:raise GeometryAmbiguous('No validated measured wafer gaps')
    return {'schema':'autofluent.geometry_auto.v1','status':'candidate_needs_flux_validation',
            'assumptions':['Measured connected wall patches and thickness pairing',
                           'X-normal wafers and Y-flow',
                           'Unique face ownership; overlapping rows use a measured centroid split'],
            'layer_split_y_m':split_y,'total_layers':len(layers),
            'total_plates':count,'total_channels':channels,'layers':layers}


def partition_cut_faces(points, signed_flux, layer, *, min_faces_per_channel=1):
    """Non-overlapping four-way classification on a native Y staircase cut.

    Two rows may have *different* X plate coordinates (staggered arrays).
    Every cut face is assigned once. Narrow/unknown gaps are not wafer gaps.
    """
    import numpy as np
    ctr=np.asarray(points,dtype=float)
    q=np.asarray(signed_flux,dtype=float).reshape(-1)
    if ctr.ndim!=2 or ctr.shape[1]!=3 or len(ctr)!=len(q) or not np.isfinite(q).all():
        raise ValueError('Cut face and flux lengths or numeric data are invalid')
    if not layer['rows']:raise GeometryAmbiguous('Layer has no recognized wafer rows')
    # start with all faces not passing between regular wafer plates
    wafer=np.zeros(len(q),dtype=bool)
    center=np.zeros(len(q),dtype=bool)
    central_x=np.zeros(len(q),dtype=bool)
    central_z=np.zeros(len(q),dtype=bool)
    channels=[]
    split=layer.get('overlap_split_z_m')
    if split is not None and len(layer['rows'])!=2:
        raise GeometryAmbiguous('Overlap split requires two verified wafer rows')
    for row_id,row in enumerate(layer['rows']):
        zband=(ctr[:,2]>=row['z_min_m'])&(ctr[:,2]<=row['z_max_m'])
        if split is not None:
            # A single geometric face belongs to exactly one measured row.
            # The split is an explicit approximation inside the overlap.
            zband &= (ctr[:,2]<split) if row_id==0 else (ctr[:,2]>=split)
        lo=np.asarray(row['face_positions_lo_m']);hi=np.asarray(row['face_positions_hi_m'])
        if len(lo)<2:raise GeometryAmbiguous('Wafer row has no plate gap')
        ix=np.searchsorted(lo[1:],ctr[:,0],side='right')
        safe=np.minimum(ix,len(lo)-2)
        in_gap=zband & (ix<len(lo)-1) & (ctr[:,0]>=hi[safe]) & (ctr[:,0]<=lo[safe+1])
        for i in row['regular_gap_index']:
            mask=in_gap&(ix==i)
            if np.count_nonzero(mask)<min_faces_per_channel:
                raise GeometryAmbiguous('No native faces cover recognized channel row %d gap %d' % (row_id,i))
            if np.any(wafer&mask):raise GeometryAmbiguous('Overlapping wafer channel masks')
            wafer|=mask
            row_zmin=row['z_min_m'] if split is None or row_id==0 else max(row['z_min_m'],split)
            row_zmax=row['z_max_m'] if split is None or row_id==1 else min(row['z_max_m'],split)
            channels.append({'row':row_id+1,'gap_index':int(i),
                             'channel_id':'R%d-G%d'%(row_id+1,int(i)+1),
                             'x_center_m':float((hi[i]+lo[i+1])*0.5),
                             'z_center_m':float((row_zmin+row_zmax)*0.5),
                             'x_range_m':[float(hi[i]),float(lo[i+1])],
                             'z_range_m':[float(row_zmin),float(row_zmax)],
                             'face_count':int(mask.sum()),
                             'net_kg_s':float(q[mask].sum(dtype=np.float64)),
                             'positive_kg_s':float(q[mask&(q>0)].sum(dtype=np.float64)),
                             'negative_magnitude_kg_s':float(-q[mask&(q<0)].sum(dtype=np.float64))})
        # Wide gaps and unclassified narrow gaps are excluded from wafer CV;
        # their flow is labelled as unclassified central candidates for review.
        central_x|=in_gap&~wafer
    if len(layer['rows'])==2:
        lower_row,upper_row=layer['rows']
        midpoint=split if split is not None else (lower_row['z_max_m']+upper_row['z_min_m'])/2
        # The passage BETWEEN the two Z rows is central only where BOTH
        # rows have actual wafer-array X coverage. This also handles staggered
        # arrays without inventing a central passage beyond one row's end.
        # Previous code used Z alone, misclassifying X-end bypass faces.
        overlap_lo=max(row['face_positions_lo_m'][0] for row in layer['rows'])
        overlap_hi=min(row['face_positions_hi_m'][-1] for row in layer['rows'])
        if not overlap_hi>overlap_lo:
            raise GeometryAmbiguous('Two Z rows have no common wafer-array X span')
        central_z|=((ctr[:,2]>lower_row['z_max_m']) &
                    (ctr[:,2]<upper_row['z_min_m']) &
                    (ctr[:,0]>=overlap_lo) & (ctr[:,0]<=overlap_hi))
    else:
        midpoint=float(np.median(ctr[:,2]))
    if layer.get('central_bypass')=='absent':
        central_x[:]=False
        central_z[:]=False
    central_x&=~wafer
    central_z&=~wafer
    if np.any(central_x&central_z):
        raise GeometryAmbiguous('X/Z central bypass subregions overlap')
    center=central_x|central_z
    outer=~(wafer|center)
    left=outer&(ctr[:,2]<midpoint)
    right=outer&~left
    if np.any((wafer.astype(int)+center+left+right)!=1):
        raise RuntimeError('Region masks are not exclusive')
    # For staggered rows, an X-end passage exists outside the *local row's*
    # wafer span even when it falls inside the other row's global X extent.
    # Likewise, the open Z passage beyond the common overlap is not central.
    x_min=min(row['face_positions_lo_m'][0] for row in layer['rows'])
    x_max=max(row['face_positions_hi_m'][-1] for row in layer['rows'])
    end_x=outer&((ctr[:,0]<x_min)|(ctr[:,0]>x_max))
    for row in layer['rows']:
        in_row=(ctr[:,2]>=row['z_min_m'])&(ctr[:,2]<=row['z_max_m'])
        local_end=(ctr[:,0]<row['face_positions_lo_m'][0]) | (ctr[:,0]>row['face_positions_hi_m'][-1])
        end_x|=outer&in_row&local_end
    if len(layer['rows'])==2:
        between=(ctr[:,2]>layer['rows'][0]['z_max_m'])&(ctr[:,2]<layer['rows'][1]['z_min_m'])
        common_lo=max(row['face_positions_lo_m'][0] for row in layer['rows'])
        common_hi=min(row['face_positions_hi_m'][-1] for row in layer['rows'])
        end_x|=outer&between&((ctr[:,0]<common_lo)|(ctr[:,0]>common_hi))
    total=float(q.sum(dtype=np.float64));parts={k:float(q[m].sum(dtype=np.float64)) for k,m in
        (('wafer',wafer),('central',center),('outer_low_z',left),('outer_high_z',right))}
    if abs(sum(parts.values())-total)>max(1e-13,abs(total)*1e-8):
        raise RuntimeError('Native cut-face partition does not close')
    # Net-only closure can conceal large and mutually cancelling recirculation.
    # Preserve positive and reverse integrals for every mutually exclusive
    # partition and demand BOTH directional totals close independently.
    def directional(mask):
        value=q[mask]
        positive=float(value[value>0].sum(dtype=np.float64))
        reverse=float(-value[value<0].sum(dtype=np.float64))
        return {'face_count':int(mask.sum()),'positive_kg_s':positive,
                'negative_magnitude_kg_s':reverse,'net_kg_s':positive-reverse}
    exclusive_masks={'wafer':wafer,'central':center,'outer_low_z':left,'outer_high_z':right}
    directional_parts={name:directional(mask) for name,mask in exclusive_masks.items()}
    full_positive=float(q[q>0].sum(dtype=np.float64))
    full_reverse=float(-q[q<0].sum(dtype=np.float64))
    tolerance=max(1e-13,float(np.abs(q).sum(dtype=np.float64))*1e-8)
    if abs(sum(x['positive_kg_s'] for x in directional_parts.values())-full_positive)>tolerance or (            abs(sum(x['negative_magnitude_kg_s'] for x in directional_parts.values())-full_reverse)>tolerance):
        raise RuntimeError('Positive/reverse cut-face partition does not close')
    bypass_subregions={'central_x_wide_gap':central_x,'central_z_between_rows':central_z,
                       'outer_x_end_subset':end_x}
    directional_bypass={name:directional(mask) for name,mask in bypass_subregions.items()}
    net=np.array([x['net_kg_s'] for x in channels]);mean=float(net.mean()) if len(net) else 0.
    cv=100*float(net.std())/abs(mean) if abs(mean)>1e-30 else None
    return {'status':'candidate_native_cut_partition','channel_count':len(channels),
            'channels':channels,'parts_net_kg_s':parts,'whole_cut_kg_s':total,
            'parts_directional_kg_s':directional_parts,
            'bypass_directional_kg_s':directional_bypass,
            'cv_pct':cv,'positive_kg_s':full_positive,
            'negative_magnitude_kg_s':full_reverse,
            'closure_error_kg_s':sum(parts.values())-total,
            'central_x_wide_gap_kg_s':float(q[central_x].sum(dtype=np.float64)),
            'central_z_between_rows_kg_s':float(q[central_z].sum(dtype=np.float64)),
            'outer_x_end_subset_kg_s':float(q[end_x].sum(dtype=np.float64)),
            'central_bypass_applicability':'not_applicable' if layer.get('central_bypass')=='absent' else 'candidate',
            'overlap_split_z_m':split,
            'partition_note':'Every cut face has one owner; overlapping rows use explicit Z split approximation. X end remains an outer subset, not added twice; serial cuts are not additive'}


def _pair_stations_then_assign_rows(ctr, area, selected_layer, row_cfgs,
                                    tol, thickness_range, *, min_plates=5):
    """Attribute a whole measured wafer to one row, NEVER a wall face alone.

    First match two measured X-normal surfaces by physical thickness, then
    determine row ownership from the combined two surfaces' non-overlap Z
    evidence. A surface may not be assigned to two wafers. Ambiguous pairs and
    coincident two-row candidates fail closed. Array-external X walls should
    already have been filtered by the enclosing layer's wafer_x_range_m.
    """
    import numpy as np
    ids=np.flatnonzero(selected_layer)
    bins=np.rint(ctr[ids,0]/tol).astype(np.int64)
    unique,inverse=np.unique(bins,return_inverse=True)
    weights=area[ids]
    areas=np.bincount(inverse,weights=weights,minlength=len(unique))
    pos=np.bincount(inverse,weights=weights*ctr[ids,0],minlength=len(unique))/areas
    # Unique exterior bands permit ownership even when the full wafers overlap.
    evidence=[]
    for i,r in enumerate(row_cfgs):
        za,zb=r['z_range_m']
        z=ctr[ids,2]
        unique_z=(z>=za)&(z<=zb)
        for j,other in enumerate(row_cfgs):
            if i!=j:
                oa,ob=other['z_range_m']
                unique_z &= (z<oa)|(z>ob)
        evidence.append(np.bincount(inverse,weights=weights*unique_z,minlength=len(unique)))
    evidence=np.asarray(evidence).T
    low,high=thickness_range
    order=np.argsort(pos)
    pos=pos[order];unique=unique[order];areas=areas[order];evidence=evidence[order]
    # Suppress wall planes that cannot be actual repeated wafer stations. This
    # is only a candidate filter; identified wafer counts remain measured.
    median_area=float(np.median(areas)) if len(areas) else 0
    kept=(areas>=.23*median_area)&(areas<=3.5*median_area)
    pos=pos[kept];unique=unique[kept];areas=areas[kept];evidence=evidence[kept]
    conflict=(evidence.min(axis=1)>evidence.max(axis=1)*.05)&(evidence.max(axis=1)>0)
    if np.any(conflict):
        raise GeometryAmbiguous('X=%.9g m has evidence for both overlapping rows; connected-surface review required' % pos[int(np.flatnonzero(conflict)[0])])
    if len(pos)<4*min_plates:
        raise GeometryAmbiguous('Too few paired-wall candidates after configured layer and X bounds')
    pairs=[];orphans=[];used=set();candidate_edges=[]
    # Thickness edges are built before allocating row ownership. Adjacent
    # interleaved stations are NOT by themselves evidence of a wafer gap.
    for i in range(len(pos)):
        for j in range(i+1,len(pos)):
            delta=pos[j]-pos[i]
            if delta>high+tol:break
            if delta<low-tol:continue
            # Strong, contradictory exterior evidence proves different rows.
            ei,ej=evidence[i],evidence[j]
            if ei.max()>0 and ej.max()>0 and np.argmax(ei)!=np.argmax(ej):
                if min(ei.max(),ej.max())>10*max(ei.min(),ej.min(),1e-30):
                    continue
            candidate_edges.append((i,j))
    # Exact disjoint station pairing. Ambiguous matching is never silently
    # resolved using expected counts or historic pitch values.
    neighbors={i:[] for i in range(len(pos))}
    for i,j in candidate_edges:
        neighbors[i].append(j);neighbors[j].append(i)
    for i,j in candidate_edges:
        if len(neighbors[i])!=1 or len(neighbors[j])!=1:
            raise GeometryAmbiguous('Ambiguous front/back wafer pairing near X=%.9g m; multiple thickness-compatible stations'%(pos[i]))
        if i not in used and j not in used:
            combined=evidence[i]+evidence[j]
            if combined.max()<=0:
                raise GeometryAmbiguous('No row-specific wafer edge evidence near X=%.9g m'%(pos[i]))
            if combined.min()>=combined.max()*.05:
                raise GeometryAmbiguous('X=%.9g m has evidence for both overlapping rows; connected-surface review required'%(pos[i]))
            row=int(np.argmax(combined))
            pairs.append((i,j,row))
            used.add(i);used.add(j)
    orphans=[float(pos[i]) for i in range(len(pos)) if i not in used]
    if len(pairs)<min_plates*len(row_cfgs):
        raise GeometryAmbiguous('Insufficient confirmed wafer pairs; excluded isolated stations=%d, first=%s' %
                                (len(orphans),orphans[:3]))
    owner_bins=[[],[]]
    for i,j,row in pairs:
        owner_bins[row].extend([int(unique[i]),int(unique[j])])
    if any(len(b)<min_plates*2 for b in owner_bins):
        raise GeometryAmbiguous('One staggered row lacks enough fully paired wafers; cannot infer missing positions')
    return owner_bins,{'candidate_station_count':int(len(pos)),
                       'paired_wafer_count':len(pairs),
                       'excluded_isolated_station_count':len(orphans),
                       'first_excluded_station_x_m':orphans[:8],
                       'ownership_method':'paired_front_back_planes_then_combined_unique_Z_exterior_evidence'}


def identify_from_configured_wall_triangles(triangles, config, *, min_plates=5):
    """Measure actual wafers by connected wall patches before classifying rows.

    Config bounds reject unrelated surfaces; expected counts verify results but
    never create measured wafer positions. This entry supports both aligned and
    staggered X stations, including coincident aligned X for disjoint rows.
    """
    import numpy as np
    from af_wafer_patches import extract_patches, pair_wafer_patches, PatchRecognitionError
    v=np.asarray(triangles,dtype=float)
    if v.ndim!=3 or v.shape[1:]!=(3,3) or not np.isfinite(v).all():
        raise GeometryAmbiguous('Invalid wall triangles for configured geometry')
    scale=float(np.linalg.norm(np.ptp(v.reshape(-1,3),axis=0)))
    tol=max(2e-8,scale*2e-7)
    measured=[]
    for n,layer_cfg in enumerate(sorted(config['layers'],key=lambda d:d['y_range_m'][0])):
        ya,yb=layer_cfg['y_range_m']
        row_cfgs=sorted(layer_cfg['rows'],key=lambda d:d['z_range_m'][0])
        x_range=layer_cfg.get('wafer_x_range_m')
        overlap=(len(row_cfgs)==2 and row_cfgs[0]['z_range_m'][1]>row_cfgs[1]['z_range_m'][0])
        diagnostic={'layer_id':layer_cfg['id'],'y_range_m':[ya,yb],
                    'configured_x_range_m':x_range,'row_z_ranges_m':[r['z_range_m'] for r in row_cfgs]}
        try:
            patches=extract_patches(v,tol=tol,y_range=(ya,yb),x_range=x_range,
                                    diagnostics=diagnostic)
            rows,evidence=pair_wafer_patches(
                patches,thickness_range=config['wafer']['thickness_range_m'],
                row_ranges=[r['z_range_m'] for r in row_cfgs],
                regular_gap_range=config['wafer']['regular_gap_range_m'],
                x_range=x_range,min_plates=min_plates,diagnostics=diagnostic)
        except PatchRecognitionError as exc:
            raise GeometryAmbiguous('Layer %s: %s' %(layer_cfg['id'],exc),
                                    stage=exc.stage,diagnostics={**diagnostic,**exc.diagnostics}) from exc
        for j,(row,row_cfg) in enumerate(zip(rows,row_cfgs)):
            row.update(id=row_cfg['id'],configured_z_search_range_m=row_cfg['z_range_m'],
                       paired_station_evidence=evidence)
        if len(rows)==2:
            left=np.asarray(rows[0]['face_positions_lo_m'])
            right=np.asarray(rows[1]['face_positions_lo_m'])
            if config['wafer']['arrangement']=='aligned':
                if len(left)!=len(right) or not np.allclose(left,right,rtol=0.,atol=max(tol*5,1e-6)):
                    raise GeometryAmbiguous('Layer %s: aligned two-row model has different measured axial wafer positions' %layer_cfg['id'])
        ylo=min(r['y_min_m'] for r in rows);yhi=max(r['y_max_m'] for r in rows)
        if not ya<=ylo<=yhi<=yb:
            raise GeometryAmbiguous('Measured wafer bounds outside configured layer %s' %layer_cfg['id'])
        ycut=layer_cfg.get('cut_y_m')
        if ycut is None:ycut=(ylo+yhi)/2.
        if not ylo<ycut<yhi:
            raise GeometryAmbiguous('Requested cut outside measured wafer faces in layer %s' %layer_cfg['id'])
        measured.append({'id':n+1,'configured_id':layer_cfg['id'],'rows':rows,
                         'y_cut_m':float(ycut),'y_min_m':ylo,'y_max_m':yhi,
                         'channels':sum(len(r['regular_gap_index']) for r in rows),
                         'overlap_split_z_m':config.get('partition',{}).get('overlap_split_z_m') if overlap else None,
                         'central_bypass':config.get('partition',{}).get('central_bypass','auto'),
                         'configured_y_search_range_m':[ya,yb],
                         'configured_x_search_range_m':x_range,
                         'wafer_pairing_diagnostics':{**evidence,'pipeline':diagnostic}})
    wafer_count=sum(sum(row['plate_count'] for row in layer['rows']) for layer in measured)
    channel_count=sum(layer['channels'] for layer in measured)
    for label,actual in (('wafer_count',wafer_count),('channel_count',channel_count)):
        reference=config.get('expected',{}).get(label)
        if reference is not None and reference!=actual:
            raise GeometryAmbiguous('Configured expected.%s=%s disagrees with identified geometry=%s'
                                    %(label,reference,actual),stage='expected_model_validation',
                                    diagnostics={'actual_wafer_count':wafer_count,'actual_channel_count':channel_count,
                                                 'expected':config.get('expected',{})})
    return {'schema':'autofluent.geometry_configured.v1','status':'candidate_needs_flux_validation',
            'assumptions':['Measured connected X-normal wall panels',
                           'One-to-one front/back wafer pairs before row assignment',
                           'X-normal wafers and Y-flow tetra mesh',
                           'Native face centroid Z partition is approximate and exclusive'],
            'total_layers':len(measured),'total_plates':wafer_count,
            'total_channels':channel_count,'layers':measured,
            'arrangement':config['wafer']['arrangement'],
            'classification':'configured_real_wall_geometry'}
