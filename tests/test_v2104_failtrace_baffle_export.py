"""Counterexamples: physical patches, baffle wall rings, partial export safety."""
import json,sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_wafer_patches import extract_patches,PatchRecognitionError
from af_adaptive_geometry import identify_from_configured_wall_triangles, GeometryAmbiguous
from af_baffle_openings import find_planar_openings,match_flux_components
from test_v211_overlap_and_pairing import _config,_overlapping_wafer_triangles
from af_model_config import validate_model_config


def plate(y, holes=(),n=8):
    tris=[]
    for i in range(n):
        for j in range(n):
            if any(a<=i<b and c<=j<d for a,b,c,d in holes):continue
            x0,x1=i*.01,(i+1)*.01;z0,z1=j*.01,(j+1)*.01
            a=[x0,y,z0];b=[x1,y,z0];c=[x1,y,z1];d=[x0,y,z1]
            tris.extend([[a,b,c],[a,c,d]])
    return np.asarray(tris,float)


def test_wafer_screening_reports_counts_and_rejected_external_surface():
    from test_adaptive_geometry import rectangles
    triangles=rectangles(n=8,layers=1)
    outside=rectangles(n=1,layers=1,z_rows=1) + np.array([[[2.,0,0]]])
    diag={};all_tri=np.concatenate([triangles,outside])
    patches=extract_patches(all_tri,tol=1e-7,y_range=(-1,1),x_range=(-.01,.8),diagnostics=diag)
    assert diag['raw_wall_triangle_count']==len(all_tri)
    assert diag['spatially_filtered_triangle_count']==len(triangles)
    assert len(patches)==32
    assert diag['connected_patch_count']==32


def test_wafer_true_collision_has_staged_coordinate_and_reason():
    c=_config();v=_overlapping_wafer_triangles();v[32:,:,0]-=.03
    with pytest.raises(GeometryAmbiguous) as err:
        identify_from_configured_wall_triangles(v,validate_model_config(c))
    exc=err.value
    assert exc.stage=='row_assignment'
    assert exc.diagnostics['first_conflict_x_m']==pytest.approx(0)
    assert exc.diagnostics['rejected_by']=='physically_overlapping_wafer_panels'
    assert exc.diagnostics['connected_patch_count']==32


def test_aligned_two_rows_same_x_remain_separate_when_spatially_disjoint():
    from test_adaptive_geometry import rectangles
    v=rectangles(n=8,layers=1,z_rows=2,wide_gap=False,shifted=False)
    # The automatic path must not collapse two rows at one X station.
    from af_adaptive_geometry import identify_from_wall_triangles
    geo=identify_from_wall_triangles(v)
    assert geo['total_plates']==16 and geo['total_channels']==14


def test_two_candidate_baffle_plates_do_not_silently_select_one(monkeypatch):
    import af_adaptive_native as native
    # Two thin plate pairs over two separate floor stations: both plausible.
    tr=np.concatenate([plate(y) for y in (-.1,-.08,-.079,-.07,-.05,-.049)])
    monkeypatch.setattr(native,'_horizontal_wall_triangles',lambda mesh:tr)
    result=native._baffle_plenum_candidates({'xyz':np.array([[0,-.1,0],[.08,-.049,.08]])},None,None)
    assert result['status']=='plate_candidate_ambiguous'
    assert len(result['diagnostics']['plate_candidates'])>=2
    assert 'baffle_y_range_m' in result['reason']


def _piece(x,z,q,*,width=.003):
    return {'center_m':[x,-.05,z],'width_x_m':width,'width_z_m':width,
            'net_kg_s':q,'positive_kg_s':max(0.,q),
            'negative_magnitude_kg_s':max(0.,-q),'face_count':2}


