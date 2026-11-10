#!/usr/bin/env python3
"""Insert a valid Windows RT_ICON / RT_GROUP_ICON into a built 64-bit PE.
Uses only the Python standard library; safe for the Go Windows GUI executable.
Run after `go build`, not on a signed binary. Refuses to overwrite existing resources.
"""
import struct
import sys
from pathlib import Path

def u16(data, i): return struct.unpack_from('<H', data, i)[0]
def u32(data, i): return struct.unpack_from('<I', data, i)[0]
def align(n,a): return (n+a-1)//a*a

def parse_ico(data):
    reserved,typ,n=struct.unpack_from('<HHH',data,0)
    if reserved or typ!=1 or not (1<=n<=20): raise ValueError('ICO header invalid')
    frames=[];group=bytearray(struct.pack('<HHH',0,1,n))
    for idx in range(n):
        ent=data[6+16*idx:6+16*(idx+1)]
        w,h,colors,res,planes,bits,size,off=struct.unpack('<BBBBHHII',ent)
        if off+size>len(data) or size<40: raise ValueError('ICO frame bounds invalid')
        frames.append(data[off:off+size])
        group += struct.pack('<BBBBHHIH',w,h,colors,res,planes,bits,size,idx+1)
    return frames,bytes(group)

def build_resource(frames,group,rva):
    # root: type 3=RT_ICON, type 14=RT_GROUP_ICON; leaf = lang 1033
    tree={3:{i+1:{1033:data} for i,data in enumerate(frames)},14:{1:{1033:group}}}
    data=bytearray();leaves=[]
    def reserve(size):
        off=len(data);data.extend(b'\0'*size);return off
    def directory(node):
        pairs=sorted(node.items());base=reserve(16+8*len(pairs))
        struct.pack_into('<IIHHHH',data,base,0,0,0,0,0,len(pairs))
        for idx,(key,val) in enumerate(pairs):
            e=base+16+idx*8
            struct.pack_into('<I',data,e,key)
            if isinstance(val,dict):
                child=directory(val);struct.pack_into('<I',data,e+4,child|0x80000000)
            else:
                entry=reserve(16);struct.pack_into('<I',data,e+4,entry)
                leaves.append((entry,val))
        return base
    directory(tree)
    for entry,blob in leaves:
        while len(data)%4:data.append(0)
        off=len(data);data.extend(blob)
        struct.pack_into('<IIII',data,entry,rva+off,len(blob),0,0)
    return data

def embed(exe,ico):
    payload=bytearray(Path(exe).read_bytes())
    icon_frames,icon_group=parse_ico(Path(ico).read_bytes())
    if payload[:2]!=b'MZ':raise ValueError('not MZ')
    pe=u32(payload,0x3c)
    if payload[pe:pe+4]!=b'PE\0\0':raise ValueError('not a PE executable')
    num=u16(payload,pe+6);optsz=u16(payload,pe+20);opt=pe+24
    if u16(payload,opt)!=0x20b:raise ValueError('expected PE32+ Windows x64')
    sectalign=u32(payload,opt+32);filealign=u32(payload,opt+36)
    headers=u32(payload,opt+60)
    table=opt+optsz
    if table+40*(num+1)>headers:raise ValueError('no safe extra section header slot')
    if u32(payload,opt+112+16)!=0:raise ValueError('PE already has resources; refusing overwrite')
    last=table+(num-1)*40
    last_rva=u32(payload,last+12)
    last_vs=u32(payload,last+8)
    last_raw=u32(payload,last+16)
    new_rva=align(last_rva+max(last_vs,last_raw),sectalign)
    resource=build_resource(icon_frames,icon_group,new_rva)
    new_raw=align(len(payload),filealign)
    new_raw_sz=align(len(resource),filealign)
    if new_raw>len(payload):payload.extend(b'\x00'*(new_raw-len(payload)))
    payload.extend(resource)
    payload.extend(b'\x00'*(new_raw_sz-len(resource)))
    new_section=struct.pack('<8sIIIIIIHHI',b'.rsrc\0\0\0',len(resource),new_rva,new_raw_sz,new_raw,0,0,0,0,0x40000040)
    payload[table+40*num:table+40*(num+1)]=new_section
    struct.pack_into('<H',payload,pe+6,num+1)
    struct.pack_into('<I',payload,opt+56,align(new_rva+len(resource),sectalign))
    struct.pack_into('<II',payload,opt+112+8*2,new_rva,len(resource))
    Path(exe).write_bytes(payload)
    return dict(frames=len(icon_frames),resource_bytes=len(resource),rva=hex(new_rva),exe_bytes=len(payload))

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('usage: embed_ico_pe.py AutoFluent.exe assets/AutoFluent.ico')
    print(embed(sys.argv[1],sys.argv[2]))
