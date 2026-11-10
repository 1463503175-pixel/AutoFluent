# -*- coding: utf-8 -*-
"""AutoFluent Workbench attach adapter.

Follows the AutoMesh R8 Workbench bridge pattern: existing Workbench only,
loopback-only port discovery, authenticated session validation, unique-system
selection, explicit TUI read-only probe. No Workbench/Fluent launch, init, or
case mutation. A WB bridge alone is NOT the PyFluent case/data read-back API.
"""
import argparse
import csv
import io
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback
import uuid
from af_live_capture import capture_live_report
from am_workbench_bridge import connect_existing, read_only_probe, list_systems as am_list_systems

SCRIPT = r'''import json
items=[]
for s in GetAllSystems():
    try: name=str(s.Name)
    except Exception: continue
    try: label=str(s.DisplayText)
    except Exception: label=name
    if not label: label=name
    found={}
    for kind in ('Setup','Solution'):
        try:
            c=s.GetContainer(ComponentName=kind)
            if c is not None:
                found[kind]=c
        except Exception: pass
    fluent=False
    container=''
    evidence=''
    # A Fluent-only API is good positive evidence, but never make it a
    # mandatory recognition gate: some older Workbench/Fluent versions can
    # raise here, especially on already-active editors.
    for kind in ('Setup','Solution'):
        if kind in found:
            try:
                found[kind].GetFluentLauncherSettings()
                fluent=True
                container=kind
                evidence='launcher_api'
                break
            except Exception: pass
    # Safe fallback: identify from the WB system's explicit Fluent label,
    # but only if it actually exposes a Setup/Solution container. TUI will
    # still be checked separately and never implies solver readiness.
    if not fluent and found:
        title=(name+' '+label).lower()
        if 'fluent' in title or name.upper().startswith(('FFF','FLU')):
            fluent=True
            container='Setup' if 'Setup' in found else 'Solution'
            evidence='system_label'
    items.append({'name':name,'label':label,'fluent':fluent,
                  'container':container,'evidence':evidence,
                  'has_setup':('Setup' in found),
                  'has_solution':('Solution' in found)})
wb_script_result=json.dumps(items)
'''


def _loads(raw):
    if raw is None or str(raw).strip().lower() in ('none','null',''):
        return None
    if isinstance(raw, (list, dict)):
        return raw
    if raw in ('AUTOFLUENT_TUI_REPORT_SUBMITTED', 'AUTOFLUENT_WB_ONLY_OK'):
        return raw
    data=json.loads(raw)
    if isinstance(data,str) and data.startswith(('[','{')):
        data=json.loads(data)
    return data


def _run_wb_script(wb, script):
    raw=wb.run_script_string(script)
    answer=_loads(raw)
    if answer is not None:
        return answer
    # Same unique local-file return fallback as AutoMesh. No stale files.
    file=Path(tempfile.gettempdir())/('af_wb_'+uuid.uuid4().hex+'.json')
    p=json.dumps(str(file).replace('\\','/'))
    try:
        wb.run_script_string(script+'\nwith open(u%s, "w") as f:\n    f.write(wb_script_result)\n' % p)
        if not file.is_file():
            raise RuntimeError('Workbench returned None and did not write probe receipt')
        return _loads(file.read_text(encoding='utf-8-sig'))
    finally:
        try: file.unlink()
        except OSError: pass


def read_project_identity(wb):
    """Read only the existing Workbench project identity; not solver Case/Data."""
    script = ("import json\n"
              "try: _p=str(GetProjectFile())\n"
              "except Exception: _p=''\n"
              "try: _d=str(GetProjectDirectory())\n"
              "except Exception: _d=''\n"
              "wb_script_result=json.dumps({'project_file':_p,'project_dir':_d})")
    answer=_run_wb_script(wb,script)
    if not isinstance(answer,dict):
        raise RuntimeError('Workbench did not return a project identity')
    return {k:str(answer.get(k) or '') for k in ('project_file','project_dir')}


