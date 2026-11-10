"""Workbench server bootstrap compatible with AutoMesh, for standalone AF installs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from af_wb_bridge import listeners


def bootstrap():
    before = listeners()
    helper=Path(__file__).with_name('af_start_wb_server_uia.ps1')
    if os.name!='nt' or not helper.is_file():
        return {'status':'unavailable','detail':'Win32 helper missing','candidate_ports':[]}
    exe=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    if not exe.is_file(): exe='powershell.exe'
    output=subprocess.run([str(exe),'-NoLogo','-NoProfile','-NonInteractive','-STA',
                           '-ExecutionPolicy','Bypass','-File',str(helper),'-Mode','start'],
                          capture_output=True,timeout=22)
    result={}
    for line in reversed(output.stdout.decode('utf-8-sig','replace').splitlines()):
        try:
            result=json.loads(line)
            if isinstance(result,dict) and 'status' in result: break
        except ValueError: pass
    if result.get('status')!='submitted':
        return {'status':'unavailable','detail':result.get('detail','No helper result'), 'candidate_ports':[]}
    pid=int(result.get('process_id',0))
    ports=set()
    for _ in range(12):
        new=listeners()-before
        ports={p for p,i in new if i==pid} or {p for p,_ in new}
        if ports: break
        time.sleep(0.5)
    return {'status':'submitted','detail':result.get('detail',''), 'candidate_ports':sorted(ports),'process_id':pid}

if __name__=='__main__':
    try: result=bootstrap()
    except Exception as exc: result={'status':'unavailable','detail':str(exc)[:500],'candidate_ports':[]}
    print(json.dumps(result,ensure_ascii=False))
