# -*- coding: utf-8 -*-
"""AutoMesh connection core, transplanted from the user's AutoMesh repository.

Source: 1463503175-pixel/AutoMesh, source/assets/workbench_bridge.py
The connect_existing, read_only_probe, list_systems, and their dependencies
below are copied from the AM source. Meshing-specific dispatch_job is excluded:
AutoFluent must not execute Meshing ACT actions.
"""
import json
import re
import os
import uuid
from pathlib import Path

LIST_SCRIPT = r'''import json
items=[]
for s in GetAllSystems():
    name=''
    try:
        name=str(s.Name)
    except Exception:
        continue
    try:
        label=str(s.DisplayText)
    except Exception:
        label=name
    if not label:
        label=name
    has_mesh=False
    try:
        has_mesh=(s.GetContainer(ComponentName='Mesh') is not None)
    except Exception:
        try:
            has_mesh=(s.GetComponent(Name='Mesh') is not None)
        except Exception:
            pass
    items.append({'name':name,'label':label,'mesh':has_mesh})
wb_script_result=json.dumps(items)
'''

PROBE_SCRIPT = "import json\nwb_script_result=json.dumps('AUTOMESH_PROBE_OK')"


def _missing_return_value(raw):
    return raw is None or (isinstance(raw,str) and raw.strip().lower() in ('','none','null'))


def _decode_script_result(raw, purpose):
    """WorkbenchClient may return JSON text or an already-decoded object.

    None must never reach json.loads. It indicates a script result transfer
    problem or an exception on the Workbench side, not a user security error.
    """
    if raw is None:
        raise RuntimeError('%s did not return wb_script_result (received None); check the Workbench command log / unsupported script properties' % purpose)
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, bytes):
        raw=raw.decode('utf-8-sig','replace')
    if not isinstance(raw, str):
        raise RuntimeError('%s returned unexpected response type %s' % (purpose,type(raw).__name__))
    if not raw.strip():
        raise RuntimeError('%s returned empty text instead of a JSON value' % purpose)
    try:
        obj=json.loads(raw)
        if isinstance(obj,str) and obj[:1] in ('[','{'):
            obj=json.loads(obj)
        return obj
    except (TypeError,ValueError) as exc:
        raise RuntimeError('%s returned non-JSON output (first 100 chars: %r): %s' % (purpose,raw[:100],exc))


def valid_port(port):
    p = int(port)
    if not 1 <= p <= 65535:
        raise ValueError('Workbench server port must be 1..65535')
    return p


def connect_existing(port, security='insecure'):
    if security not in ('mtls', 'wnua', 'insecure'):
        raise ValueError('Unsupported security')
    from ansys.workbench.core import connect_workbench
    return connect_workbench(port=valid_port(port), host='127.0.0.1', security=security,
                             client_workdir=str(Path(__file__).resolve().parent))


def _fallback_systems_from_local_file(wb):
    """Read-only local fallback if a Workbench 2024 return channel yields None.

    Both processes are on localhost. We do NOT create a new WB or new model;
    the only side effect is a tiny JSON file in the dedicated app data folder.
    The path is unpredictable and cleaned after each attempt to reject stale data.
    """
    target=Path(__file__).resolve().parent/('wb_systems_'+uuid.uuid4().hex+'.json')
    raw_path=json.dumps(str(target).replace('\\','/'),ensure_ascii=True)
    script=LIST_SCRIPT+"\nwith open(u%s,'w') as _f:\n    _f.write(wb_script_result)\n" % raw_path
    try:
        wb.run_script_string(script)
        if not target.is_file():
            raise RuntimeError('Workbench did not write the local fallback list file; inspect Workbench command-window errors')
        return _decode_script_result(target.read_text(encoding='utf-8-sig'),'Workbench local fallback file')
    finally:
        try:target.unlink()
        except OSError:pass


def list_systems(wb):
    raw=wb.run_script_string(LIST_SCRIPT)
    if _missing_return_value(raw):
        data=_fallback_systems_from_local_file(wb)
    else:
        data=_decode_script_result(raw,'List systems')
    if not isinstance(data, list):
        raise RuntimeError('Workbench returned %s, expected system list' % type(data).__name__)
    result=[]
    for n,item in enumerate(data):
        if not isinstance(item,dict):
            raise RuntimeError('System row %d is not a JSON object' % n)
        name=str(item.get('name') or '')
        if not name:
            continue
        result.append({'name':name,'label':str(item.get('label') or name),
                       'mesh':bool(item.get('mesh',False))})
    return result


def read_only_probe(wb):
    """Optional diagnostics only, never a mandatory connection gate.

    PyWorkbench 0.14 can return either an already-decoded string or the JSON
    serialization itself. A healthy session returning the bare sentinel must
    not be mistaken for a malformed JSON result.
    """
    raw=wb.run_script_string(PROBE_SCRIPT)
    if _missing_return_value(raw):
        return 'AUTOMESH_PROBE_NO_RETURN'
    if isinstance(raw, bytes):
        raw=raw.decode('utf-8-sig','replace')
    if raw == 'AUTOMESH_PROBE_OK':
        return raw
    result=_decode_script_result(raw,'Workbench return-value probe')
    if result != 'AUTOMESH_PROBE_OK':
        raise RuntimeError('Workbench returned an unexpected probe value: %r' % result)
    return result