def inventory_case_data(directory, max_files=35):
    """Metadata only. Do not read huge HDF5 files or infer which one is current."""
    root=Path(directory)
    if not root.is_dir(): return []
    found=[]
    dirs_seen=0
    for folder, dirs, files in os.walk(str(root), followlinks=False):
        dirs_seen+=1
        if dirs_seen>1300: break
        depth=len(Path(folder).relative_to(root).parts)
        if depth >= 7: dirs[:] = []
        else: dirs[:]=[x for x in dirs if not x.lower().startswith(('__','backup','temp'))]
        for name in files:
            low=name.lower()
            if not low.endswith(('.cas.h5','.dat.h5','.cas.gz','.dat.gz','.cas','.dat')): continue
            path=Path(folder)/name
            try: st=path.stat()
            except OSError:continue
            if st.st_size<100:continue
            found.append({'path':str(path),'bytes':st.st_size,'modified_epoch':st.st_mtime,
                          'kind':'case' if '.cas' in low else 'data'})
    found.sort(key=lambda x:x['modified_epoch'],reverse=True)
    return found[:max_files]


def list_project_systems(wb):
    """Return ALL real WB systems before filtering; never hide false negatives."""
    rows=_run_wb_script(wb,SCRIPT)
    if not isinstance(rows,list):
        raise ValueError('Workbench system inventory did not return a list')
    return [x for x in rows if isinstance(x,dict)]


def select_fluent_systems(rows):
    """Conservative classification; TUI validation is a distinct next stage."""
    return [x for x in rows if x.get('fluent') is True and
            re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',str(x.get('name','')))]


def list_fluent_systems(wb):
    return select_fluent_systems(list_project_systems(wb))


def connect(port, security):
    """Use the same actual attach call as AutoMesh, without reimplementing it."""
    return connect_existing(port, security)


def read_am_connection():
    roots=[Path(os.environ.get('ProgramData',r'C:\ProgramData'))/'AutoMesh2024R2'/'Data',
           Path(os.environ.get('LOCALAPPDATA',''))/'AutoMesh2024R2'/'Data']
    result=[]
    for root in roots:
        path=root/'last_connection.json'
        try:
            obj=json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):
            continue
        # A cached port is a hint, NEVER proof of connected Workbench.
        p=obj.get('port')
        try: p=int(p)
        except (TypeError,ValueError): continue
        if 1024<=p<=65535:
            security=obj.get('security','auto')
            result.append((p,security if security in ('mtls','insecure','wnua') else 'auto'))
    return result


def parse_netstat(content):
    records=set()
    for line in content.splitlines():
        v=line.split()
        if len(v)<5 or v[0].upper()!='TCP': continue
        if not ('LISTEN' in v[3].upper() or '侦听' in v[3] or '監聽' in v[3]): continue
        try:
            addr=v[1]
            if not (addr.startswith('127.0.0.1:') or addr.startswith('0.0.0.0:')
                    or addr.startswith('[::1]:') or addr.startswith('[::]:')):
                continue
            p=int(addr.rsplit(':',1)[1]); pid=int(v[4])
            if 1024<=p<=65535: records.add((p,pid))
        except ValueError: continue
    return records


def listeners():
    if os.name!='nt': return set()
    r=subprocess.run(['netstat','-ano','-p','tcp'],stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,timeout=8,check=True)
    return parse_netstat(r.stdout.decode('gbk',errors='replace')) | parse_netstat(r.stdout.decode('utf-8',errors='replace'))


def process_names():
    if os.name!='nt': return {}
    raw=subprocess.run(['tasklist','/FO','CSV','/NH'],stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,timeout=7,check=True).stdout
    names={}
    for codec in (locale.getpreferredencoding(False),'gbk','utf-8-sig'):
        try:
            for row in csv.reader(io.StringIO(raw.decode(codec,errors='replace'))):
                if len(row)>1:
                    try:names[int(row[1].replace(',','').strip())]=row[0].lower().strip()
                    except ValueError: pass
            if names: break
        except (LookupError,UnicodeError): pass
    return names


