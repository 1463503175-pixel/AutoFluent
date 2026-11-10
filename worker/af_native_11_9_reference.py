from pathlib import Path
import argparse
import csv
from html import escape
parser=argparse.ArgumentParser(description="Fluent CFF 只读统计。内部几何使用 11.9 ALD 规则。")
parser.add_argument("input",type=Path,help="Case 或 Data 文件")
parser.add_argument("companion",type=Path,nargs="?",help="另一文件，默认寻找同名前缀的配对文件")
parser.add_argument("--output",type=Path,required=True,help="CSV 和 HTML 的输出路径前缀")
args=parser.parse_args()
source=args.input
if source.name.endswith(".cas.h5"):
    case=source
    data=args.companion or source.with_name(source.name[:-7]+".dat.h5")
elif source.name.endswith(".dat.h5"):
    data=source
    case=args.companion or source.with_name(source.name[:-7]+".cas.h5")
else:
    parser.error("输入应为 .cas.h5 或 .dat.h5")
for path in (case,data):
    if not path.is_file():
        parser.error(f"找不到配对文件 {path}")
csv_path=args.output.with_name(args.output.name+".csv")
html_path=args.output.with_name(args.output.name+".html")
if csv_path.exists() or html_path.exists():
    parser.error("输出已存在，请使用新前缀")
if not args.output.parent.is_dir():
    parser.error("输出目录不存在")
try:
    import h5py
    import numpy as np
except ImportError as exc:
    parser.exit(2,f"需要安装 h5py 和 numpy，或使用 ANSYS 自带且包含这两个库的 Python。{exc}\n")
