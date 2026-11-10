# -*- coding: utf-8 -*-
"""One durable on-disk per-target lease shared by settings and solve.

The lock is intentionally retained on unknown WB/Fluent outcomes, and can
only be cleared via an explicit independent recovery check outside this code.
"""
import hashlib
import json
import os
from pathlib import Path
import uuid


def target_lock_path(app_dir,project,system,container):
    if not system or container not in ('Setup','Solution'):
        raise ValueError('Unverified Fluent target key')
    identity={'project':os.path.normcase(os.path.normpath(project or '')).casefold(),
              'system':str(system).casefold(),'container':container.casefold()}
    token=hashlib.sha256(json.dumps(identity,sort_keys=True).encode('utf-8')).hexdigest()[:32]
    directory=Path(app_dir).resolve()/'.fluent_target_locks'
    directory.mkdir(parents=True,exist_ok=True)
    return directory/(token+'.lock')


def acquire_target(app_dir,project,system,container,job_id,kind):
    lock=target_lock_path(app_dir,project,system,container)
    record={'job_id':str(job_id),'kind':kind,'project':project or '',
            'system':system,'container':container}
    try:
        with lock.open('x',encoding='utf-8') as handle:
            json.dump(record,handle)
            handle.flush();os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FileExistsError('Same Fluent target already has an active or unconfirmed settings/solve operation: '+str(lock)) from exc
    return lock


def release_confirmed(lock,job_id):
    path=Path(lock)
    try:
        owner=json.loads(path.read_text(encoding='utf-8'))
        if owner.get('job_id')==str(job_id):
            path.unlink();return True
    except (OSError,ValueError):
        pass
    return False
