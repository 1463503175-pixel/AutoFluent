# -*- coding: utf-8 -*-
"""Explicit one-shot Workbench -> existing Fluent solver command and evidence.

This module NEVER launches Workbench/Fluent, initializes, saves/replaces Case/Data,
changes settings or silently retries a non-idempotent Fluent command.

A durable lease remains after unconfirmed remote execution.  Only a positively
verified transcript with an iteration table can mark an operator-authorized
solve command observed. Residuals alone cannot certify convergence.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import threading
import uuid
from datetime import datetime, timezone

from af_live_capture import fluent_tui_argument

SYSTEM_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')
RESIDUAL_FIELDS = {'continuity', 'x-velocity', 'y-velocity', 'z-velocity', 'energy',
                   'k', 'epsilon', 'omega', 'nut', 'tke', 'dissipation'}
NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?$')


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def parse_residuals(content):
    """Parse Fluent console residual table conservatively, not arbitrary numbers.

    Requires a recognizable residual table header and subsequent indexed rows.
    Stops at other console text. A table without rows produces unknown evidence.
    """
    header = None
    result = []
    for line in content.splitlines():
        parts = line.strip().lower().split()
        if ('continuity' in parts and any(v in parts for v in ('x-velocity', 'y-velocity', 'z-velocity', 'energy'))):
            header = [p for p in parts if p in RESIDUAL_FIELDS]
            continue
        if header is None:
            continue
        match = re.match(r'^\s*(\d+)\s+(.+?)\s*$', line)
        if not match:
            continue
        # Fluent may print extra columns after residuals (time/it); only the
        # fields named in the immediately preceding header are parsed.
        nums = match.group(2).split()
        if len(nums) < len(header) or not all(NUMBER.fullmatch(x) for x in nums[:len(header)]):
            continue
        vals = {key: float(value.replace('D', 'e').replace('d', 'e'))
                for key, value in zip(header, nums)}
        if not all(0 <= x < 1e100 for x in vals.values()):
            continue
        result.append({'iteration': int(match.group(1)), 'residuals': vals})
    # Repeated header blocks and duplicate iteration numbers are expected;
    # preserve the latest observation for each index.
    by_it = {r['iteration']: r for r in result}
    rows = sorted(by_it.values(), key=lambda r: r['iteration'])
    latest = rows[-1] if rows else None
    return {'status': 'residual_rows_observed' if rows else 'residual_not_observed',
            'rows': rows[-4000:], 'row_count': len(rows), 'latest': latest,
            'residual_convergence_certified': False,
            'mass_flow_stability_certified': False,
            'note': '残差下降不等于流量/压降稳定；需独立检查质量守恒和关键通道监控。'}


def make_wb_script(system, container, transcript, receipt, iterations):
    if not SYSTEM_RE.fullmatch(system) or container not in ('Setup', 'Solution'):
        raise ValueError('Unknown or unsafe Fluent system/container')
    if not 1 <= iterations <= 50:
        raise ValueError('One-shot iteration budget must be 1..50')
    trn = Path(transcript)
    rec = Path(receipt)
    if trn.parent != rec.parent or not trn.is_absolute():
        raise ValueError('Evidence paths must be in the same absolute directory')
    start = '/file/start-transcript ' + fluent_tui_argument(trn)
    # The script writes a durable "submitted" receipt immediately before
    # SendCommand(iterate).  On any exception or ambiguous response, Go/Python
    # must retain its active lease and NEVER automatically reissue the command.
    lines = [
        'import json, os, time',
        '_af_count=0', '_af_s=None',
        'for _s in GetAllSystems():',
        '    if str(_s.Name)==%s:' % json.dumps(system),
        '        _af_count+=1', '        _af_s=_s',
        'if _af_count!=1: raise Exception("Selected system identity changed")',
        '_af_c=_af_s.GetContainer(ComponentName=%s)' % json.dumps(container),
        'if _af_c is None: raise Exception("Fluent container missing")',
        '_af_c.GetFluentLauncherSettings()',
        '_af_trn=u%s' % json.dumps(str(trn).replace('\\', '/')),
        '_af_receipt=u%s' % json.dumps(str(rec).replace('\\', '/')),
        'if os.path.exists(_af_trn): raise Exception("Transcript already exists")',
        '_af_c.SendCommand(Command=%s)' % json.dumps(start),
        'if not os.path.isfile(_af_trn): raise Exception("Transcript not started; no iterations sent")',
        '_af_f=open(_af_receipt, "w")',
        '_af_f.write(json.dumps({"state":"iterate_submitted","epoch":time.time()}))',
        '_af_f.close()',
        'try:',
        '    _af_c.SendCommand(Command=%s)' % json.dumps('/solve/iterate %d' % iterations),
        '    _af_f=open(_af_receipt, "w")',
        '    _af_f.write(json.dumps({"state":"iterate_returned","epoch":time.time()}))',
        '    _af_f.close()',
        'finally:',
        '    _af_c.SendCommand(Command="/file/stop-transcript")',
        'wb_script_result=json.dumps({"status":"iterate_command_returned"})'
    ]
    return '\n'.join(lines) + '\n'


def reserve_job(output_dir, system, iterations, project, container='Setup'):
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'active_solve.lock'
    token = uuid.uuid4().hex
    # Keep legacy solve lease for backward compatibility and add a shared
    # project/system/container lease preventing concurrent publication.
    from af_target_lock import acquire_target,release_confirmed
    app_dir=root.parent if root.name=='solver_tasks' else root
    target_lock=acquire_target(app_dir,project,system,container,token,'solve')
    try:
        with lock.open('x', encoding='utf-8') as f:
            f.write(json.dumps({'job_id': token, 'system': system, 'at': timestamp()}))
    except BaseException:
        release_confirmed(target_lock,token)
        raise
    folder = root / token
    try:
        folder.mkdir(mode=0o700)
        state = {'job_id': token, 'status': 'reserved', 'system': system,
                 'requested_iterations': iterations, 'project': project,
                 'started_at': timestamp(), 'convergence_certified': False,
                 'shared_target_lock':str(target_lock), 'target_container':container}
        atomic_json(folder / 'job.json', state)
        return folder, lock, state
    except BaseException:
        lock.unlink(missing_ok=True)
        release_confirmed(target_lock,token)
        raise


def finish_job(folder, lock, state, confirmed):
    atomic_json(folder / 'job.json', state)
    if confirmed:
        from af_target_lock import release_confirmed
        if state.get('shared_target_lock'):
            release_confirmed(state['shared_target_lock'],state['job_id'])
        # A stale/foreign lease must never be removed by this task.
        try:
            owner = json.loads(lock.read_text(encoding='utf-8'))
            if owner.get('job_id') == state['job_id']:
                lock.unlink()
        except (OSError, ValueError):
            pass


def execute(wb, systems, system, iterations, output_dir, expected_project='', project_reader=None):
    if not SYSTEM_RE.fullmatch(system):
        raise ValueError('Invalid target system name')
    if type(iterations) is not int or not 1 <= iterations <= 50:
        raise ValueError('Must explicitly authorize 1..50 solver iterations')
    if expected_project:
        if project_reader is None:
            raise RuntimeError('Project identity reader unavailable')
        actual = project_reader(wb).get('project_file', '')
        if not actual or os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(expected_project)):
            raise RuntimeError('Workbench project identity changed; no iterate dispatched')
    matches = [s for s in systems if s.get('name') == system and s.get('fluent')]
    if len(matches) != 1 or matches[0].get('container') not in ('Setup', 'Solution'):
        raise RuntimeError('No unique verified Fluent target; no iterate dispatched')
    folder, lock, state = reserve_job(output_dir, system, iterations, expected_project,matches[0]['container'])
    transcript = folder / 'solve.trn'
    receipt = folder / 'wb_receipt.json'
    # A script validation failure is local: release our lease without contacting WB.
    try:
        script = make_wb_script(system, matches[0]['container'], transcript, receipt, iterations)
    except Exception:
        state['status'] = 'rejected_before_dispatch'
        finish_job(folder, lock, state, confirmed=True)
        raise
    state.update(status='dispatching', dispatch_at=timestamp())
    atomic_json(folder / 'job.json', state)
    stop_monitor = threading.Event()
    def monitor_transcript():
        while not stop_monitor.wait(1.5):
            if not transcript.is_file():
                continue
            try:
                # Avoid unbounded reads even for lengthy solver runs.
                with transcript.open('rb') as f:
                    f.seek(0, 2)
                    length=f.tell()
                    f.seek(max(0,length-1024*1024))
                    content=f.read().decode('utf-8','replace')
                observation=parse_residuals(content)
                observation['observed_at']=timestamp()
                observation['source_bytes']=length
                atomic_json(folder/'residuals.json', observation)
            except (OSError, ValueError):
                pass
    thread=threading.Thread(target=monitor_transcript, daemon=True)
    thread.start()
    try:
        raw = wb.run_script_string(script)  # ONE non-idempotent dispatch only.
        state['wb_return_type'] = type(raw).__name__
        if receipt.exists():
            try:
                state['remote_receipt'] = json.loads(receipt.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state['remote_receipt'] = {'state': 'unreadable'}
        if transcript.exists():
            with transcript.open('rb') as f:
                f.seek(0,2)
                total=f.tell()
                f.seek(max(0,total-2*1024*1024))
                content=f.read().decode('utf-8','replace')
            state['transcript_bytes'] = transcript.stat().st_size
            residue = parse_residuals(content)
            atomic_json(folder / 'residuals.json', residue)
            state['residual_rows'] = residue['row_count']
        else:
            state['transcript_bytes'] = 0
        # A WB receipt is a command acknowledgement, NOT enough. It is possible
        # that Fluent returned without doing any iterations (already converged).
        receipt_returned = state.get('remote_receipt', {}).get('state') == 'iterate_returned'
        rows = state.get('residual_rows', 0)
        confirmed = receipt_returned and rows > 0
        state['status'] = 'residuals_observed_not_convergence_certified' if confirmed else 'remote_execution_unconfirmed'
        state['finished_at'] = timestamp()
        state['convergence_certified'] = False
        finish_job(folder, lock, state, confirmed)
        return dict(state, transcript=str(transcript), residual_file=str(folder / 'residuals.json'),
                    lock_retained=not confirmed)
    except BaseException as exc:
        state.update(status='remote_execution_unconfirmed', error=repr(exc), finished_at=timestamp(),
                     convergence_certified=False)
        finish_job(folder, lock, state, confirmed=False)
        raise RuntimeError('WB command outcome unknown, lease retained; never auto retry: ' + str(exc)) from exc
    finally:
        stop_monitor.set()
        thread.join(timeout=2.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--security', choices=('mtls', 'insecure', 'wnua'), required=True)
    p.add_argument('--system', required=True)
    p.add_argument('--iterations', type=int, required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--expected-project', default='')
    args = p.parse_args()
    try:
        if not 1024 <= args.port <= 65535:
            raise ValueError('Invalid port')
        if not os.path.isabs(args.output_dir):
            raise ValueError('Output dir must be absolute')
        from af_wb_bridge import connect, _wb_only_handshake, list_fluent_systems, read_project_identity
        wb = connect(args.port, args.security)
        _wb_only_handshake(wb)
        result = execute(wb, list_fluent_systems(wb), args.system, args.iterations,
                         args.output_dir, args.expected_project, read_project_identity)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'error_or_unknown', 'detail': str(exc)}, ensure_ascii=False))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