rows = []
totals = []
with h5py.File(case, "r") as c, h5py.File(data, "r") as d:
    if Path(d["settings/Case File"][0].decode().replace("\\","/")).name != case.name:
        parser.error("Data 记录的 Case 文件名与指定文件不匹配")
    mesh = c["meshes/1"]
    zone = mesh["faces/zoneTopology"]
    result = d["results/1/phase-1/faces"]
    flux = result["SV_FLUX/1"]
    assert flux.attrs["minId"][0] == 1 and flux.attrs["maxId"][0] == len(flux)
    xyz = mesh["nodes/coords/4"][:]
    connectivity = mesh["faces/nodes/1"]
    names = zone["name"][0].decode().split(";")
    for name, sign, label in [("inlet", -1, "入口"), ("outlet", 1, "出口")]:
        z = names.index(name)
        start, end = int(zone["minId"][z])-1, int(zone["maxId"][z])
        assert (connectivity["nnodes"][start:end] == 3).all()
        offset = int(connectivity["nnodes"][:start].sum())
        triangles = connectivity["nodes"][offset:offset+3*(end-start)].reshape(-1,3)-1
        groups = []
        for i, triangle in enumerate(triangles):
            nodes = set(triangle)
            faces = [i]
            others = []
            for group_nodes, group_faces in groups:
                if nodes.intersection(group_nodes):
                    nodes.update(group_nodes)
                    faces.extend(group_faces)
                else:
                    others.append((group_nodes, group_faces))
            groups = others + [(nodes, faces)]
        assert len(groups) == 3
        assert sorted(i for _, faces in groups for i in faces) == list(range(end-start))
        values = sign * flux[start:end]
        assert np.isfinite(values).all()
        totals.append(values.sum())
        groups.sort(key=lambda g: tuple(xyz[sorted(g[0])].mean(axis=0)))
        for index, (_, faces) in enumerate(groups, 1):
            selected = values[faces]
            center = xyz[triangles[faces]].mean(axis=(0,1))
            rows.append([label+str(index), len(faces), " / ".join(f"{x:.6f}" for x in center),
                         f"{selected[selected>0].sum():.10g}", f"{abs(selected[selected<0].sum()):.10g}",
                         f"{selected.sum():.10g}", ""])
        rows.append([label+"合计", len(values), "", f"{values[values>0].sum():.10g}",
                     f"{abs(values[values<0].sum()):.10g}", f"{values.sum():.10g}", ""])
    error = abs(totals[0]-totals[1])
    assert totals[0] > 0 and totals[1] > 0
    rows.append(["整体质量守恒", "", "", "", "", f"{error:.10g}",
                 f"绝对不平衡量除以入口净流量为 {100*error/totals[0]:.6g}%"])
    print("net in/out, absolute error, relative percent",*totals,error,100*error/totals[0])
    boundary_start=int(zone["minId"][names.index("inlet")])-1
    def boundary_field(key):
        pieces=[]
        for field in result[key].values():
            lo,hi=int(field.attrs["minId"][0])-1,int(field.attrs["maxId"][0])
            if hi>boundary_start:
                pieces.append((max(lo,boundary_start),hi,field[max(0,boundary_start-lo):]))
        cursor=boundary_start
        for lo,hi,values in sorted(pieces,key=lambda p:p[0]):
            assert lo==cursor and len(values)==hi-lo
            cursor=hi
        assert cursor==len(flux)
        return np.concatenate([p[2] for p in sorted(pieces,key=lambda p:p[0])])
    speed2=sum(boundary_field(k)**2 for k in ("SV_U","SV_V","SV_W"))
    density=boundary_field("SV_DENSITY")
    assert np.isfinite(density).all() and np.all(density>0)
    temperature=boundary_field("SV_T")
    assert np.all(temperature>0) and np.max(speed2/(1.4*296.8*temperature))<.01
    total_pressure=result["SV_P/1"][boundary_start:] + .5*density*speed2
    boundary_flux=np.abs(flux[boundary_start:])
    split=int(zone["maxId"][names.index("inlet")])-boundary_start
    pin=np.average(total_pressure[:split],weights=boundary_flux[:split])
    pout=np.average(total_pressure[split:],weights=boundary_flux[split:])
    rows.append(["进出口总压降","","","","","",f"{pin-pout:.9g} Pa，低速近似，按局部密度与端口质量流量加权 p+ρ|u|²/2"])
    nodes=connectivity["nodes"]
    owner=mesh["faces/c0/1"][:]-1
    neighbour=mesh["faces/c1/1"][:]-1
    start=int(mesh["faces/c1/1"].attrs["minId"][0])-1
    end=start+len(neighbour)
    nc=int(mesh["cells/zoneTopology/maxId"][0])
    assert len(owner)==len(flux) and np.isfinite(flux[:]).all()
    assert (connectivity["nnodes"][:]==3).all()
    coord=np.zeros((nc,3))
    degree=np.bincount(owner,minlength=nc)+np.bincount(neighbour,minlength=nc)
    assert np.all(degree==4) and len(nodes)==3*len(owner)
    for a in range(0,len(owner),400000):
        b=min(a+400000,len(owner))
        fcenter=xyz[nodes[3*a:3*b].reshape(-1,3)-1].mean(axis=1)
        lo,hi=max(a,start),min(b,end)
        for axis in range(3):
            coord[:,axis]+=np.bincount(owner[a:b],weights=fcenter[:,axis],minlength=nc)
            if lo<hi:
                coord[:,axis]+=np.bincount(neighbour[lo-start:hi-start],weights=fcenter[lo-a:hi-a,axis],minlength=nc)
    coord/=4
    # Current wall planes identified by their large horizontal surface area.
    wall=xyz[nodes[:3*start].reshape(-1,3)-1]
    flat=np.ptp(wall[:,:,1],axis=1)<1e-9
    planes, bins=np.unique(np.round(wall[flat,0,1],7),return_inverse=True)
    tri=wall[flat]
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    floor,lower,upper=planes[np.bincount(bins,weights=areas)>.1][:3]
    assert .001 < upper-lower < .003 and .008 < lower-floor < .02
    pressure=d["results/1/phase-1/cells/SV_P/1"][:]
    plenum=(coord[:,1]>floor)&(coord[:,1]<lower)
    assert plenum.sum()>100 and np.isfinite(pressure[plenum]).all()
    p=pressure[plenum]
    rows.append(["底部集气腔静压","","","","","",
                 f"表压最小 {p.min():.9g} Pa，最大 {p.max():.9g} Pa，极差 {np.ptp(p):.9g} Pa。单元质心 Y 为 {floor:.7f} 至 {lower:.7f} m，共 {len(p)} 个单元"])
    for index, ids in enumerate(np.array_split(np.flatnonzero(plenum)[np.argsort(coord[plenum,0])],12),1):
        rows.append([f"集气腔静压区 {index}","","","","","",
                     f"X {coord[ids,0].min():.6f} 至 {coord[ids,0].max():.6f} m，单元算术均值 {pressure[ids].mean():.8g} Pa"])
    low=coord[:,1]<(lower+upper)/2
    a,b=low[owner[start:end]],low[neighbour]
    cut=np.flatnonzero(a!=b)+start
    allflux=flux[:]
    q=allflux[cut]*np.where(b[cut-start],1,-1)
    cell_residual=(np.bincount(owner,weights=allflux,minlength=nc)
                   -np.bincount(neighbour,weights=allflux[start:end],minlength=nc))
    wall_flux=allflux[:start]
    assert abs(cell_residual.sum()-(wall_flux.sum()+allflux[end:].sum()))<1e-14
    external=np.sum(wall_flux[low[owner[:start]]])+np.sum(allflux[end:][low[owner[end:]]])
    residual=cell_residual[low].sum()
    assert abs(external-q.sum()-residual)<1e-14
    data_vars=d["settings/Data Variables"][0].decode()
    import re
    metadata="；".join(re.findall(r"\([^\n()]*(?:iter|solvertime|flow-time)[^\n()]*\)",data_vars,re.I))
    rows.append(["数据来源","","","","","","Data 内部指向 "+case.name+"；原生 SV_FLUX 面索引覆盖全部网格面。"+metadata+
                 "。收敛历史需要另行核对 Fluent 日志。"])
    rows.append(["壁面通量检查",start,"","","",f"{wall_flux.sum():.10g}",
                 f"壁面绝对通量合计 {abs(wall_flux).sum():.10g} kg/s"])
    rows.append(["挡板高度全截面",len(q),"",f"{q[q>0].sum():.10g}",f"{abs(q[q<0].sum()):.10g}",
                 f"{q.sum():.10g}",f"原生网格面组成阶梯截面，截面以下控制体不平衡 {residual:.8g} kg/s，占入口 {100*abs(residual)/totals[0]:.4g}%"])
    print("pressure drop, plenum min/max, cut total",pin-pout,p.min(),p.max(),q.sum())
    cut_nodes=nodes[3*cut[0]:3*(cut[-1]+1)].reshape(-1,3)[cut-cut[0]]-1
    parent=list(range(len(cut)))
    def find(i):
        while i!=parent[i]:
            parent[i]=parent[parent[i]]
            i=parent[i]
        return i
    seen={}
    for i, tri in enumerate(cut_nodes):
        for node in tri:
            parent[find(i)]=find(seen.setdefault(int(node),i))
    patches={}
    for i in range(len(cut)):
        patches.setdefault(find(i),[]).append(i)
    holes=[]
    for ids in patches.values():
        v=xyz[cut_nodes[ids]].reshape(-1,3)
        span=np.ptp(v,axis=0)
        assert np.all((span[[0,2]]>.005)&(span[[0,2]]<.008)), span
        holes.append((v.mean(0),q[ids]))
    hole_rows=[]
    for hole in sorted(holes,key=lambda h:h[0][2]):
        if not hole_rows or hole[0][2]-hole_rows[-1][0][0][2]>.005:
            hole_rows.append([])
        hole_rows[-1].append(hole)
    assert abs(sum(v.sum() for _,v in holes)-q.sum())<1e-15
    for index, holes_in_row in enumerate(hole_rows,1):
        net=np.array([v.sum() for _,v in holes_in_row])
        values=np.concatenate([v for _,v in holes_in_row])
        rows.append([f"挡板孔排 {index}（{len(net)} 孔）",len(values),"",
                     f"{values[values>0].sum():.10g}",f"{abs(values[values<0].sum()):.10g}",f"{net.sum():.10g}",
                     f"孔净流量 CV {100*net.std()/abs(net.mean()):.4g}%，最小 {net.min():.8g}，最大 {net.max():.8g} kg/s"])
    net=np.array([v.sum() for _,v in holes])
    print("holes per row", [len(h) for h in hole_rows], "overall CV percent",100*net.std()/abs(net.mean()))
    rows.append(["挡板全部孔","","","","","",
                 f"{len(net)} 孔，孔净流量 CV {100*net.std()/abs(net.mean()):.5g}%，各排按 Z 递增"])
    wall_center=wall.mean(axis=1)
    xflat=np.ptp(wall[:,:,0],axis=1)<1e-9
    wall_area=np.linalg.norm(np.cross(wall[:,1]-wall[:,0],wall[:,2]-wall[:,0]),axis=1)/2
    # shortcut: 11.9 geometry profile, recalibrate from current wall mesh for a different layout.
    for label,y0,y1 in [("上层",-.505,-.39),("下层",-.65,-.505)]:
        selected=xflat&(wall_center[:,1]>y0)&(wall_center[:,1]<y1)
        xpos, idx=np.unique(np.round(wall_center[selected,0],7),return_inverse=True)
        xa=np.bincount(idx,weights=wall_area[selected])
        xpos=xpos[(xa>.01)&(xa<.025)]
        pair=np.flatnonzero(np.isclose(np.diff(xpos),.001,atol=1e-6))
        assert len(pair)>2 and np.all(np.diff(pair)>1)
        plate_lo,plate_hi=xpos[pair],xpos[pair+1]
        assert len(plate_lo)==96
        gaps=plate_lo[1:]-plate_hi[:-1]
        regular=np.isclose(gaps,.006,atol=1e-6)
        assert regular.sum()>0 and (~regular).sum()==1
        ref=wall[selected&(abs(wall_center[:,0]-plate_lo[len(pair)//3])<1e-7)].reshape(-1,3)
        extremes=ref[[ref[:,2].argmin(),ref[:,2].argmax()]]
        ycut=extremes[:,1].mean()
        zlo,zhi=extremes[:,2]
        zmid=(zlo+zhi)/2
        row_bounds=[]
        for side in (0,1):
            side_mask=(wall_center[:,2]>=zmid)==bool(side)
            side_x,si=np.unique(np.round(wall_center[selected&side_mask,0],7),return_inverse=True)
            side_area=np.bincount(si,weights=wall_area[selected&side_mask])
            for positions in (plate_lo,plate_hi):
                match=np.searchsorted(side_x,positions)
                assert np.array_equal(side_x[match],positions) and np.all(side_area[match]>.006)
            v=ref[(ref[:,2]>=zmid)==bool(side)]
            row_bounds.append((v[:,2].min(),v[:,2].max()))
        assert row_bounds[0][1]<row_bounds[1][0]
        rows.append([label+"晶圆数量","","","","","",
                     f"低 Z、高 Z 各 {len(plate_lo)} 片；各排壁面 Z 范围 {row_bounds} m"])
        low=coord[:,1]<ycut
        a,b=low[owner[start:end]],low[neighbour]
        cut=np.flatnonzero(a!=b)+start
        cut_ids=nodes[3*cut[0]:3*(cut[-1]+1)].reshape(-1,3)[cut-cut[0]]-1
        points=xyz[cut_ids].mean(axis=1)
        q=allflux[cut]*np.where(b[cut-start],1,-1)
        lower_region_residual=cell_residual[low].sum()
        ext=np.sum(allflux[:start][low[owner[:start]]])+np.sum(allflux[end:][low[owner[end:]]])
        assert abs(ext-q.sum()-lower_region_residual)<1e-14
        x,z=points[:,0],points[:,2]
        gap_index=np.searchsorted(plate_lo[1:],x,side="right")
        inside=(gap_index<len(gaps))&(x>=plate_hi[gap_index])&(z>=zlo)&(z<=zhi)
        safe=np.minimum(gap_index,len(gaps)-1)
        bands=[(z>=lo)&(z<=hi) for lo,hi in row_bounds]
        wafer=inside&regular[safe]&(bands[0]|bands[1])
        center=(inside&~regular[safe]) | (inside&~(bands[0]|bands[1]))
        outer=~(wafer|center)
        end_gap=outer&((x<plate_lo[0])|(x>plate_hi[-1]))
        assert not np.any(end_gap&(wafer|center))
        left=outer&(z<zmid)
        right=outer&~left
        assert np.all(wafer.astype(int)+center+left+right==1)
        assert abs(sum(q[m].sum() for m in (wafer,center,left,right))-q.sum())<1e-15
        def add_region(name, mask, note=""):
            v=q[mask]
            rows.append([label+name,len(v),"",f"{v[v>0].sum():.10g}",f"{abs(v[v<0].sum()):.10g}",
                         f"{v.sum():.10g}",f"占入口净流量 {100*v.sum()/totals[0]:.5g}%。"+note])
        channels=np.flatnonzero(regular)
        assert len(channels)==94
        all_channels=[]
        for row_label,band in zip(("低 Z 排","高 Z 排"),bands):
            channel_net=[]
            for number,i in enumerate(channels,1):
                mask=wafer&band&(gap_index==i)
                assert mask.any()
                channel_net.append(q[mask].sum())
                add_region(f"{row_label}晶圆通道 {number}",mask,f"X {plate_hi[i]:.7f} 至 {plate_lo[i+1]:.7f} m")
            all_channels.extend(channel_net)
            values=np.array(channel_net)
            add_region(row_label+"晶圆片间合计",wafer&band,
                       f"{len(values)} 通道，净流量 CV {100*values.std()/abs(values.mean()):.5g}%，最小 {values.min():.8g}、最大 {values.max():.8g} kg/s")
        channel_net=np.array(all_channels)
        assert len(channel_net)==188 and abs(channel_net.sum()-q[wafer].sum())<1e-15
        cv=100*channel_net.std()/abs(channel_net.mean())
        add_region("晶圆片间合计",wafer,f"{len(channel_net)} 通道，净流量 CV {cv:.5g}%，最小 {channel_net.min():.8g}、最大 {channel_net.max():.8g} kg/s")
        add_region("中央旁路",center)
        add_region("左外围旁路（低 Z）",left)
        add_region("右外围旁路（高 Z）",right)
        add_region("原生面全截面",np.ones(len(q),dtype=bool),
                   f"阶梯截面 Y≈{ycut:.7f} m。截面下方外边界通量 {ext:.9g} kg/s，单元原生通量散度之和 {lower_region_residual:.9g} kg/s。跨分区面按面心归属")
        add_region("X端部通路（已计入外围）",end_gap)
        for shift in (-.001,.001):
            below=coord[:,1]<ycut+shift
            aa,bb=below[owner[start:end]],below[neighbour]
            crossed=aa!=bb
            shifted=(allflux[start:end][crossed]*np.where(bb[crossed],1,-1)).sum()
            shifted_ext=wall_flux[below[owner[:start]]].sum()+allflux[end:][below[owner[end:]]].sum()
            assert abs(shifted_ext-shifted-cell_residual[below].sum())<1e-14
            rows.append([label+"截面偏移复核",int(crossed.sum()),"","","",f"{shifted:.10g}",
                         f"Y 偏移 {shift*1000:+.0f} mm，控制体差额 {shifted_ext-shifted:.9g} kg/s"])
        print(label,len(channel_net),q[wafer].sum(),cv,q[center].sum(),q[left].sum(),q[right].sum(),lower_region_residual)
headers = ["区域", "面数", "面心均值 X / Y / Z (m)", "正向 (kg/s)", "反向 (kg/s)", "净流量或绝对误差 (kg/s)", "说明"]
with csv_path.open("x", encoding="utf-8-sig", newline="") as out:
    csv.writer(out).writerows([headers, *rows])
table = "".join("<tr>"+"".join(f"<td>{escape(str(x))}</td>" for x in row)+"</tr>" for row in rows)
html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Fluent 当前解统计</title>'
html += '<style>body{font:16px system-ui;margin:2rem}td,th{border:1px solid #ccc;padding:.45rem}table{border-collapse:collapse}</style>'
html += f'<h1>Fluent 只读统计</h1><p>数据来源 {escape(str(data.resolve()))}，Case 为 {escape(str(case.resolve()))}。</p><p>使用原生 SV_FLUX。上下层使用独立阶梯截面，跨分区面按面心唯一归属。上、下层位于串联位置。挡板按互斥连通分量划孔。进出口总压以质量流量加权。</p>'
html += '<table><tr>'+"".join(f"<th>{escape(x)}</th>" for x in headers)+'</tr>'+table+'</table></html>'
with html_path.open("x",encoding="utf-8") as out:
    out.write(html)
