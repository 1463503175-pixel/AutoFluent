"""End-to-end **synthetic** CFF with genuine tetra face topology and known flux.

This intentionally does NOT stand in for a real Fluent 11.9 save. Every tetra
is built from a conforming hexa-to-six-tetra subdivision; signed SV_FLUX is
computed from outward oriented triangular areas and known downward velocity.
"""
import itertools
from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_adaptive_native import analyze_saved_pair,try_adaptive_native


def build_synthetic_ald_pair(root, *, nplates=8, staggered=False, two_layers=True):
    h5py=pytest.importorskip('h5py')
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


def test_synthetic_full_cff_pipeline_recomputes_wafer_and_bypass(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    result=analyze_saved_pair(case,data)
    assert result['status']=='adaptive_native_candidate_computed'
    assert result['geometry']['total_layers']==2
    assert result['geometry']['total_channels']==24
    for layer in result['layers'].values():
        assert layer['channel_count']==12
        assert layer['net_kg_s']>0
        assert layer['cv_pct']>0
        assert layer['flux_control_volume']['algebraic_closure_error_kg_s']==pytest.approx(0,abs=1e-12)
        assert layer['partition_net_kg_s']['central']>0
    assert result['port_flux']['mass_imbalance_pct']==pytest.approx(0,abs=1e-10)


def test_synthetic_staggered_saved_pair_real_json_html_csv(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    outcome=try_adaptive_native(case,data,tmp_path/'results')
    assert outcome['status']=='adaptive_native_candidate_computed',outcome
    import json
    metrics=json.loads(Path(outcome['json_file']).read_text())
    assert metrics['geometry']['total_channels']==24
    assert '中央旁路' in Path(outcome['html_file']).read_text(encoding='utf-8')
    assert '仿真结果与结构优化' in Path(outcome['html_file']).read_text(encoding='utf-8')
    assert '1. 入口气体去了哪里' in Path(outcome['html_file']).read_text(encoding='utf-8')
    assert '候选' in Path(outcome['html_file']).read_text(encoding='utf-8')
    assert '中央 X/Z 子旁路正反向流量' in Path(outcome['html_file']).read_text(encoding='utf-8')
    assert len(Path(outcome['csv_file']).read_text(encoding='utf-8-sig').splitlines())==25


def test_real_cli_entry_and_compare_direct_native_results(tmp_path):
    import json,subprocess
    import af_compare
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    code=Path(__file__).resolve().parents[1]/'worker/af_h5_offline.py'
    proc=subprocess.run([sys.executable,str(code),'--file',str(case),
                         '--output-dir',str(tmp_path/'data_index')],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        text=True,timeout=30,check=True)
    payload=json.loads(proc.stdout)
    assert payload['physical_analysis_status']=='adaptive_native_candidate_computed',payload
    assert Path(payload['analysis_json']).is_file()
    assert Path(payload['html_file']).is_file()
    assert Path(payload['native_csv']).is_file()
    # Comparing two copies of directly computed native-H5 metrics must not
    # accidentally invoke the historical-Codex HTML/CSV import route.
    native=af_compare.read_limited(payload['analysis_json'])
    assert native['schema']=='autofluent.adaptive_native.v1'
    from af_adaptive_compare import compare_native_saved
    result=compare_native_saved(native,native)
    assert result['status']=='provisional_only_not_qualified'
    assert result['geometry_signature_equal']
    assert result['layers']['lower']['cv_delta_pp']==pytest.approx(0)


def test_skip_pyfluent_does_not_block_raw_h5_native_algorithm(tmp_path):
    import json,subprocess
    case,_=build_synthetic_ald_pair(tmp_path,nplates=8)
    code=Path(__file__).resolve().parents[1]/'worker/af_h5_offline.py'
    result=subprocess.run([sys.executable,str(code),'--file',str(case),'--skip-pyfluent',
                           '--output-dir',str(tmp_path/'output')],
                           text=True,capture_output=True,timeout=30,check=True)
    payload=json.loads(result.stdout)
    assert payload['physical_analysis_status']=='adaptive_native_candidate_computed'


def test_section_offset_sensitivity_is_recorded_or_explicitly_unavailable(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    out=analyze_saved_pair(case,data)
    for layer in out['layers'].values():
        state=layer['cut_height_sensitivity']
        assert state['status'] in ('stable_within_1pp_advisory_only',
                                    'sensitive_candidate_needs_review',
                                    'unable_to_validate_shifted_cut',
                                    'unavailable_layer_geometry')
        assert state['offset_m']==pytest.approx(.001)
    assert out['provenance']['case_data_pair_independently_verified'] is False
    assert 'basename' in out['provenance']['case_data_identity_evidence']


def test_cut_sensitivity_respects_extra_pass_budget(tmp_path):
    from af_adaptive_native import _read_cff,_cell_centroids,_region_flow,_cut_height_sensitivity
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    cf,df,mesh=_read_cff(case,data)
    try:
        from af_adaptive_geometry import identify_from_wall_triangles
        geo=identify_from_wall_triangles(mesh['xyz'][mesh['conn'][mesh['wall_ids']]])
        centroids=_cell_centroids(mesh)
        layer=geo['layers'][0]
        base,_,_,_=_region_flow(mesh,centroids,layer,None)
        status=_cut_height_sensitivity(mesh,centroids,layer,base,1e-5,face_budget=1)
        assert status['status']=='skipped_resource_budget'
    finally:
        cf.close();df.close()


def test_native_report_publish_keeps_verified_json_if_html_replace_fails(tmp_path,monkeypatch):
    import os,json
    import af_adaptive_native
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    out=tmp_path/'native_reports';out.mkdir()
    original_replace=os.replace
    def fail_html_commit(source,destination,*args,**kwargs):
        if str(destination).endswith('.html'):
            raise OSError('Simulated disk/report publish failure')
        return original_replace(source,destination,*args,**kwargs)
    monkeypatch.setattr(af_adaptive_native.os,'replace',fail_html_commit)
    got=af_adaptive_native.try_adaptive_native(case,data,out)
    assert got['status']=='computed_export_failed'
    assert got['computed_status']=='adaptive_native_candidate_computed'
    assert 'publish failure' in got['export_error']['reason']
    assert Path(got['json_file']).is_file()
    assert Path(got['csv_file']).is_file()
    assert got.get('html_file') is None
    stored=json.loads(Path(got['json_file']).read_text('utf-8'))
    assert stored['geometry']['total_channels']==24
    assert sorted(p.suffix for p in out.iterdir())==['.csv','.json']
