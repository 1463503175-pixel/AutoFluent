import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_adaptive_geometry import identify_from_wall_triangles, partition_cut_faces, GeometryAmbiguous


def rectangles(*, n=8, wide_gap=True, shifted=False, layers=2, z_rows=2):
    triangles=[]
    lower_x=np.arange(n,dtype=float)*.082
    for lev in range(layers):
        ylo=.04+lev*.18
        yhi=ylo+.11
        for side in range(z_rows):
            zlo=.12+side*.24
            zhi=zlo+.10
            xs=lower_x+(side*.018 if shifted else 0)
            if wide_gap:
                xs=xs.copy();xs[n//2:]+=.06
            for x0 in xs:
                for x in (x0,x0+.008):
                    corners=np.array([[x,ylo,zlo],[x,yhi,zlo],[x,yhi,zhi],[x,ylo,zhi]])
                    triangles.extend([corners[[0,1,2]],corners[[0,2,3]]])
    return np.asarray(triangles)


def test_auto_detect_layers_rows_no_fixed_count():
    result=identify_from_wall_triangles(rectangles(n=8))
    assert result['total_layers']==2
    assert result['total_plates']==32
    assert result['total_channels']==24  # 8 plates, 7 gaps, one wide per row
    for layer in result['layers']:
        assert len(layer['rows'])==2
        assert layer['channels']==12
        for row in layer['rows']:
            assert row['plate_thickness_m']==pytest.approx(.008)
            assert len(row['wide_gap_index'])==1


def test_auto_detect_staggered_rows_and_unequal_flow_partition():
    layer=identify_from_wall_triangles(rectangles(n=6,wide_gap=False,shifted=True,layers=1))['layers'][0]
    assert layer['channels']==10
    z0,z1=layer['rows']
    assert z1['face_positions_lo_m'][0]-z0['face_positions_lo_m'][0]==pytest.approx(.018)
    pts=[]; qs=[]
    for row in layer['rows']:
        for i in row['regular_gap_index']:
            pts.append([sum((row['face_positions_hi_m'][i],row['face_positions_lo_m'][i+1]))/2,
                        layer['y_cut_m'],(row['z_min_m']+row['z_max_m'])/2]); qs.append(1e-7*(i+1))
    pts.extend([[.12,layer['y_cut_m'],(z0['z_max_m']+z1['z_min_m'])/2],
                [-.1,layer['y_cut_m'],z0['z_min_m']-.01],
                [.6,layer['y_cut_m'],z1['z_max_m']+.01]])
    qs.extend([4e-7,5e-7,-2e-7])
    data=partition_cut_faces(pts,qs,layer)
    assert data['channel_count']==10
    assert data['parts_net_kg_s']['central']==pytest.approx(4e-7)
    assert data['parts_net_kg_s']['outer_low_z']==pytest.approx(5e-7)
    assert data['parts_net_kg_s']['outer_high_z']==pytest.approx(-2e-7)
    assert data['closure_error_kg_s']==pytest.approx(0)


def test_refuse_missing_wafer_plane_pair():
    tr=rectangles(n=8,layers=1)
    tr=tr[~np.isclose(tr[:,:,0].mean(axis=1),.008)]
    with pytest.raises(GeometryAmbiguous):identify_from_wall_triangles(tr)


def test_invalid_flux_partition_rejected():
    layer=identify_from_wall_triangles(rectangles(n=6,layers=1))['layers'][0]
    with pytest.raises(ValueError):partition_cut_faces(np.zeros((3,3)),[1,2,float('nan')],layer)


def test_central_x_and_z_bypass_are_disjoint_and_end_is_subset():
    layer=identify_from_wall_triangles(rectangles(n=8,layers=1))['layers'][0]
    row0,row1=layer['rows']
    i=row0['wide_gap_index'][0]
    pts=[[.5*(row0['face_positions_hi_m'][i]+row0['face_positions_lo_m'][i+1]),layer['y_cut_m'],
          (row0['z_min_m']+row0['z_max_m'])/2],
         [.2,layer['y_cut_m'],(row0['z_max_m']+row1['z_min_m'])/2],
         [-.2,layer['y_cut_m'],row0['z_min_m']-.02]]
    flux=[2.e-7,3.e-7,4.e-7]
    for row in layer['rows']:
        for gap in row['regular_gap_index']:
            pts.append([.5*(row['face_positions_hi_m'][gap]+row['face_positions_lo_m'][gap+1]),
                        layer['y_cut_m'],.5*(row['z_min_m']+row['z_max_m'])]);flux.append(1.e-7)
    p=partition_cut_faces(pts,flux,layer)
    assert p['central_x_wide_gap_kg_s']==pytest.approx(2.e-7)
    assert p['central_z_between_rows_kg_s']==pytest.approx(3.e-7)
    assert p['parts_net_kg_s']['central']==pytest.approx(5.e-7)
    assert p['outer_x_end_subset_kg_s']==pytest.approx(4.e-7)
    assert p['closure_error_kg_s']==pytest.approx(0.)
