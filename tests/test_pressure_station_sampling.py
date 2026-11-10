import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_adaptive_native import analyze_saved_pair
from af_model_config import load_model_config
from test_adaptive_full_cff import build_synthetic_ald_pair


def test_real_cell_pressure_station_reads_only_selected_cells(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    cfg=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    cfg['measurement']['pressure_stations']=[
        {'id':'inlet_plenum','y_m':-.42,'thickness_m':.035},
        {'id':'outlet_plenum','y_m':-.36,'thickness_m':.035}]
    nc=0
    with h5py.File(case,'r') as f:
        nc=int(f['meshes/1/cells/zoneTopology/maxId'][:].max())
    with h5py.File(data,'r+') as f:
        ds=f.create_dataset('results/1/phase-1/cells/SV_P/1',data=np.full(nc,103.25))
        ds.attrs['minId']=[1];ds.attrs['maxId']=[nc]
    result=analyze_saved_pair(case,data,config=cfg)
    stations=result['pressure']['stations']
    assert len(stations)==2
    assert all(v['status']=='computed_arithmetic_cell_sample' for v in stations)
    assert all(v['mean_pa']==pytest.approx(103.25) for v in stations)
    from af_report_v3 import render_native_report
    html=render_native_report(result)
    assert 'inlet_plenum' in html and 'outlet_plenum' in html


def test_missing_cells_pressure_is_not_inferred(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    cfg=load_model_config(ROOT/'templates/geometry_config_example_staggered.json')
    cfg['measurement']['pressure_stations']=[{'id':'sample','y_m':-.40,'thickness_m':.03}]
    result=analyze_saved_pair(case,data,config=cfg)
    assert result['pressure']['stations'][0]['status']=='not_available'
    assert result['layers']['lower']['channel_count']>0
