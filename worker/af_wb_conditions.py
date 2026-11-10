# -*- coding: utf-8 -*-
"""Workbench Fluent .set transfer with before/after *file* evidence.

A .set file includes USER-MODIFIED settings only. It is not a complete case
or independent proof of defaults, zone matching, mesh topology or material
properties. Publication requires operator approval and leaves the solver
unchanged unless it reaches exactly one /file/read-settings command.
"""
import argparse
import hashlib
import difflib
from itertools import islice
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from af_live_capture import fluent_tui_argument
from af_wb_solver import atomic_json, timestamp

SYSTEM_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')


def sha(path):
    p=Path(path)
    h=hashlib.sha256()
    if p.stat().st_size>64*1024*1024 or p.stat().st_size<32:
        raise ValueError('Settings file size is unsafe or empty')
    with p.open('rb') as f:
        for buf in iter(lambda:f.read(1024*1024), b''):
            h.update(buf)
    return h.hexdigest()


def validate_source(source, app_dir):
    p=Path(source).resolve(strict=True)
    root=Path(app_dir).resolve(strict=True)/'settings_exports'
    if not p.is_file() or p.suffix.lower()!='.set' or p.parent!=root:
        raise ValueError('Standard .set must be selected from AutoFluent settings_exports directory')
    return p


def current_identity(wb, systems, target, expected_project, reader):
    if not SYSTEM_RE.fullmatch(target):
        raise ValueError('Unsafe WB system name')
    found=[s for s in systems if s.get('name')==target and s.get('fluent')]
    if len(found)!=1 or found[0].get('container') not in ('Setup','Solution'):
        raise RuntimeError('Selected Fluent target not unique')
    actual=reader(wb).get('project_file','') if reader else ''
    if expected_project and (not actual or os.path.normcase(os.path.normpath(actual))!=os.path.normcase(os.path.normpath(expected_project))):
        raise RuntimeError('Workbench project changed; no settings action sent')
    return found[0], actual


def preview(wb, systems, system, source, app_dir, expected_project, project_reader, export):
    target, actual=current_identity(wb,systems,system,expected_project,project_reader)
    source=validate_source(source,app_dir)
    src_sha=sha(source)
    # Read the current target configuration FIRST; this is non-mutating.
    before=export(wb,systems,system,str(Path(app_dir)/'settings_exports'))
    if before.get('status')!='settings_exported':
        raise RuntimeError('Fluent did not produce a verified pre-publication settings file')
    jobs=Path(app_dir)/'settings_jobs'
    jobs.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex
    file=jobs/(token+'.json')
    state={'id':token,'status':'review_required','system':system,
           'container':target['container'],'project':actual,'created_epoch':time.time(),
           'source_file':str(source),'source_sha256':src_sha,
           'before_file':before['file'],'before_sha256':before['sha256'],
           'same_raw_settings_file':src_sha==before['sha256'],
           'zone_mapping_independently_verified':False,
           'defaults_verified':False,
           'note':'仅对比用户修改设置文件；不同几何/版本的默认项、zone映射和材料需在 Fluent 逐项核验。'}
    # Human-readable byte/text difference for the operator; NOT a semantic
    # comparison of default values or Fluent zone mappings.
    source_bytes=source.read_bytes()
    before_bytes=Path(before['file']).read_bytes()
    diff_file=jobs/(token+'.diff.txt')
    if len(source_bytes)>5*1024*1024 or len(before_bytes)>5*1024*1024:
        diff='文件超过 5MB，仅报告 SHA256；未进行逐行差异比较。\n'
    else:
        a=before_bytes.decode('utf-8','replace').splitlines(keepends=True)
        b=source_bytes.decode('utf-8','replace').splitlines(keepends=True)
        diff=''.join(list(islice(difflib.unified_diff(a,b,fromfile='当前目标 .set',tofile='基准源 .set'),500)))
        if not diff:diff='两份导出的原始设置文件逐字节一致；仍未验证默认值/zone 映射。\n'
    diff_file.write_text(diff[:40000],encoding='utf-8')
    state.update(diff_file=str(diff_file),diff_scope='raw_modified_settings_files_only')
    atomic_json(file,state)
    return dict(state,plan_file=str(file))


def build_apply_script(system,container,source,receipt):
    if not SYSTEM_RE.fullmatch(system) or container not in ('Setup','Solution'):
        raise ValueError('Invalid Fluent target')
    cmd='/file/read-settings '+fluent_tui_argument(source)
    return '\n'.join([
        'import os, json, time',
        '_af_items=[]',
        'for _s in GetAllSystems():',
        '    if str(_s.Name)==%s: _af_items.append(_s)' % json.dumps(system),
        'if len(_af_items)!=1: raise Exception("Fluent system changed")',
        '_af_c=_af_items[0].GetContainer(ComponentName=%s)' % json.dumps(container),
        'if _af_c is None: raise Exception("Fluent container not available")',
        '_af_c.GetFluentLauncherSettings()',
        '_af_receipt=u%s' % json.dumps(str(receipt).replace('\\','/')),
        '_af_f=open(_af_receipt,"w")',
        '_af_f.write(json.dumps({"status":"read_settings_submitted","epoch":time.time()}))',
        '_af_f.close()',
        '_af_c.SendCommand(Command=%s)' % json.dumps(cmd),
        '_af_f=open(_af_receipt,"w")',
        '_af_f.write(json.dumps({"status":"read_settings_returned","epoch":time.time()}))',
        '_af_f.close()',
        'wb_script_result=json.dumps({"status":"read_settings_returned"})',
    ])+'\n'


