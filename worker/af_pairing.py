# -*- coding: utf-8 -*-
"""Shared read-only Fluent Case/Data pairing by Data's actual Case File pointer.

Never infer a snapshot from a numbered data filename or silently choose from
multiple matching saved solution snapshots. Pure metadata reads, no mesh load.
"""
from pathlib import Path

CAS_SUFFIX = '.cas.h5'
DAT_SUFFIX = '.dat.h5'


def referenced_case_basename(data):
    """Read only the small settings/Case File link; None if not available."""
    try:
        import h5py
        with h5py.File(data, 'r') as f:
            if 'settings/Case File' not in f:
                return None
            ds=f['settings/Case File']
            if ds.size!=1:
                raise ValueError('Data has an ambiguous settings/Case File pointer')
            raw=ds[0] if ds.ndim else ds[()]
            value=raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)
            name=value.replace('\\','/').split('/')[-1]
            if not name.lower().endswith(CAS_SUFFIX):
                raise ValueError('Data settings/Case File does not name a .cas.h5')
            return name
    except (OSError,ImportError):
        return None


def resolve_saved_pair(selected, *, explicit_case=None, explicit_data=None):
    p=Path(selected).expanduser().resolve()
    if not p.is_file():raise FileNotFoundError('所选 Fluent 文件不存在：'+str(p))
    name=p.name.casefold()
    if not name.endswith((CAS_SUFFIX,DAT_SUFFIX)):
        raise ValueError('请选择 .cas.h5 或 .dat.h5')
    if name.endswith(DAT_SUFFIX):
        data=p
        ref=referenced_case_basename(data)
        if explicit_case:
            case=Path(explicit_case).expanduser().resolve()
            if ref and ref.casefold()!=case.name.casefold():
                raise ValueError('Explicit Case disagrees with Data settings/Case File: '+ref)
        elif ref:
            matches=[f for f in p.parent.iterdir() if f.is_file() and f.name.casefold()==ref.casefold()]
            if len(matches)!=1:
                raise FileNotFoundError('Data references '+ref+' but exactly one matching Case is not present in '+str(p.parent))
            case=matches[0]
        else:
            stem=p.name[:-len(DAT_SUFFIX)].casefold()
            cases=[f for f in p.parent.iterdir() if f.is_file() and f.name.casefold()==stem+CAS_SUFFIX]
            case=cases[0] if len(cases)==1 else p.with_name(p.name[:-len(DAT_SUFFIX)]+CAS_SUFFIX)
    else:
        case=p
        if explicit_data:
            data=Path(explicit_data).expanduser().resolve()
            ref=referenced_case_basename(data)
            if ref and ref.casefold()!=case.name.casefold():
                raise ValueError('Specified Data references another Case: '+ref)
        else:
            matches=[]
            for f in p.parent.iterdir():
                if f.is_file() and f.name.casefold().endswith(DAT_SUFFIX):
                    ref=referenced_case_basename(f)
                    if ref and ref.casefold()==case.name.casefold():
                        matches.append(f)
            if len(matches)>1:
                raise ValueError('Multiple Data snapshots reference this Case; select the desired .dat.h5 explicitly: '+
                                 '; '.join(sorted(f.name for f in matches)[:24]))
            if matches:
                data=matches[0]
            else:
                stem=p.name[:-len(CAS_SUFFIX)].casefold()
                candidates=[f for f in p.parent.iterdir() if f.is_file() and f.name.casefold()==stem+DAT_SUFFIX]
                data=candidates[0] if len(candidates)==1 else p.with_name(p.name[:-len(CAS_SUFFIX)]+DAT_SUFFIX)
    if not case.is_file() or not data.is_file():
        raise FileNotFoundError('缺少 Case/Data 配对文件: '+str(case if not case.is_file() else data))
    if case.samefile(data):raise ValueError('Case 与 Data 不能是同一个文件')
    return case,data