def candidate_ports():
    cached=[(p,s,'AutoMesh cached port') for p,s in read_am_connection()]
    records=listeners()
    names=process_names()
    wb={'runwb2.exe','ansyswbu.exe','ansysfw.exe','ansysfww.exe'}
    direct=sorted({p for p,pid in records if names.get(pid,'') in wb})
    others=sorted({p for p,_ in records if p not in direct})
    candidates=cached+[(p,'auto','Workbench-owned listener') for p in direct]
    # Only limited candidates. A TCP listener is never proof of Workbench.
    if not direct: candidates += [(p,'auto','unattributed listener') for p in others[:4]]
    dedup=[]; seen=set()
    for p,s,origin in candidates:
        if p not in seen:
            seen.add(p);dedup.append((p,s,origin))
    return dedup[:12]


def find_am_bootstrap():
    roots=[Path(os.environ.get('ProgramData',r'C:\ProgramData'))/'AutoMesh2024R2'/'Data',
           Path(os.environ.get('LOCALAPPDATA',''))/'AutoMesh2024R2'/'Data']
    # Prefer exactly the installed, proven AutoMesh worker, not guessed scripts.
    for root in roots:
        f=root/'wb_server_bootstrap.py'
        helper=root/'start_wb_server_uia.ps1'
        if f.is_file() and helper.is_file(): return f
    return None


def bootstrap_using_am():
    helper=find_am_bootstrap()
    if helper is None:
        helper=Path(__file__).with_name('af_wb_bootstrap.py')
        if not helper.is_file():
            return {'status':'not_installed','detail':'Neither AutoMesh nor embedded helper is available','candidate_ports':[]}
    # Execute only once; failed or uncertain submission is not retried.
    result=subprocess.run([sys.executable,str(helper),'--mode','start'],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=38)
    for line in reversed(result.stdout.splitlines()):
        try:
            data=json.loads(line)
            if isinstance(data,dict) and 'status' in data: return data
        except ValueError: pass
    return {'status':'unavailable','detail':result.stderr[-500:] or 'no helper result','candidate_ports':[]}


def validate_ports(candidates):
    errors=[]
    for port,preferred,source in candidates:
        modes=([preferred]+[x for x in ('mtls','insecure','wnua') if x!=preferred]) if preferred!='auto' else ['mtls','insecure','wnua']
        for sec in modes:
            try:
                wb=connect(port,sec)
                systems=list_fluent_systems(wb)
                return {'status':'connected','port':port,'security':sec,'source':source,
                        'systems':systems,'wb':wb}
            except Exception as exc:
                if len(errors)<9: errors.append('%s/%s: %s'%(port,sec,str(exc)[:160]))
    return {'status':'unavailable','errors':errors}


