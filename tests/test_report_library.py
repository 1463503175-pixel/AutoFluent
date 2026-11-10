from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_report_library import analyze_library,save_library,html_summary,candidates
from test_codex_report_import import demo_html


def test_report_library_finds_only_real_supported_reports(tmp_path):
    (tmp_path/'modelA').mkdir()
    (tmp_path/'modelA'/'summary.html').write_text(demo_html(),encoding='utf8')
    (tmp_path/'random.html').write_text('<p>Fluent is important but no data</p>'*30,encoding='utf8')
    r=analyze_library(tmp_path)
    assert len(r['records'])==1
    assert r['records'][0]['model_id']=='11.9'
    assert r['records'][0]['layers']['upper']['channels']==3
    assert r['comparison_qualification']=='NOT_QUALIFIED_FOR_FORMAL_RANKING'
    written=save_library(r,tmp_path/'results')
    assert written['status']=='library_imported' and Path(written['html_file']).exists()
    assert '未独立核验' in Path(written['html_file']).read_text(encoding='utf8')


def test_no_reports_never_claims_success(tmp_path):
    result=save_library(analyze_library(tmp_path),tmp_path/'out')
    assert result['status']=='no_supported_reports' and result['record_count']==0


def test_html_escapes_untrusted_paths(tmp_path):
    r=analyze_library(tmp_path);r['records']=[dict(model_id='<script>alert(1)</script>',path='<b>x</b>',layers={},mass_imbalance_pct=None)]
    s=html_summary(r)
    assert '<script>alert(1)</script>' not in s and '&lt;script&gt;' in s


def test_report_library_ignores_own_export(tmp_path):
    (tmp_path/'model.html').write_text(demo_html(),encoding='utf8')
    first=save_library(analyze_library(tmp_path),tmp_path)
    second=analyze_library(tmp_path)
    assert len(second['records'])==1
    assert all('codex_library_' not in x['path'] for x in second['records'])
