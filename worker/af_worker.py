"""JSON-line worker. stdout is one JSON response; stderr is not a transport."""
import json
import sys
import traceback
from pathlib import Path
from af_core import scan, export_template, make_plan, publish, AFError

def connect(server_info):
    if not server_info or not Path(server_info).is_file():
        raise AFError('未提供有效的 Fluent server-info 文件。请先在 Fluent 中启动 gRPC Server。')
    try:
        from ansys.fluent.core import connect_to_fluent
    except ImportError as e:
        raise AFError('当前 Python 环境未安装 ansys-fluent-core。请安装兼容 Fluent 2024 R2 的版本。') from e
    # Important: never shut down a user's existing Solver.
    return connect_to_fluent(server_info_file_name=str(Path(server_info).resolve()),
                             cleanup_on_exit=False, start_transcript=False)

def execute(request):
    action=request.get('action')
    if action=='selftest':
        try:
            import ansys.fluent.core as core
            version = getattr(core,'__version__','unknown')
        except ImportError:
            version = 'not-installed'
        return {'python':sys.executable,'pyfluent':version, 'actions':['scan','extract','preview','publish']}
    if action not in ('scan','extract','preview','publish'):
        raise AFError('不支持的操作: '+str(action))
    session=connect(request.get('server_info'))
    settings=session.settings
    if action=='scan':
        snapshot=scan(settings)
        snapshot['connection_verified'] = True
        try:
            snapshot['fluent_version'] = str(session.get_fluent_version())
        except Exception as exc:
            snapshot['fluent_version'] = 'unknown'
        try:
            snapshot['draft_template']=export_template(snapshot, request.get('template_id','ALD-EXTRACTED'))
        except Exception as e:
            snapshot.setdefault('warnings',[]).append('无法生成工况草稿：'+str(e)[:200])
        # An already approved template may be compared read-only during scan;
        # a new draft must never be approved or published automatically.
        approved = request.get('template')
        if isinstance(approved, dict) and approved.get('verified_with_real_case') is True:
            try:
                snapshot['auto_preview']=make_plan(settings, approved)
            except Exception as e:
                snapshot.setdefault('warnings',[]).append('自动差异预检未完成：'+str(e)[:200])
        return snapshot
    if action=='extract': return export_template(scan(settings),request.get('template_id','ALD-EXTRACTED'))
    template=request.get('template')
    if not isinstance(template,dict): raise AFError('请先提供工况模板 JSON')
    if action=='preview': return make_plan(settings,template)
    return publish(settings,template,request.get('confirm'),request.get('expected_plan_signature'))

def main():
    try:
        req=json.load(sys.stdin)
        answer={'ok':True,'data':execute(req)}
    except Exception as e:
        answer={'ok':False,'error':str(e), 'kind':type(e).__name__}
    print(json.dumps(answer,ensure_ascii=False,allow_nan=False,default=str))

if __name__=='__main__':main()
