import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_geometry_scout import scan_wall_stations


def test_axis_station_scan_returns_candidates_not_flux(tmp_path):
    h5py=pytest.importorskip('h5py');np=pytest.importorskip('numpy')
    p=tmp_path/'test.cas.h5'
    xyz=np.array([[0,0,0],[0,1,0],[0,0,1],[0,1,1],
                  [1,0,0],[1,1,0],[1,0,1],[1,1,1]],float)
    tris=np.array([[1,2,3],[2,3,4],[5,6,7],[6,7,8]],int)
    with h5py.File(p,'w') as f:
        f.create_dataset('meshes/1/nodes/coords/4',data=xyz)
        f.create_dataset('meshes/1/faces/nodes/1/nodes',data=tris.reshape(-1))
        f.create_dataset('meshes/1/faces/nodes/1/nnodes',data=[3]*4)
        z=f.require_group('meshes/1/faces/zoneTopology')
        z.create_dataset('name',data=[b'wall'])
        z.create_dataset('minId',data=[1])
        z.create_dataset('maxId',data=[4])
    data=scan_wall_stations(p)
    assert data['status']=='candidate_wall_station_histograms'
    assert data['not_physical_result'] is True
    x=data['geometry_axes'][0]
    assert x['station_count']==2
    assert x['large_wall_station_count']==2


def test_scout_gracefully_skips_missing_mesh(tmp_path):
    h5py=pytest.importorskip('h5py')
    p=tmp_path/'blank.h5'
    with h5py.File(p,'w') as f:f.create_dataset('abc',data=[1])
    result=scan_wall_stations(p)
    assert result['status']=='unavailable'
    assert result['not_physical_result']
