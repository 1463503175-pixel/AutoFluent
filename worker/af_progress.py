# -*- coding: utf-8 -*-
"""Atomic, optional, read-only-worker progress status for the native Windows GUI.

Percent denotes completed *weighted processing stages*, not elapsed-time ETA.
No fake throughput or background success is inferred from this file.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time

_LAST_PERCENT = -1
_LAST_WRITE = 0.0
_SCOPE_START = 0
_SCOPE_END = 100

def set_scope(start, end):
    """Map an internal 0-100 job to an explicitly bounded parent phase."""
    global _SCOPE_START, _SCOPE_END
    if not (0 <= start < end <= 100):raise ValueError("Invalid progress scope")
    _SCOPE_START, _SCOPE_END = int(start), int(end)



def update(percent, stage, detail='', *, completed=None, total=None, force=False):
    global _LAST_PERCENT, _LAST_WRITE
    path = os.environ.get('AUTOFLUENT_PROGRESS_FILE', '').strip()
    if not path:
        return
    val = max(0, min(100, int(_SCOPE_START + (float(percent)/100)*(_SCOPE_END-_SCOPE_START))))
    now = time.monotonic()
    if not force and val == _LAST_PERCENT and now-_LAST_WRITE < 0.8:
        return
    _LAST_PERCENT, _LAST_WRITE = val, now
    payload = {'schema': 'autofluent.task_progress.v1', 'percent': val,
               'stage': str(stage)[:75], 'detail': str(detail)[:220],
               'meaning': 'weighted_completed_stages_not_eta',
               'timestamp_ns': time.time_ns()}
    if completed is not None and total is not None:
        payload['completed_units'] = int(completed)
        payload['total_units'] = int(total)
    target=Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=target.parent, prefix='.afprogress_',
                                         suffix='.tmp', delete=False) as f:
            name = f.name
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
            f.flush()
        os.replace(name, target)
    except OSError:
        # Progress is best effort, never break the CFD computation.
        try:
            os.unlink(name)
        except (OSError, UnboundLocalError):
            pass


def portion(start, end, completed, total, stage, detail=''):
    if total <= 0:
        update(start, stage, detail)
    else:
        update(start+(end-start)*completed/total, stage, detail,
               completed=completed, total=total)


def fail(stage, detail=''):
    """Report failure without implying completed work never occurred."""
    fraction=max(0, min(99, (_LAST_PERCENT-_SCOPE_START)*100/max(1,_SCOPE_END-_SCOPE_START)))
    update(fraction, stage, detail, force=True)
