# -*- coding: utf-8 -*-
"""Geometry-first planar baffle openings, independent from staircase flux pieces.

Only an explicitly closed ring of wall-face boundary edges can represent an
opening. A connected cut component never, by itself, certifies a physical hole.
Topology mismatch is diagnostic; unassigned face flux is retained for closure.
"""
from collections import Counter, defaultdict
import numpy as np


def _inside(point, polygon):
    x,z=map(float,point); ring=np.asarray(polygon, dtype=float)
    result=False
    for (x0,z0),(x1,z1) in zip(ring,np.roll(ring,-1,axis=0)):
        if (z0>z)!=(z1>z) and x<(x1-x0)*(z-z0)/(z1-z0)+x0:
            result=not result
    return result


def _ring_area(points):
    p=np.asarray(points,dtype=float)
    return float(.5*np.sum(p[:,0]*np.roll(p[:,1],-1)-np.roll(p[:,0],-1)*p[:,1]))


def find_planar_openings(triangles, y_m, tol, *, max_triangles=650000):
    """Closed coplanar plate wall boundaries; odd-depth rings are apertures."""
    tris=np.asarray(triangles,dtype=float)
    keep=(np.ptp(tris[:,:,1],axis=1)<=tol)&(np.abs(tris[:,:,1].mean(axis=1)-y_m)<=tol*2)
    tris=tris[keep]
    diag={'plane_y_m':float(y_m),'plane_triangle_count':int(len(tris))}
    if len(tris)<4:
        return {'status':'not_identified','reason':'Insufficient coplanar plate wall triangles', 'diagnostics':diag}
    if len(tris)>max_triangles:
        return {'status':'resource_limit','reason':'Selected plate exceeds safe planar contour budget', 'diagnostics':diag}
    yz=np.rint(tris[:,:,[0,2]].reshape(-1,2)/tol).astype(np.int64)
    unique,ids=np.unique(yz,axis=0,return_inverse=True)
    edge_count=Counter()
    for row in ids.reshape(-1,3):
        for a,b in ((0,1),(1,2),(2,0)):
            u,v=sorted((int(row[a]),int(row[b])))
            if u!=v:edge_count[(u,v)]+=1
    if any(v>2 for v in edge_count.values()):
        return {'status':'ambiguous','reason':'Non-manifold plate plane edge', 'diagnostics':diag}
    borders=[e for e,n in edge_count.items() if n==1]
    graph=defaultdict(set)
    for a,b in borders:graph[a].add(b);graph[b].add(a)
    diag['boundary_edge_count']=len(borders)
    if not borders or any(len(n)!=2 for n in graph.values()):
        return {'status':'ambiguous','reason':'Plate wall has open or branched contour; hole boundaries unverified', 'diagnostics':diag}
    unused=set(borders);rings=[]
    while unused:
        a,b=next(iter(unused));ring=[a];prev=a;curr=b
        unused.remove((min(a,b),max(a,b)))
        for _ in range(len(graph)+1):
            if curr==a:break
            ring.append(curr)
            nxt=next((j for j in graph[curr] if j!=prev),None)
            if nxt is None:break
            edge=(min(curr,nxt),max(curr,nxt))
            if edge not in unused:break
            unused.remove(edge);prev,curr=curr,nxt
        if curr!=a:
            return {'status':'ambiguous','reason':'Incomplete plate boundary ring', 'diagnostics':diag}
        polygon=unique[np.asarray(ring,dtype=int)].astype(float)*tol
        if abs(_ring_area(polygon))>tol*tol*10:rings.append(polygon)
    if not rings:
        return {'status':'not_identified','reason':'No closed plate ring contours','diagnostics':diag}
    # A ring is an aperture if it is inside exactly one solid outer contour.
    areas=[abs(_ring_area(r)) for r in rings]
    openings=[];outers=[];depths=[]
    for i,polygon in enumerate(rings):
        p=polygon[0]
        depth=sum(_inside(p,other) for j,other in enumerate(rings)
                  if j!=i and areas[j]>areas[i])
        depths.append(depth)
        box=[float(polygon[:,0].min()),float(polygon[:,0].max()),
             float(polygon[:,1].min()),float(polygon[:,1].max())]
        record={'outline_xz_m':polygon.tolist(),'bbox_xz_m':box,
                'center_xz_m':[(box[0]+box[1])*.5,(box[2]+box[3])*.5],
                'boundary_area_m2':float(areas[i])}
        if depth==1:openings.append(record)
        elif depth==0:outers.append(record)
    openings.sort(key=lambda r:(r['center_xz_m'][1],r['center_xz_m'][0]))
    for i,h in enumerate(openings,1):h['hole_id']=f'H{i:04d}'
    diag.update(boundary_ring_count=len(rings),outer_loop_count=len(outers),
                opening_loop_count=len(openings),nested_ring_depths=depths)
    if not openings or not outers:
        return {'status':'not_identified','reason':'Cannot confirm both plate exterior and enclosed aperture rings',
                'openings':openings,'outers':outers,'diagnostics':diag}
    return {'status':'closed_contours','openings':openings,'outers':outers,'diagnostics':diag}


