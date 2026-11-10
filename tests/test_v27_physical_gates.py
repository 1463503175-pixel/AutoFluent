"""V2.7 regression: prove error detection and native-face numerical oracle.

All CFF fixtures here are synthetic, not a real Fluent saved case.
"""
import csv
import os
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_adaptive_native import analyze_saved_pair,UnsupportedCFF
from af_adaptive_geometry import identify_from_wall_triangles,partition_cut_faces
from af_native_profile import parse_native_csv
from test_adaptive_full_cff import build_synthetic_ald_pair
from test_adaptive_geometry import rectangles


def test_all_external_faces_audited_and_clean_synthetic_case(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    result=analyze_saved_pair(case,data)
    flux=result['port_flux']
    assert flux['external_face_audit']=='checked_all_non_inlet_outlet_external_faces'
    assert flux['other_external_absolute_flux_kg_s']==pytest.approx(0,abs=1e-14)
    assert flux['other_external_face_count']>0
    assert result['provenance']['input_files_unchanged_during_analysis'] is True


def test_external_opening_which_is_not_a_named_port_must_block_physical_result(tmp_path):
    h5py=pytest.importorskip('h5py')
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    with h5py.File(data,'r+') as h:
        arr=h['results/1/phase-1/faces/SV_FLUX/1']
        # First face is an exterior wall, not inlet or outlet.  This models
        # an unnamed vent or incorrect wall boundary with appreciable flow.
        arr[0]=3e-6
    with pytest.raises(UnsupportedCFF,match='non-inlet/outlet boundary flux'):
        analyze_saved_pair(case,data)


def test_change_to_saved_file_during_read_is_not_reported_as_static_snapshot(tmp_path,monkeypatch):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8)
    import af_adaptive_native as native
    original=native._total_pressure
    def change_metadata(*args,**kwargs):
        record=case.stat()
        # Does not mutate data or topology, but proves we detect an input
        # being overwritten/replaced during the analysis window.
        os.utime(case,ns=(record.st_atime_ns,record.st_mtime_ns+2_000_000_000))
        return original(*args,**kwargs)
    monkeypatch.setattr(native,'_total_pressure',change_metadata)
    with pytest.raises(UnsupportedCFF,match='replaced or modified'):
        native.analyze_saved_pair(case,data)


def test_native_face_flux_matches_analytical_wafer_channel_flows_not_only_cv(tmp_path):
    case,data=build_synthetic_ald_pair(tmp_path,nplates=8,staggered=True)
    result=analyze_saved_pair(case,data)
    for key,layer in result['layers'].items():
        layer_index=0 if key=='lower' else 1
        stations=result['geometry']['layers'][layer_index]['rows']
        for ch in layer['channels']:
            row_index=ch['row']-1
            gap_index=ch['gap_index']
            row=stations[row_index]
            width=row['face_positions_lo_m'][gap_index+1]-row['face_positions_hi_m'][gap_index]
            span_z=row['z_max_m']-row['z_min_m']
            # The synthetic tetra mesh has piecewise uniform downward speed
            # chosen from its row/gap ID. The native-face integration should
            # reproduce mass flux across this rectangular gap EXACTLY.
            speed=1e-4*(1+.065*(gap_index+1)+.04*row_index+.03*layer_index)
            expected=speed*width*span_z
            assert ch['net_kg_s']==pytest.approx(expected,rel=1e-8,abs=1e-14)
            assert ch['negative_magnitude_kg_s']==pytest.approx(0.,abs=1e-14)