def test_opening_contours_merge_split_pieces_and_keep_edge_flux():
    t=plate(0,((2,4,2,4),(5,7,5,7)))
    r=find_planar_openings(t,0,1e-6)
    assert r['status']=='closed_contours'
    assert len(r['openings'])==2
    pieces=[_piece(.026,.03,1e-7),_piece(.034,.03,2e-7),
            _piece(.06,.06,4e-7),_piece(.09,.09,3e-7)]
    flow=match_flux_components(r['openings'],r['outers'],pieces,
                               full_cut_net_kg_s=1e-6)
    assert flow['status']=='verified_openings'
    assert [len(h['component_ids']) for h in flow['holes']]==[2,1]
    assert flow['edge_net_kg_s']==pytest.approx(3e-7)
    assert flow['hole_net_kg_s']==pytest.approx(7e-7)
    assert flow['closure_error_kg_s']==pytest.approx(0)


def test_unassigned_inside_solid_plate_prevents_false_hole_cv():
    r=find_planar_openings(plate(0,((2,4,2,4),)),0,1e-6)
    flow=match_flux_components(r['openings'],r['outers'],[_piece(.03,.03,2e-7),_piece(.055,.055,1e-7)],
                               full_cut_net_kg_s=3e-7)
    assert flow['status']=='candidate_unverified'
    assert flow['diagnostics']['verified_hole_count'] is None
    assert flow['unassigned_net_kg_s']==pytest.approx(1e-7)
    assert flow['closure_error_kg_s']==pytest.approx(0)


def test_html_render_failure_keeps_full_json_recoverable(tmp_path,monkeypatch):
    from test_adaptive_full_cff import build_synthetic_ald_pair
    import af_adaptive_native,af_report_v3,af_render_saved
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    original=af_report_v3.render_native_report
    with monkeypatch.context() as m:
        m.setattr(af_report_v3,'render_native_report',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Rendering failure')))
        value=af_adaptive_native.try_adaptive_native(case,data,tmp_path/'out')
    assert value['status']=='computed_export_failed'
    assert value['metrics']['status']=='adaptive_native_candidate_computed'
    assert Path(value['json_file']).exists()
    assert not value.get('html_file')
    assert 'Rendering failure' in value['export_error']['reason']
    html=af_render_saved.render_stored_json(value['json_file'])
    assert '仿真结果与结构优化' in html


def test_baffle_end_to_end_uses_real_outlines_not_fragment_count(monkeypatch):
    """Two physical apertures, three native cut pieces + board edge: 2 holes."""
    import af_adaptive_native as native
    wall=np.concatenate([plate(-.100),
                         plate(-.080,((2,4,2,4),(5,7,5,7))),
                         plate(-.079,((2,4,2,4),(5,7,5,7)))])
    monkeypatch.setattr(native,'_horizontal_wall_triangles',lambda mesh:wall)
    # Four disconnected native triangles: two fragments of hole 1, one in
    # hole 2, and one outside the physical plate boundary.
    pieces=[(.026,.030,1e-7),(.034,.030,2e-7),(.060,.060,4e-7),(.090,.090,3e-7)]
    nodes=[];conn=[]
    for x,z,_ in pieces:
        base=len(nodes)
        nodes.extend([(x-.001,-.0795,z-.001),(x+.001,-.0795,z-.001),(x,-.0795,z+.001)])
        conn.append([base,base+1,base+2])
    mesh={'xyz':np.asarray(nodes,dtype=float),'conn':np.asarray(conn,dtype=int),
          'wall_ids':np.asarray([],dtype=int),'inside':np.arange(4),
          'owner':np.ones(4,dtype=int),'neighbors':np.full(4,2,dtype=int),
          'flux':np.asarray([p[2] for p in pieces]),'result_group':'1',
          'phase_group':'phase-1','nc':2}
    centroids=np.asarray([[0,0,0],[0,-.081,0],[0,-.078,0]],float)
    result=native._baffle_plenum_candidates(mesh,centroids,{})
    assert result['status']=='geometry_candidate_holes_native_flux_computed'
    assert result['hole_count']==2
    assert result['candidate_integral_component_count']==4
    assert result['board_cut_closure_error_kg_s']==pytest.approx(0)
    assert result['board_edge_net_kg_s']==pytest.approx(-3e-7)
    assert result['total_hole_net_kg_s']==pytest.approx(-7e-7)
    assert any(len(h['integration_component_ids'])==2 for h in result['holes'])