def match_flux_components(openings,outers,components, *, full_cut_net_kg_s, tolerance=1e-9):
    """Map each integral piece to exactly one physical aperture or else retain it.

    Coordinates are observations of the native staircase faces; rectangular
    bounds are a conservative first test, not a replacement for wall outlines.
    """
    holes=[{**h,'component_ids':[],'net_kg_s':0.,'positive_kg_s':0.,
            'negative_magnitude_kg_s':0.,'face_count':0} for h in openings]
    edge=[];unassigned=[]
    for i,component in enumerate(components):
        p=component['center_m'];x,z=float(p[0]),float(p[2])
        cx0,cx1=x-component['width_x_m']/2,x+component['width_x_m']/2
        cz0,cz1=z-component['width_z_m']/2,z+component['width_z_m']/2
        possible=[]
        for j,h in enumerate(holes):
            a,b,c,d=h['bbox_xz_m']
            if (a-tolerance<=x<=b+tolerance and c-tolerance<=z<=d+tolerance and
                    cx0>=a-max(tolerance,(b-a)*.16) and cx1<=b+max(tolerance,(b-a)*.16) and
                    cz0>=c-max(tolerance,(d-c)*.16) and cz1<=d+max(tolerance,(d-c)*.16)):
                possible.append(j)
        entry={**component,'component_index':i+1}
        if len(possible)==1:
            hole=holes[possible[0]];hole['component_ids'].append(i+1)
            for key in ('net_kg_s','positive_kg_s','negative_magnitude_kg_s'):
                hole[key]+=float(component.get(key,0.))
            hole['face_count']+=int(component.get('face_count',0))
        elif len(possible)>1:
            entry['reason']='component_overlaps_multiple_apertures';unassigned.append(entry)
        elif any(_inside((x,z),ring['outline_xz_m']) for ring in outers):
            entry['reason']='inside_plate_but_no_verified_opening';unassigned.append(entry)
        else:
            entry['reason']='outside_verified_plate_outline';edge.append(entry)
    from math import fsum
    assigned=fsum(float(h['net_kg_s']) for h in holes)
    edge_net=fsum(float(h['net_kg_s']) for h in edge)
    unmatched=fsum(float(h['net_kg_s']) for h in unassigned)
    closure=assigned+edge_net+unmatched-float(full_cut_net_kg_s)
    verified=(all(h['component_ids'] for h in holes) and not unassigned and
              abs(closure)<=max(1e-12,abs(full_cut_net_kg_s)*1e-8))
    return {'status':'verified_openings' if verified else 'candidate_unverified',
            'holes':holes,'board_edge_components':edge,'unassigned_components':unassigned,
            'whole_cut_net_kg_s':float(full_cut_net_kg_s),
            'hole_net_kg_s':assigned,'edge_net_kg_s':edge_net,
            'unassigned_net_kg_s':unmatched,'closure_error_kg_s':closure,
            'diagnostics':{'verified_hole_count':len(holes) if verified else None,
                           'opening_contour_count':len(holes),
                           'integral_piece_count':len(components),
                           'unassigned_piece_count':len(unassigned),
                           'split_piece_hole_count':sum(len(h['component_ids'])>1 for h in holes)}}
