"""Conservative comparison of Fluent .set data; never evaluates Scheme."""

import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import re

TOKEN=re.compile(r';[^\n]*|[()]|"(?:\\.|[^"\\])*"|[^\s();"]+',re.S)

def forms(text):
    tokens=[];end=0
    for m in TOKEN.finditer(text):
        if text[end:m.start()].strip():raise ValueError('Invalid settings token')
        end=m.end()
        if not m.group().startswith(';'):tokens.append(m.group())
        if len(tokens)>1000000:raise ValueError('Too many settings tokens')
    if text[end:].strip():raise ValueError('Incomplete settings token')
    index=0
    def node(depth=0):
        nonlocal index
        if depth>128 or index>=len(tokens):raise ValueError('Invalid settings nesting')
        token=tokens[index];index+=1
        if token==')':raise ValueError('Unexpected closing parenthesis')
        if token!='(':return token
        out=[]
        while index<len(tokens) and tokens[index]!=')':out.append(node(depth+1))
        if index>=len(tokens):raise ValueError('Unclosed settings list')
        index+=1
        return tuple(out)
    out=[]
    while index<len(tokens):out.append(node())
    return tuple(out)

def named(items):
    out={}
    for item in items:
        if not isinstance(item,tuple) or not item or not isinstance(item[0],str) or (item[0] in out and out[item[0]]!=item):
            raise ValueError('Duplicate or invalid settings entry')
        out[item[0]]=item
    return out

def read_parts(text):
    data=forms(text);rp=[f for f in data if isinstance(f,tuple) and f and f[0]=='rp']
    if len(rp)!=1 or len(rp[0])!=2:raise ValueError('Expected one settings rp block')
    other=[];views=[]
    for f in data:
        if f is rp[0]:continue
        if not isinstance(f,tuple) or not f or f[0] not in ('bc','ni','dv','cx1'):
            raise ValueError('Unknown settings section')
        if f[0]=='ni':
            if len(f)!=2 or any(not isinstance(v,tuple) or len(v)!=2 or not v[0].isdigit() for v in f[1]):raise ValueError('Unknown ni shape')
            names=tuple(sorted(v[1] for v in f[1]))
            if len(set(names))!=len(names):raise ValueError('Duplicate zone alias')
            f=('ni',names)
        if f[0]=='cx1':
            if len(f)!=2 or len(f[1])!=1:raise ValueError('Unknown cx1 shape')
            entries=named(f[1][0]);views.append(entries.pop('view-list',None))
            f=('cx1',tuple(sorted(entries.items())))
        other.append(f)
    headers=[f[1] for f in other if f[0]=='bc']
    if len(set(headers))!=len(headers):raise ValueError('Duplicate boundary region')
    return named(rp[0][1]),tuple(sorted(other,key=lambda f:(f[0],repr(f[1]) if f[0]=='bc' else ''))),tuple(views)

def replace_material(value,old,new):
    if value==('material','.',old):return ('material','.',new)
    return tuple(replace_material(v,old,new) for v in value) if isinstance(value,tuple) else value

def context_equal(before,after,a,b):
    left=named(before[1]);right=named(after[1])
    if set(left)!=set(right):return False
    current=a.get('context/current-r17+')
    if current!=b.get('context/current-r17+'):return False
    active=current[1] if current and len(current)==2 and isinstance(current[1],tuple) else ()
    def equal(x,y,allow):
        if x==y:return True
        if not isinstance(x,tuple) or not isinstance(y,tuple) or len(x)!=len(y):return False
        if allow and len(x)==7 and isinstance(x[0],str) and x[0]==y[0] and x[0] in a:
            actual=a[x[0]]
            if actual==b.get(x[0]) and len(actual)==2 and x[2] in ('integer','boolean','real') and x[1]==y[1]==actual[1] and y[5]==actual[1] and x[:5]==y[:5] and x[6:]==y[6:]:
                return True
        return all(equal(u,v,allow) for u,v in zip(x,y))
    return all(equal(left[k],right[k],k in active) for k in left)

def compare_settings(source,after):
    if source==after:return {'match':True,'mode':'byte_equal','normalizations':[],'changed_keys':[]}
    try:
        a,ab,av=read_parts(source);b,bb,bv=read_parts(after)
        if ab!=bb:return {'match':False,'changed_keys':['boundary_or_zone_sections'],'normalizations':[]}
        changed=sorted(k for k in set(a)|set(b) if a.get(k)!=b.get(k));normal=['cx1/view-list：仅视图状态变化'] if av!=bv else [];unmatched=[]
        materials_equal=False;materials={}
        if 'materials' in a and 'materials' in b:
            materials=named(a['materials'][1]);materials_equal=materials==named(b['materials'][1])
        zone_materials=set()
        for f in ab:
            if f[0]=='bc' and len(f)==3 and len(f[1])>=2 and f[1][1]=='fluid':
                for v in f[2]:
                    if isinstance(v,tuple) and len(v)==3 and v[:2]==('material','.'):
                        zone_materials.add(v[2])
        zone=next(iter(zone_materials)) if len(zone_materials)==1 else None
        single=a.get('sg-mphase?')==b.get('sg-mphase?')==('sg-mphase?','#f')
        cache_rebuilt=(single and materials_equal and zone in materials and 'air' not in materials and
            'domains' in a and 'domains' in b and a['domains']!=b['domains'] and
            replace_material(a['domains'],'air',zone)==b['domains'])
        for k in changed:
            if k in ('gui-processing?','view-list'):
                normal.append(k+': display/runtime state')
            elif k=='materials' and materials_equal:
                normal.append('materials：材料属性相同，仅列表顺序变化')
            elif k=='context/map-r17+' and k in a and k in b and context_equal(a[k],b[k],a,b):
                normal.append(k+': active-context restore cache rebuilt; actual solver values unchanged')
            elif k=='domains' and cache_rebuilt:
                normal.append('domains：失效的默认 air 缓存按未变的实际流体区材料 '+zone+' 重建')
            elif k=='mp/n-materials' and cache_rebuilt and a[k]==(k,'1') and b[k]==(k,'0'):
                normal.append(k+'：单相模型的非活动多相缓存计数')
            else:unmatched.append(k)
        return {'match':not unmatched,'mode':'parsed_settings_with_explicit_normalizations','normalizations':normal,
                'changed_keys':changed,'unmatched_keys':unmatched,'boundary_sections_equal':True}
    except (ValueError,TypeError,IndexError,RecursionError) as exc:
        return {'match':False,'mode':'unverified','detail':str(exc),'normalizations':[]}

def compare_settings_files(source,after):
    from pathlib import Path
    a=Path(source).read_bytes();b=Path(after).read_bytes()
    if a==b:return {'match':True,'mode':'byte_equal','normalizations':[],'changed_keys':[]}
    try:return compare_settings(a.decode('utf-8-sig'),b.decode('utf-8-sig'))
    except UnicodeError as exc:return {'match':False,'mode':'unverified','detail':str(exc),'normalizations':[]}

def zone_signature(path):
    from pathlib import Path
    _,other,_=read_parts(Path(path).read_text(encoding='utf-8-sig'))
    headers=[f[1] for f in other if f[0]=='bc']
    if not headers:raise ValueError('设置文件没有可核验的区域，未提交导入')
    return tuple(sorted(headers))
