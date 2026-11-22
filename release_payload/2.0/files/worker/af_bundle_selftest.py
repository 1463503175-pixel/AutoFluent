
import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import itertools,json,sys
from pathlib import Path
import numpy as np
def build_synthetic_ald_pair(root, *, nplates=8, staggered=False, two_layers=True):
    import h5py
    root=Path(root)
    nodes=[];faces={};cell_vel=[];wafer_sites=set()
    # Each Z row may have its own X station phase (staggered packing).
    for layer in range(2 if two_layers else 1):
        ylo=-.45+layer*.23;yhi=ylo+.13
        ymid=(ylo+yhi)/2
        for row in range(2):
            zlo=-.3+row*.28;zhi=zlo+.12
            plates=np.arange(nplates,dtype=float)*.085
            plates[nplates//2:]+=.06
            plates+=(row*.017 if staggered else 0)
            lo=plates;hi=plates+.009
            wafer_sites.update(float(p) for p in np.r_[lo,hi])
            gaps=[(lo[0]-.070,lo[0],'outer')]
            for i in range(nplates-1):
                gaps.append((hi[i],lo[i+1],str(i)))
            gaps.append((hi[-1],hi[-1]+.070,'outer'))
            for gapid,(x0,x1,tag) in enumerate(gaps):
                # Single prism, two conforming vertical slabs.
                base=len(nodes)
                grid={}
                for ix,x in enumerate((x0,x1)):
                    for iy,y in enumerate((ylo,ymid,yhi)):
                        for iz,z in enumerate((zlo,zhi)):
                            grid[(ix,iy,iz)]=len(nodes)
                            nodes.append((x,y,z))
                speed=1.e-4*(1+.065*gapid+.04*row+.03*layer)
                for slab in (0,1):
                    for perm in itertools.permutations(range(3)):
                        unit=[0,0,0]
                        path=[tuple(unit)]
                        for axis in perm:
                            unit[axis]=1;path.append(tuple(unit))
                        verts=tuple(grid[(ix,iy+slab,iz)] for ix,iy,iz in path)
                        cid=len(cell_vel)+1
                        cell_vel.append((0.,-speed,0.))
                        for face in itertools.combinations(verts,3):
                            key=tuple(sorted(face))
                            if key in faces:
                                assert faces[key]['c1']==0
                                faces[key]['c1']=cid
                            else:
                                faces[key]={'vertices':key,'c0':cid,'c1':0,'speed':speed,
                                            'opposite':next(v for v in verts if v not in key)}
    xyz=np.array(nodes,float)
    by_zone={k:[] for k in ('wall','end','interior','inlet','outlet')}
    for row in faces.values():
        v=xyz[list(row['vertices'])]
        if row['c1']>0:
            zone='interior'
        elif np.ptp(v[:,1])<1e-10:
            zone='inlet' if v[0,1]>-.4 and abs(v[0,1]-(.13-.45+.23))<1e-10 else 'outlet'
            # Use absolute boundary layer y, not one global top/bottom.
            if abs(v[0,1]-(-.45+.13))<1e-10 or abs(v[0,1]-(-.45+.23+.13))<1e-10:
                zone='inlet'
            elif abs(v[0,1]-(-.45))<1e-10 or abs(v[0,1]-(-.45+.23))<1e-10:
                zone='outlet'
            else:
                raise AssertionError('Unexpected unpaired horizontal tetra boundary')
        elif np.ptp(v[:,0])<1e-10:
            # The outside X boundary is not a wafer plate; keep it separate.
            x=v[0,0]
            zone='wall' if any(abs(float(x)-site)<1e-9 for site in wafer_sites) else 'end'
        else:
            zone='wall'
        by_zone[zone].append(row)
    # For clean single-block CFF, each zone is a contiguous Face ID interval.
    face_rows=[];ranges=[]
    for name in ('wall','end','interior','inlet','outlet'):
        start=len(face_rows)+1
        face_rows.extend(by_zone[name]);end=len(face_rows)
        assert end>=start
        ranges.append((name,start,end))
    tri=np.array([r['vertices'] for r in face_rows],int)
    owners=np.array([r['c0'] for r in face_rows],int)
    neigh=np.array([r['c1'] for r in face_rows],int)
    flux=[]
    for r in face_rows:
        pts=xyz[list(r['vertices'])]
        vec=np.cross(pts[1]-pts[0],pts[2]-pts[0])*.5
        toward_opposite=xyz[r['opposite']]-pts.mean(axis=0)
        if np.dot(vec,toward_opposite)>0:vec=-vec
        flux.append(float(np.dot(vec,cell_vel[r['c0']-1])))
    nf=len(face_rows)
    case=root/'synthetic.cas.h5';data=root/'synthetic.dat.h5'
    with h5py.File(case,'w') as h:
        h.create_dataset('meshes/1/nodes/coords/4',data=xyz)
        h.create_dataset('meshes/1/faces/nodes/1/nodes',data=(tri+1).reshape(-1))
        h.create_dataset('meshes/1/faces/nodes/1/nnodes',data=[3]*nf)
        h.create_dataset('meshes/1/faces/c0/1',data=owners)
        internal=np.flatnonzero(neigh>0)
        assert len(internal)>0 and np.array_equal(internal,np.arange(internal[0],internal[-1]+1))
        c1=h.create_dataset('meshes/1/faces/c1/1',data=neigh[internal])
        c1.attrs['minId']=[int(internal[0]+1)];c1.attrs['maxId']=[int(internal[-1]+1)]
        h.create_dataset('meshes/1/cells/zoneTopology/maxId',data=[len(cell_vel)])
        zone=h.require_group('meshes/1/faces/zoneTopology')
        zone.create_dataset('name',data=[';'.join(r[0] for r in ranges).encode()])
        zone.create_dataset('minId',data=[r[1] for r in ranges])
        zone.create_dataset('maxId',data=[r[2] for r in ranges])
    with h5py.File(data,'w') as h:
        h.create_dataset('settings/Case File',data=[b'synthetic.cas.h5'])
        ds=h.create_dataset('results/1/phase-1/faces/SV_FLUX/1',data=np.array(flux))
        ds.attrs['minId']=[1];ds.attrs['maxId']=[nf]
        for field,values in [('SV_P',np.full(nf,100.)),('SV_DENSITY',np.ones(nf)),
                             ('SV_U',np.zeros(nf)),('SV_V',np.full(nf,-.0001)),('SV_W',np.zeros(nf))]:
            ds=h.create_dataset('results/1/phase-1/faces/'+field+'/1',data=values)
            ds.attrs['minId']=[1];ds.attrs['maxId']=[nf]
    return case,data


if __name__=='__main__':
    import h5py
    from af_adaptive_native import try_adaptive_native
    from af_flow_diagnostics import pressure_statistics,driving_summary
    root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
    if (root/'synthetic.cas.h5').exists() or (root/'synthetic.dat.h5').exists():
        raise FileExistsError('Self-test output already contains a saved pair; choose an empty output directory')
    case,data=build_synthetic_ald_pair(root,nplates=8)
    result=try_adaptive_native(case,data,root/'native_reports')
    assert result['status']=='adaptive_native_candidate_computed',result.get('detail')
    assert result['metrics']['geometry']['total_channels']==24
    assert 'afReportData' in Path(result['html_file']).read_text(encoding='utf-8')
    assert all(abs(l['net_kg_s']-sum(c['net_kg_s'] for c in l['channels']))<1e-14 for l in result['metrics']['layers'].values())
    stats=pressure_statistics(np.array([2.,-1.]),*[np.array([p,p]) for p in (2.,2.,3.,3.)])
    assert stats['total_pa']==3. and stats['reverse_kg_s']==1.
    import contextlib,io
    import unittest,af_bundle_diagnostics_selftest
    check=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(af_bundle_diagnostics_selftest))
    assert check.wasSuccessful(), 'Embedded diagnostic self-tests failed'
    import af_bundle_species_selftest
    species_check=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(af_bundle_species_selftest))
    assert species_check.wasSuccessful(), 'Embedded species self-tests failed'
    assert '混合气体组分与暴露证据' in Path(result['html_file']).read_text(encoding='utf-8')
    base=Path(__file__).resolve().parents[1]
    assert Path(sys.executable).resolve().is_relative_to(base)
    assert Path(np.__file__).resolve().is_relative_to(base)
    assert Path(h5py.__file__).resolve().is_relative_to(base)
    print(json.dumps({'status':'single_exe_selftest_passed','synthetic_only':True,
        'python':sys.executable,'numpy':np.__version__,'h5py':h5py.__version__,
        'channel_count':24,'diagnostic_tests':check.testsRun,'html_file':result['html_file'],'cache_base':str(base)},ensure_ascii=False))