def apply(wb, systems, system, plan_file, app_dir, expected_project, project_reader, export, approved=False):
    if not approved:
        raise PermissionError('Settings publication requires explicit operator approval')
    jobs=(Path(app_dir).resolve()/'settings_jobs')
    file=Path(plan_file).resolve(strict=True)
    if file.parent!=jobs or not re.fullmatch(r'[a-f0-9]{32}\.json',file.name):
        raise ValueError('Invalid or external reviewed-plan file')
    state=json.loads(file.read_text(encoding='utf-8'))
    if state.get('status')!='review_required' or state.get('system')!=system:
        raise RuntimeError('Settings plan already used, cancelled or for another system')
    if time.time()-float(state.get('created_epoch',0))>900:
        raise RuntimeError('Settings plan expired (15 minutes)')
    selected, actual=current_identity(wb,systems,system,expected_project,project_reader)
    if selected['container']!=state['container'] or state.get('project')!=actual:
        raise RuntimeError('Workbench project or Fluent container changed')
    source=validate_source(state['source_file'],app_dir)
    if sha(source)!=state['source_sha256'] or sha(state['before_file'])!=state['before_sha256']:
        raise RuntimeError('Standard source or archived target snapshot changed')
    # Confirm current Fluent settings have not changed since the preview.
    current=export(wb,systems,system,str(Path(app_dir)/'settings_exports'))
    if current.get('status')!='settings_exported' or current.get('sha256')!=state['before_sha256']:
        raise RuntimeError('Current Fluent settings changed since preflight; no read-settings sent')
    from af_target_lock import acquire_target,release_confirmed
    target_lock=acquire_target(Path(app_dir).resolve(),actual,system,state['container'],state['id'],'publication')
    lock=jobs/'active_publication.lock'
    try:
        with lock.open('x',encoding='utf-8') as f:
            f.write(state['id'])
    except BaseException:
        release_confirmed(target_lock,state['id'])
        raise
    receipt=jobs/(state['id']+'.receipt.json')
    # Mark irreversible submission boundary before giving script to WB.
    state.update(status='publication_dispatching',started_at=timestamp())
    atomic_json(file,state)
    try:
        raw=wb.run_script_string(build_apply_script(system,state['container'],source,receipt))
        if not receipt.is_file():
            raise RuntimeError('No independent WB receipt; remote state unknown')
        evidence=json.loads(receipt.read_text(encoding='utf-8'))
        if evidence.get('status')!='read_settings_returned':
            raise RuntimeError('Workbench settings command return unconfirmed')
        after=export(wb,systems,system,str(Path(app_dir)/'settings_exports'))
        if after.get('status')!='settings_exported':
            raise RuntimeError('Cannot obtain Fluent after-settings for readback')
        state.update(status=('user_modified_settings_readback_match' if after['sha256']==state['source_sha256']
                             else 'readback_mismatch_needs_review'),
                     after_file=after['file'],after_sha256=after['sha256'],finished_at=timestamp(),
                     full_physics_verified=False,zone_mapping_independently_verified=False,
                     defaults_verified=False)
        atomic_json(file,state)
        if state['status']=='user_modified_settings_readback_match':
            lock.unlink(missing_ok=True)
            release_confirmed(target_lock,state['id'])
        # A mismatch retains the lease: don't let a later click overwrite the scene.
        return dict(state,plan_file=str(file),active_lock_retained=lock.exists(),
                    shared_target_lock_retained=target_lock.exists())
    except BaseException as exc:
        state.update(status='publication_remote_state_unconfirmed',failure=repr(exc),finished_at=timestamp())
        atomic_json(file,state)
        raise RuntimeError('WB settings publication may have reached Fluent; lease retained, do not retry: '+str(exc)) from exc


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['preview','publish'],required=True)
    p.add_argument('--port',type=int,required=True)
    p.add_argument('--security',choices=['mtls','insecure','wnua'],required=True)
    p.add_argument('--system',required=True)
    p.add_argument('--app-dir',required=True)
    p.add_argument('--source',default='')
    p.add_argument('--plan',default='')
    p.add_argument('--expected-project',default='')
    p.add_argument('--approved',action='store_true')
    a=p.parse_args()
    try:
        from af_wb_bridge import (connect,_wb_only_handshake,list_fluent_systems,
                                  read_project_identity,export_existing_fluent_settings)
        if not 1024<=a.port<=65535 or not os.path.isabs(a.app_dir):
            raise ValueError('Invalid explicit WB port or app data folder')
        wb=connect(a.port,a.security)
        _wb_only_handshake(wb)
        systems=list_fluent_systems(wb)
        if a.mode=='preview':
            outcome=preview(wb,systems,a.system,a.source,a.app_dir,a.expected_project,
                            read_project_identity,export_existing_fluent_settings)
        else:
            outcome=apply(wb,systems,a.system,a.plan,a.app_dir,a.expected_project,
                          read_project_identity,export_existing_fluent_settings,approved=a.approved)
        print(json.dumps(outcome,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'error_or_unconfirmed','detail':str(exc)},ensure_ascii=False))
        return 1

if __name__=='__main__':sys.exit(main())
