# -*- coding: utf-8 -*-
"""Read-only report capture for an already-open Workbench Fluent editor.

No solver launch, initialization, iteration, case/data save or setting changes.
The only temporary solver action is a transcript bracket around the built-in
/report/summary and /file/show-configuration reports. This is *not* a mass-flux
postprocessor and cannot certify physical-condition equality.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

_SYSTEM = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')


def report_path(folder):
    """Create a unique target in the app-owned output dir (no user file overwritten)."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('Report destination directory does not exist')
    return root / ('fluent_live_%s_%s.trn' % (time.strftime('%Y%m%d_%H%M%S'), uuid.uuid4().hex[:12]))


def fluent_tui_argument(path):
    """Quote local windows paths; reject characters that can alter TUI commands."""
    value = str(path).replace('\\', '/')
    if not os.path.isabs(str(path)):
        raise ValueError('Transcript path must be absolute')
    if any(c in value for c in ('"', '\r', '\n', '\x00', ';')):
        raise ValueError('Unsafe Fluent transcript path')
    return '"%s"' % value


def build_script(system, container, path):
    """Workbench IronPython script; commands execute once, never auto-retry.

    A preexisting transcript must NOT be stopped. Start-transcript is accepted
    only if our unique output file was created; if not, fail closed. When a
    report command fails, stop only the transcript that we started.
    """
    if not _SYSTEM.fullmatch(system):
        raise ValueError('Invalid Workbench system name')
    if container not in ('Setup', 'Solution'):
        raise ValueError('Invalid Fluent container')
    transcript = fluent_tui_argument(path)
    start = '/file/start-transcript ' + transcript
    # Only TUI commands documented by Fluent 2024 R2 are used.
    lines = [
        'import os, json',
        '_af_system=None',
        '_af_count=0',
        'for _af_s in GetAllSystems():',
        '    if str(_af_s.Name)==%s:' % json.dumps(system),
        '        _af_system=_af_s',
        '        _af_count+=1',
        'if _af_count!=1: raise Exception("Target system missing or duplicate")',
        '_af_c=_af_system.GetContainer(ComponentName=%s)' % json.dumps(container),
        'if _af_c is None: raise Exception("Target Fluent container is missing")',
        '_af_c.GetFluentLauncherSettings()',
        '_af_path=u%s' % json.dumps(str(path).replace('\\', '/')),
        'if os.path.exists(_af_path): raise Exception("Refusing to overwrite transcript")',
        '_af_c.SendCommand(Command=%s)' % json.dumps(start),
        'if not os.path.isfile(_af_path):',
        '    raise Exception("Transcript did not start; no stop command sent")',
        'try:',
        '    _af_c.SendCommand(Command="/report/summary")',
        '    _af_c.SendCommand(Command="/file/show-configuration")',
        'finally:',
        '    _af_c.SendCommand(Command="/file/stop-transcript")',
        'wb_script_result=json.dumps({"status":"commands_returned"})',
    ]
    return '\n'.join(lines) + '\n'


def examine_report(path, system, project, max_bytes=8*1024*1024):
    """Validate actual bytes, not just a Workbench acknowledgement."""
    file = Path(path)
    if not file.is_file():
        return {'status':'report_unconfirmed', 'detail':'No transcript file found'}
    size = file.stat().st_size
    if size < 200 or size > max_bytes:
        return {'status':'report_unconfirmed','detail':'Transcript size not plausible','bytes':size}
    content = file.read_bytes()
    # Print-only TUI reports may vary by Fluent locale/version. Marker check is
    # purposefully conservative; the raw transcript is retained for review.
    text = content.decode('utf-8-sig',errors='replace').lower()
    markers = [p for p in ('boundary conditions','material properties','solver controls',
                           'models','fluent','operating conditions','pressure-based',
                           'conditions aux limites','边界条件','材料') if p in text]
    has_report = any(x in text for x in ('boundary conditions','material properties',
                                         'operating conditions','边界条件','材料'))
    result = {'status':'report_captured' if has_report else 'report_unverified',
              'file':str(file.resolve()), 'bytes':size,
              'sha256':hashlib.sha256(content).hexdigest(),
              'system':system, 'project_file':project,
              'markers':markers,
              'scope':'Fluent console summary/configuration only; no native face flux; no per-channel analysis',
              'verification':'source_file_exists_not_complete_conditions_certification'}
    if not has_report:
        result['detail']='Transcript exists, but expected summary sections are absent; inspect before use.'
    return result


def capture_live_report(wb, systems, selection, output_dir, expected_project='', project_reader=None):
    """Trusted caller requires verified WB port, unique selected Fluent and project."""
    if not _SYSTEM.fullmatch(selection):
        raise ValueError('Invalid selected system')
    matching = [x for x in systems if x.get('name')==selection and x.get('fluent') is True]
    if len(matching) != 1:
        raise ValueError('Fluent target not unique / not in Workbench list')
    if expected_project:
        if project_reader is None:
            raise ValueError('Missing project identity reader')
        actual = project_reader(wb).get('project_file','')
        if not actual or os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(expected_project)):
            raise RuntimeError('Workbench project changed; abort before sending any command')
    output = report_path(output_dir)
    script = build_script(selection, matching[0].get('container') or 'Setup',output)
    # One and only one dispatch; no retry even if a transport error or None.
    try:
        wb.run_script_string(script)
    except Exception as exc:
        result = examine_report(output, selection, expected_project)
        result.update(status='report_dispatch_uncertain', detail='Workbench command return failed; do not automatically repeat: '+str(exc)[:180])
        return result
    for _ in range(8):
        if output.is_file() and output.stat().st_size>=200:
            break
        time.sleep(.25)
    return examine_report(output, selection, expected_project)