def export_existing_fluent_settings(wb, systems, selection, output_dir):
    """Export actual user-modified settings using official Fluent v242 TUI.

    Creates one uniquely named small file OUTSIDE the case, never reads case/data,
    never initializes/iterates, never repeats a SendCommand on ambiguous return.
    This export does NOT capture default settings or independently validate a case.
    """
    import hashlib
    from datetime import datetime
    matches=[x for x in systems if x.get('name')==selection and x.get('fluent')]
    if len(matches)!=1: raise RuntimeError('需要唯一的 Fluent 系统，禁止猜测目标')
    target=matches[0]
    container=target.get('container','Setup')
    if container not in ('Setup','Solution'): raise RuntimeError('目标容器不安全')
    outdir=Path(output_dir).expanduser().resolve()
    if not outdir.is_absolute(): raise ValueError('导出目录必须是绝对路径')
    outdir.mkdir(parents=True,exist_ok=True)
    file=outdir/('fluent_settings_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10]+'.set')
    if file.exists(): raise RuntimeError('不能覆盖已有设置文件')
    name=json.dumps(target['name'],ensure_ascii=True)
    cn=json.dumps(container)
    # TUI uses forward slashes on Windows; quote the path as a TUI argument.
    command='/file/write-settings "%s"' % str(file).replace('\\','/')
    script=('target=None\n'
            'for _s in GetAllSystems():\n'
            '    if str(_s.Name)==%s:\n' % name+
            '        if target is not None: raise Exception("Duplicate system")\n'
            '        target=_s\n'
            'if target is None: raise Exception("Target system missing")\n'
            'c=target.GetContainer(ComponentName=%s)\n' % cn+
            'if c is None: raise Exception("Fluent editor container missing")\n'
            'c.SendCommand(Command=%s)\n' % json.dumps(command,ensure_ascii=True)+
            'wb_script_result="AUTOFLUENT_SETTINGS_COMMAND_SUBMITTED"\n')
    raw=wb.run_script_string(script)  # exactly once, even if return None
    # The created file, NOT a Workbench return value, proves an actual export.
    # Some solver filesystem integrations return before the file is flushed.
    import time
    for _ in range(12):
        if file.is_file() and file.stat().st_size>=32:break
        time.sleep(.25)
    if not file.is_file() or file.stat().st_size<32:
        return {'status':'settings_unconfirmed','system':selection,
                'detail':'Workbench 可能已收到指令，但未发现 Fluent 生成的设置文件；不得重发或视为成功'}
    raw_bytes=file.read_bytes()
    return {'status':'settings_exported','system':selection,
            'file':str(file),'bytes':len(raw_bytes),
            'sha256':hashlib.sha256(raw_bytes).hexdigest(),
            'scope':'user-modified-settings-only; excludes solver defaults, mesh and field data',
            'verified_provenance':'settings_file_written_by_running_fluent',
            'detail':'已从当前 Fluent 导出文件；不代表所有设置或求解结果完成一致性验证'}


def safe_probe(wb,systems,selection=None):
    """Read-only Fluent TUI report with one-shot durable receipt, no retry."""
    if selection:
        matches=[s for s in systems if s['name']==selection]
    else:
        matches=systems if len(systems)==1 else []
    if len(matches)!=1:
        return {'status':'selection_required','detail':'Multiple Fluent systems require one explicit selection'}
    target=matches[0]
    n=json.dumps(target['name'])
    container = target.get('container') if target.get('container') in ('Setup','Solution') else 'Setup'
    receipt=Path(tempfile.gettempdir())/('af_tui_receipt_'+uuid.uuid4().hex+'.txt')
    path_literal=json.dumps(str(receipt).replace('\\','/'))
    # Fluent Setup.SendCommand should fail if editor is not open. We neither
    # invoke Edit() nor create Fluent. Persist a unique receipt *after* the
    # TUI report. When Workbench returns None, never resubmit that command.
    script=('target=None\n'
            'for s in GetAllSystems():\n'
            '    if str(s.Name)==%s:\n' % n+
            '        if target is not None: raise Exception("Duplicate system")\n'
            '        target=s\n'
            'if target is None: raise Exception("System missing")\n'
            'c=target.GetContainer(ComponentName=%s)\n' % json.dumps(container)+
            'if c is None: raise Exception("Fluent container unavailable")\n'
            'c.SendCommand(Command="/report/summary")\n'
            'with open(u%s,"w") as r:\n' % path_literal+
            '    r.write("AUTOFLUENT_TUI_REPORT_SUBMITTED")\n'
            'wb_script_result="AUTOFLUENT_TUI_REPORT_SUBMITTED"\n')
    try:
        raw=wb.run_script_string(script)
        try: value=_loads(raw)
        except (ValueError,TypeError): value=None
        if value!='AUTOFLUENT_TUI_REPORT_SUBMITTED' and receipt.is_file():
            value=receipt.read_text(encoding='ascii')
        if value!='AUTOFLUENT_TUI_REPORT_SUBMITTED':
            raise RuntimeError('TUI command status unconfirmed; never resubmit automatically')
        return {'status':'tui_probe_submitted','system':target['name'], 'receipt':value}
    finally:
        try: receipt.unlink()
        except OSError: pass


def auto_connect(start_bootstrap=True):
    try:
        import ansys.workbench.core
    except Exception as exc:
        return {'status':'missing_pyworkbench','detail':str(exc)[:260]}
    candidate=candidate_ports()
    result=validate_ports(candidate)
    if result.get('status')!='connected' and start_bootstrap:
        boot=bootstrap_using_am()
        if boot.get('status')=='submitted':
            # Always revalidate protocol and Workbench system identity.
            more=[(int(p),'auto','AutoMesh StartServer') for p in boot.get('candidate_ports',[])
                  if isinstance(p,int) and 1024<=p<=65535]
            more+=candidate_ports()
            result=validate_ports(more)
        result['bootstrap']=boot
    if result.get('status')!='connected':
        return {'status':'workbench_unavailable','detail':'No validated existing Workbench script server',
                'attempts':result.get('errors',[]),'bootstrap':result.get('bootstrap')}
    wb=result.pop('wb')
    systems=result.get('systems',[])
    if len(systems)!=1:
        result['status']='selection_required' if len(systems)>1 else 'no_fluent_system'
        return result
    try: result['tui']=safe_probe(wb,systems)
    except Exception as exc:
        result['status']='fluent_probe_failed'
        result['detail']=str(exc)[:400]
        return result
    result['status']='fluent_connected' if result['tui']['status']=='tui_probe_submitted' else 'fluent_probe_failed'
    return result


def _wb_only_handshake(session):
    """Reuse AutoMesh's tolerant read-only probe; never require Fluent.

    A no-return probe is not definitive; verify the real WB system list next.
    """
    # AutoMesh explicitly treats this probe as OPTIONAL, not an attach gate.
    # An older WB return channel may fail to return a string but still allow
    # the full project-system inventory via its local-file fallback.
    try:
        status = read_only_probe(session)
    except Exception as exc:
        status = 'AM_PROBE_UNAVAILABLE: %s: %s' % (type(exc).__name__, str(exc)[:180])
    systems = am_list_systems(session)
    return {'probe': status, 'system_count': len(systems)}


def manual_connect(port, security='auto'):
    """Verify the exact supplied WB port, with each transport isolated by the caller.

    Go invokes one security mode per subprocess so a hanging mTLS handshake
    cannot eat the entire insecure/WNUA retry budget. `auto` remains supported
    for standalone use and tests, but the GUI never relies on it.
    """
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('Workbench 端口应为 1024～65535')
    if security not in ('auto', 'mtls', 'insecure', 'wnua'):
        raise ValueError('不支持的 Workbench 安全模式')
    try:
        import ansys.workbench.core  # noqa: F401
    except Exception as exc:
        return {'status':'missing_pyworkbench', 'port':port,
                'detail':type(exc).__name__ + ': ' + str(exc)[:400],
                'attempts':[type(exc).__name__ + ': ' + str(exc)[:400]]}
    modes = ('mtls','insecure','wnua') if security == 'auto' else (security,)
    errors=[]
    for mode in modes:
        try:
            session=connect(port,mode)
        except Exception as exc:
            errors.append('%s / connect_workbench: %s: %s' % (
                mode,type(exc).__name__,str(exc)[:450]))
            continue
        try:
            proof = _wb_only_handshake(session)
        except Exception as exc:
            errors.append('%s / run_script_string: %s: %s' % (
                mode,type(exc).__name__,str(exc)[:450]))
            continue
        return {'status':'workbench_connected', 'port':port,
                'security':mode, 'source':'manual_port', 'systems':[],
                'detail':'已使用 AutoMesh 原版连接和系统列表核验；未检查 Fluent。',
                'wb_probe':proof,
                'attempts':[]}
    return {'status':'workbench_unavailable','port':port,'source':'manual_port',
            'detail':'已探测指定端口，但 Workbench 握手尚未成功；以下记录包含具体阶段和异常。',
            'attempts':errors}


def discover_fluent_on_verified_wb(port, security):
    """Explicit second phase: inspect WB systems, then probe selected Fluent.

    Never launch any solver. If no Fluent system exists, WB stays connected.
    """
    if not isinstance(port,int) or not 1024 <= port <= 65535:
        raise ValueError('Invalid Workbench port')
    if security not in ('mtls','insecure','wnua'):
        raise ValueError('WB security mode must come from a verified connection')
    session=connect(port,security)
    _wb_only_handshake(session)
    base={'port':port,'security':security,'source':'manual_port','systems':[]}
    try:
        identity=read_project_identity(session)
        base.update(identity)
        base['case_data_candidates']=inventory_case_data(identity.get('project_dir',''))
    except Exception as exc:
        base['project_identity_note']='无法读取工程路径：'+str(exc)[:220]
    try:
        all_systems=list_project_systems(session)
        systems=select_fluent_systems(all_systems)
    except Exception as exc:
        return dict(base,status='workbench_connected_scan_failed',
                    detail='Workbench 仍已连接，但读取项目系统列表失败：%s'%str(exc)[:350])
    base['systems']=systems
    base['all_systems']=all_systems
    if not systems:
        if not all_systems:
            why='此端口对应的 WB 当前工程返回 0 个系统。Fluent 窗口可能属于另一份 WB 工程或独立启动。'
        else:
            labels=', '.join('%s:%s'% (str(x.get('name','?')),str(x.get('label','?'))) for x in all_systems[:8])
            why='WB 当前工程共 %d 个系统，但没有确认到 Fluent；原始系统：%s。检查该端口与 Fluent 所属工程是否一致。'%(len(all_systems),labels)
        return dict(base,status='no_fluent_system',detail=why)
    if len(systems)>1:
        return dict(base,status='selection_required',
                    detail='Workbench 已连接；检测到多个 Fluent 系统，请选择目标。')
    try:
        proof=safe_probe(session,systems)
        status='fluent_connected' if proof.get('status')=='tui_probe_submitted' else 'fluent_probe_failed'
        return dict(base,status=status,tui=proof,
                    detail='已向目标 Fluent 发送只读 TUI 报告；不代表完整工况回读。')
    except Exception as exc:
        return dict(base,status='fluent_probe_failed',
                    detail='Workbench 已连接；Fluent TUI 尚不可用（可能未打开编辑器）：%s'%str(exc)[:280])

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('manual','probe','fluent','settings','report'),default='manual')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--security',choices=('auto','mtls','insecure','wnua'),default='auto')
    parser.add_argument('--system',default='')
    parser.add_argument('--output-dir',default='')
    parser.add_argument('--expected-project',default='')
    parser.add_argument('--no-bootstrap',action='store_true')
    args=parser.parse_args()
    try:
        if args.mode=='manual': result=manual_connect(args.port,args.security)
        elif args.mode=='fluent': result=discover_fluent_on_verified_wb(args.port,args.security)
        elif args.mode=='settings':
            if not (1024<=args.port<=65535 and
                    re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',args.system) and
                    args.output_dir and os.path.isabs(args.output_dir)):
                raise ValueError('缺少有效的手动 WB 端口、系统及输出目录')
            client=connect(args.port,args.security)
            _wb_only_handshake(client)
            if args.expected_project:
                current_project=read_project_identity(client).get('project_file','')
                if not current_project or os.path.normcase(os.path.normpath(current_project)) != os.path.normcase(os.path.normpath(args.expected_project)):
                    raise RuntimeError('当前 Workbench 工程已改变，阻止导出到错误模型；需重新连接检查')
            systems=list_fluent_systems(client)
            result=export_existing_fluent_settings(client,systems,args.system,args.output_dir)
            result.update(port=args.port, security=args.security)
        elif args.mode=='report':
            if not (1024<=args.port<=65535 and
                    re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', args.system) and
                    args.output_dir and os.path.isabs(args.output_dir)):
                raise ValueError('需要已验证 WB 端口、唯一 Fluent 系统及绝对输出目录')
            client=connect(args.port,args.security)
            _wb_only_handshake(client)
            systems=list_fluent_systems(client)
            result=capture_live_report(
                client,systems,args.system,args.output_dir,
                expected_project=args.expected_project,
                project_reader=read_project_identity)
            result.update(port=args.port, security=args.security)
        elif args.mode=='probe':
            if not (1024 <= args.port <= 65535 and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',args.system)):
                raise ValueError('Invalid Workbench port/system selection')
            wb=connect(args.port,args.security)
            systems=list_fluent_systems(wb)
            proof=safe_probe(wb,systems,args.system)
            result={'status':'fluent_connected' if proof.get('status')=='tui_probe_submitted' else 'fluent_probe_failed',
                    'port':args.port,'security':args.security,'systems':systems,'tui':proof}
        else: raise ValueError('Only manual port and explicit probe modes are enabled')
        print(json.dumps(result,ensure_ascii=False,default=str))
    except Exception as exc:
        print(json.dumps({'status':'error','detail':str(exc),'traceback':traceback.format_exc()[-1200:]},ensure_ascii=False))
        sys.exit(1)

if __name__=='__main__':main()
