"""File-only bridge for Fluent's legacy path receiver; public names stay Unicode."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import hashlib
import os
from pathlib import Path
import shutil
import uuid

_owned=set()

def transport_file(path,read=False):
    path=Path(path).resolve()
    if str(path).isascii():return path
    root=Path(os.environ.get('AUTOFLUENT_TRANSPORT_DIR',str(Path(__file__).resolve().parents[1]/'.autofluent-transfer'))).absolute()
    if not str(root).isascii():raise ValueError('Fluent 内部传输目录需要可用的 ASCII 路径；中文成果路径不会被修改')
    for part in (root,*root.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('内部传输目录不能包含链接')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=root/(uuid.uuid4().hex+path.suffix.lower())
    if target.exists():raise FileExistsError(target)
    if read:
        with path.open('rb') as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
        if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(path.read_bytes()).digest():
            raise RuntimeError('工况内部传输校验失败')
    _owned.add(target)
    return target

def discard_transport(path):
    path=Path(path)
    if path not in _owned:return
    if path.exists():
        st=path.lstat()
        if path.is_symlink() or st.st_nlink>1 or getattr(st,'st_file_attributes',0)&0x400:
            raise RuntimeError('传输文件身份异常，已保留')
        path.unlink()
    _owned.discard(path)

def publish_export(transport,destination):
    transport=Path(transport);destination=Path(destination)
    if transport==destination:return
    if transport not in _owned:raise ValueError('不是本次创建的传输文件')
    content=transport.read_bytes()
    with destination.open('xb') as f:f.write(content)
    if destination.read_bytes()!=content or destination.name not in [p.name for p in destination.parent.iterdir()]:
        raise RuntimeError('工况文件保存或中文名称读回失败')
    discard_transport(transport)