def test_staggered_row_local_x_end_is_counted_even_inside_global_x_span():
    geom=identify_from_wall_triangles(rectangles(n=6,wide_gap=False,shifted=True,layers=1))
    lay=geom['layers'][0]
    a,b=sorted(lay['rows'],key=lambda r:r['face_positions_lo_m'][0])
    assert b['face_positions_lo_m'][0]>a['face_positions_lo_m'][0]
    x=.5*(a['face_positions_lo_m'][0]+b['face_positions_lo_m'][0])
    z=.5*(b['z_min_m']+b['z_max_m'])
    points=[[x,lay['y_cut_m'],z]]
    flux=[4e-7]
    for row in lay['rows']:
        for i in row['regular_gap_index']:
            points.append([.5*(row['face_positions_hi_m'][i]+row['face_positions_lo_m'][i+1]),
                           lay['y_cut_m'],.5*(row['z_min_m']+row['z_max_m'])])
            flux.append(1e-7)
    values=partition_cut_faces(points,flux,lay)
    assert values['outer_x_end_subset_kg_s']==pytest.approx(4e-7)
    assert values['parts_net_kg_s']['central']==pytest.approx(0)


def test_reference_csv_fails_if_wall_flux_is_high_even_with_balanced_ports(tmp_path):
    source=ROOT/'tests/fixtures/native_11_9_27steps.csv'
    with source.open(encoding='utf-8-sig',newline='') as fp:
        reader=csv.DictReader(fp)
        fields=reader.fieldnames
        rows=list(reader)
    assert fields
    for row in rows:
        if row['区域']=='壁面通量检查':
            row['说明']='壁面绝对通量合计 1e-7 kg/s'
            break
    else: pytest.fail('Missing fixture wall row')
    target=tmp_path/'leaking.csv'
    with target.open('w',encoding='utf-8-sig',newline='') as fp:
        wr=csv.DictWriter(fp,fieldnames=fields)
        wr.writeheader();wr.writerows(rows)
    with pytest.raises(ValueError,match='wall/unnamed-boundary flux'):
        parse_native_csv(target)


def test_model_comparison_warns_when_inlet_flows_differ_and_no_convergence_evidence():
    from af_adaptive_compare import compare_native_saved
    def model(inlet,outlet,case):
        return {'schema':'autofluent.adaptive_native.v1',
                'provenance':{'case_path':case},
                'geometry':{'layers':[]},
                'port_flux':{'inlet_kg_s':inlet,'outlet_kg_s':outlet,
                             'mass_imbalance_pct':100*abs(inlet-outlet)/inlet},
                'pressure':{'status':'unavailable'},
                'layers':{'lower':{'channel_count':8,'cv_pct':2.5,
                                   'share_inlet_pct':70.,
                                   'partition_net_kg_s':{'wafer':7e-6,'central':1e-6,
                                                         'outer_low_z':1e-6,'outer_high_z':1e-6},
                                   'partition_directional_kg_s':{}}}}
    a=model(1e-5,1e-5,'a.cas.h5')
    b=model(1.2e-5,1e-5,'b.cas.h5')
    compare=compare_native_saved(a,b)
    assert compare['inlet_flow_relative_difference_pct']>10
    assert any('入口总净流量差异' in x for x in compare['reasons'])
    assert any('不平衡超过 1%' in x for x in compare['reasons'])
    assert compare['status']=='provisional_only_not_qualified'


def test_strict_11_9_wrapper_discards_output_when_input_changes(tmp_path, monkeypatch):
    import shutil
    import af_native_profile as native
    case=tmp_path/'a.cas.h5';data=tmp_path/'a.dat.h5'
    case.write_bytes(b'fake case fixture for runtime guard')
    data.write_bytes(b'fake data fixture for runtime guard')
    monkeypatch.setattr(native,'_preflight',lambda *args:(True,''))
    def changed_while_running(path,run_name):
        argv=sys.argv
        prefix=Path(argv[argv.index('--output')+1])
        shutil.copyfile(ROOT/'tests/fixtures/native_11_9_27steps.csv',str(prefix)+'.csv')
        Path(str(prefix)+'.html').write_text('synthetic',encoding='utf-8')
        st=case.stat()
        os.utime(case,ns=(st.st_atime_ns,st.st_mtime_ns+2_000_000_000))
    monkeypatch.setattr(native.runpy,'run_path',changed_while_running)
    result=native.try_reference_native(case,data,tmp_path/'reports')
    assert result['status']=='requires_geometry_calibration'
    assert result['failure_type']=='RuntimeError'
    assert not list((tmp_path/'reports').glob('*'))
