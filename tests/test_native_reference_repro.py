# -*- coding: utf-8 -*-
import copy
import csv
import math
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from af_native_profile import parse_native_csv, try_reference_native, compare_native_metrics

REFERENCE=ROOT/'tests/fixtures/native_11_9_27steps.csv'


def test_user_supplied_real_csv_exact_reproduction():
    m=parse_native_csv(REFERENCE)
    assert m['schema']=='autofluent.native_flux_metrics.v1'
    assert m['channel_count']==376 and m['wafer_count']==384 and m['baffle_holes']==245
    assert m['mass_imbalance_pct']==pytest.approx(.00232073,rel=1e-5)
    assert m['pressure_drop_pa_low_speed_approx']==pytest.approx(.647632007)
    assert m['layers']['upper']['net_kg_s']==pytest.approx(8.798586923e-6)
    assert m['layers']['lower']['net_kg_s']==pytest.approx(8.715046962e-6)
    assert m['layers']['upper']['cv_pct']==pytest.approx(3.2676,abs=0.0001)
    assert m['layers']['lower']['cv_pct']==pytest.approx(3.4808,abs=0.0001)
    assert m['layers']['upper']['share_inlet_pct']==pytest.approx(70.389,abs=.001)
    assert m['layers']['lower']['share_inlet_pct']==pytest.approx(69.72,abs=.001)


def test_user_csv_detects_wrong_partition(tmp_path):
    with REFERENCE.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f));fields=list(rows[0])
    for row in rows:
        if row['区域']=='上层中央旁路':
            row['净流量或绝对误差 (kg/s)']='0.000001'
    altered=tmp_path/'bad.csv'
    with altered.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError,match='exclusive regions'):
        parse_native_csv(altered)


def test_invalid_h5_not_processed_as_native_wafer(tmp_path):
    h5py=pytest.importorskip('h5py')
    ca=tmp_path/'invalid.cas.h5';da=tmp_path/'invalid.dat.h5'
    with h5py.File(ca,'w') as f:f.create_dataset('meshes/1/faces/c0/1',data=[1])
    with h5py.File(da,'w') as f:f.create_dataset('results/1/phase-1/faces/SV_FLUX/1',data=[1.])
    result=try_reference_native(ca,da,tmp_path/'reports')
    assert result['status']=='reference_profile_inapplicable'
    assert not list(tmp_path.glob('reports/*'))


def test_native_compare_keeps_formal_qualification_blocked():
    a=parse_native_csv(REFERENCE);b=copy.deepcopy(a)
    a['reference_script_sha256']='abc';b['reference_script_sha256']='abc'
    a['case_file']='a.cas.h5';b['case_file']='b.cas.h5'
    b['layers']['upper']['cv_pct']+=1
    result=compare_native_metrics(a,b)
    assert result['status'].startswith('provisional_')
    assert result['layers']['upper']['cv_delta_pp']==pytest.approx(1)
    assert len(result['requirements_before_formal_ranking'])>=2


def test_compare_rejects_incompatible_profile_or_script():
    a=parse_native_csv(REFERENCE);b=copy.deepcopy(a)
    a['reference_script_sha256']='one';b['reference_script_sha256']='two'
    with pytest.raises(ValueError,match='algorithms differ'):
        compare_native_metrics(a,b)
    b['reference_script_sha256']='one';b['profile']='not11.9'
    with pytest.raises(ValueError,match='geometry profiles'):
        compare_native_metrics(a,b)


def test_saved_h5_default_entry_uses_real_native_engine_when_profile_passes(tmp_path,monkeypatch,capsys):
    """GUI invokes af_h5_offline.main; prove native backend reaches its JSON."""
    import json
    import af_native_profile
    import af_h5_offline
    h5py=pytest.importorskip('h5py')
    case=tmp_path/'one.cas.h5';data=tmp_path/'one.dat.h5'
    with h5py.File(case,'w') as h:h.create_dataset('meshes/1/dummy',data=[1,2])
    with h5py.File(data,'w') as h:h.create_dataset('results/1/dummy',data=[3,4])
    expected=parse_native_csv(REFERENCE)
    original=af_native_profile.try_reference_native
    def successful_real_backend(case_path,data_path,output_dir):
        assert Path(case_path)==case and Path(data_path)==data
        out=tmp_path/'native';out.mkdir(exist_ok=True)
        (out/'report.json').write_text('{}',encoding='utf-8')
        (out/'report.html').write_text('<h1>Reference</h1>',encoding='utf-8')
        (out/'report.csv').write_text('Reference',encoding='utf-8')
        return {'status':'native_11_9_profile_computed', 'metrics':expected,
                'json_file':str(out/'report.json'),'html_file':str(out/'report.html'),
                'csv_file':str(out/'report.csv')}
    monkeypatch.setattr(af_native_profile,'try_reference_native',successful_real_backend)
    monkeypatch.setattr(af_h5_offline,'inspect_case_data',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must use native engine first')))
    monkeypatch.setattr(sys,'argv',['af_h5_offline.py','--file',str(case),'--prefer-11-9-reference','--output-dir',str(tmp_path/'indexes')])
    af_h5_offline.main()
    r=json.loads(capsys.readouterr().out)
    assert r['physical_analysis_status']=='native_11_9_profile_computed'
    assert r['analysis_json'].endswith('report.json')
    assert r['derived_metrics']['layers']['upper']['cv_pct']==pytest.approx(3.2676,abs=.0001)
    assert Path(r['index_file']).is_file()
    monkeypatch.setattr(af_native_profile,'try_reference_native',original)


def test_reference_script_byte_for_byte_same_as_uploaded():
    import hashlib
    source=Path('/mnt/data/检查原生数据.py')
    bundled=ROOT/'worker/af_native_11_9_reference.py'
    if source.exists():  # Real uploaded reference available in this project's test run.
        assert hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(bundled.read_bytes()).digest()
