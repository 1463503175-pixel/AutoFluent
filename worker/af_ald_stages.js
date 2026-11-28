
(()=>{
 const root=document.querySelector('#ald-stages');
 if(!root)return;
 const q=s=>root.querySelector(s);
 const read=raw=>{const data=JSON.parse(raw);return data.meta||data;};
 const current=read(q('script[type="application/json"]').textContent);
 const signature=m=>m.provenance?.geometry_candidate_signature_not_mesh_hash;
 const time=m=>m.species_exposure?.flow_time_s;
 const kind=m=>{
   const x=m.species_exposure?.inlet_mass_fractions;
   if(!x||!['alme3','h2o','n2','ch4'].every(k=>Number.isFinite(x[k])))return -1;
   const e=1e-8;
   if(x.alme3>e&&x.h2o<=e)return 0;
   if(x.h2o>e&&x.alme3<=e)return 2;
   return x.n2>=1-e&&x.alme3<=e&&x.h2o<=e&&x.ch4<=e?1:-1;
 };
 // User-confirmed switch record for these two saved solutions from the same cycle.
 const sourceFolder=m=>String(m.provenance?.data_path||'').replaceAll(String.fromCharCode(92),'/').toLowerCase();
 const matchesCycle=m=>{
   const path=sourceFolder(m);
   const base='e:/thing/w.f/yy/fzmn/model/11.18/1.0_files/dp0/fff/fluent/';
   return [base+'last/1.dat.h5',base+'2+13+1+9/1.dat.h5'].includes(path)
     && signature(m)==='5a80bdc94d3399135f87e617e3e9fc959d0e12e46348a12a3dfe0d0411f0ec09';
 };
 const names=['TMA 供料','第一次 N₂ 吹扫','H₂O 供料','第二次 N₂ 吹扫'];
 const switches=[0,2,15,16,30];
 const expected=[0,1,2,1];
 function phase(m){
   if(!matchesCycle(m)||!Number.isFinite(time(m)))return null;
   const t=time(m), inlet=kind(m);
   const options=expected.map((v,i)=>({stage:i,inlet:v})).filter(p=>
     t>=switches[p.stage]-1e-8&&t<=switches[p.stage+1]+1e-8&&p.inlet===inlet);
   if(options.length!==1)return null;
   const i=options[0].stage;
   const side=i>0&&Math.abs(t-switches[i])<1e-8?' · 切换后':
     i<3&&Math.abs(t-switches[i+1])<1e-8?' · 切换前':
     i===3&&Math.abs(t-30)<1e-8?' · 吹扫末端':'';
   return {stage:i,caption:names[i]+side+'｜'+t.toFixed(1)+' s'+(i%2?'，累计吹扫 '+(t-switches[i]).toFixed(1)+' s':'')};
 }
 const snapshots=[[],[],[],[]];
 const inferred=kind(current);
 const recorded=phase(current);
 const classified=recorded?.stage??(inferred===0||inferred===2?inferred:-1);
 if(classified>=0)snapshots[classified].push(current);
 let stage=classified>=0?classified:inferred===1?1:0;
 let selected=null;
 const fmt=(x,d=3)=>Number.isFinite(x)?(100*x).toFixed(d)+'%':'未取得';
 const concentration=x=>Number.isFinite(x)?(100*x).toPrecision(4)+'%':'未取得';
 const frame=()=>snapshots[stage].find(m=>String(time(m))===q('[data-time]').value);
 const key=(layer,row,index)=>[layer,row,index].join('/');
 const ordered=m=>[...new Set((m.wall_reaction?.wafers||[]).map(r=>r.layer))];
 const layers=m=>[...new Set((m.geometry?.layers||[]).map(r=>r.id))];
 const layerName=(ids,id)=>ids.length===2?(ids.indexOf(id)===0?'下层':'上层'):'第 '+id+' 层';
 const plateKey=(r,i)=>key(r.layer,r.row,i);
 function render(){
   const grid=q('[data-stage-grid]');grid.replaceChildren();
   const status=q('[data-stage-status]');status.textContent='';
   const detail=q('[data-stage-detail]');detail.textContent='';
   const m=frame();
   q('[data-export-inventory]').disabled=!snapshots[stage].some(s=>s.species_exposure?.gas_inventory_kg);
   const pending=q('[data-unassigned]');
   pending.hidden=inferred!==1||snapshots.some(group=>group.some(frame=>time(frame)===time(current)));
   pending.textContent=pending.hidden?'':time(current)+' s · '+(matchesCycle(current)?'Data 时间与工艺切换记录或入口组分不一致。':'缺少可匹配的工艺切换记录，需确认属于哪一次 N₂ 吹扫。');
   if(!m){status.textContent='本阶段未取得保存帧。可载入同一工程的对应 AF 报告。';return;}
   const t=time(m);const purge=stage%2===1;const gas=q('[data-gas]').value;
   const latest=snapshots[1].filter(p=>time(p)<t).at(-1);
   const before=new Map((latest?.wall_reaction?.wafers||[]).flatMap(r=>r.plates.map((p,i)=>[plateKey(r,i),p])));
   const rows=new Map();
   const ids=purge?layers(m):ordered(m);
   function add(layer,row,entry){
     const id=key(layer,row,0);
     if(!rows.has(id))rows.set(id,{layer,row,entries:[]});
     rows.get(id).entries.push(entry);
   }
   if(purge){
     for(const c of m.species_exposure?.channels||[]){
       const layer=c.layer==='lower'?ids[0]:c.layer==='upper'?ids.at(-1):c.layer;
       const v=c.species?.[gas]?.mean;
       const x=(c.x_range_m[0]+c.x_range_m[1])*500;
       const history=snapshots[stage].map(s=>{
         const p=s.species_exposure?.channels?.find(a=>a.layer===c.layer&&a.row===c.row&&a.gap_index===c.gap_index);
         return [time(s),p?.species?.[gas]?.mean];
       }).filter(([,a])=>Number.isFinite(a));
       add(layer,c.row,{id:key(layer,c.row,c.gap_index),x,v,text:'通道 '+(c.gap_index+1)+' · '+gas+'：'+concentration(v)+(history.length>1?' · 时间历程 '+history.map(([s,a])=>s+' s '+concentration(a)).join(' → '):'')});
     }
   }else{
     for(const r of m.wall_reaction?.wafers||[]){
       r.plates.forEach((p,i)=>{
         const b=before.get(plateKey(r,i));
         const delta=Number.isFinite(p.oh_consumed)&&Number.isFinite(b?.oh_consumed)&&Math.abs(p.x_mm-b.x_mm)<.00001?b.oh_consumed-p.oh_consumed:null;
         const v=stage===0?p.pass_area:p.methyl_above_5_area;
         const text=stage===0?'OH 达标面积 '+fmt(v)+' · 平均 OH 消耗 '+fmt(p.oh_consumed):
           '含甲基位点平均 '+fmt(p.methyl_mean)+' · 残留 >5% 的面积 '+fmt(v)+
           (p.methyl_sides?' · '+p.methyl_sides.map((s,i)=>(i===0?'−X':'＋X')+' 面：平均 '+fmt(s.mean)+'，最高 '+fmt(s.peak)+'，超线面积 '+fmt(s.above_5_area)).join('；'):'')+
           ' · OH 覆盖率 '+fmt(Number.isFinite(p.oh_consumed)?1-p.oh_consumed:null)+' · OH 恢复 '+(Number.isFinite(delta)?(delta*100).toFixed(3)+' 个百分点':'未取得');
         const neighbors=(m.species_exposure?.channels||[]).filter(c=>(c.layer===r.layer||c.layer===(r.layer===ids[0]?'lower':'upper'))&&c.row===r.row&&[i-1,i].includes(c.gap_index));
         const residual=neighbors.map(c=>'通道 '+(c.gap_index+1)+' TMA '+concentration(c.species?.alme3?.mean)+' / H₂O '+concentration(c.species?.h2o?.mean)+' / CH₄ '+concentration(c.species?.ch4?.mean)).join('；');
         add(r.layer,r.row,{id:plateKey(r,i),x:p.x_mm,v,text:'晶圆 '+(i+1)+' · '+text+
           ' · MMA 覆盖率 '+fmt(p.mma_coverage)+(stage===2&&Number.isFinite(b?.mma_coverage)?'（进水前 '+fmt(b.mma_coverage)+'）':'')+(residual?' · 邻近片间 '+residual:'')});
       });
     }
   }
   if(!rows.size){status.textContent='本帧未取得对应的空间数据。';return;}
   const values=[...rows.values()].flatMap(r=>r.entries.map(e=>e.v)).filter(Number.isFinite);
   const limit=Math.max(0,...values);
   status.textContent=t+' s · '+(purge?'片间体积加权质量分数，色阶 0 至 '+concentration(limit):
     stage===0?'局部 OH 消耗达到 90% 的晶圆面积占比，色阶 90–100%':
     values.length?'逐片含甲基位点残留筛查。颜色越深，局部残留 >5% 的面积比例越大。5% 为查看用筛选线。此图不能单独判断累计沉积已趋稳。':'未取得含甲基位点数据。旧报告需用新版重新分析，且机理须与已核实的表面物种表一致。');
   for(const r of [...rows.values()].sort((a,b)=>ids.indexOf(b.layer)-ids.indexOf(a.layer)||a.row-b.row)){
     const line=document.createElement('div');line.className='ald-row';
     const label=document.createElement('span');label.textContent=layerName(ids,r.layer)+'第 '+r.row+' 排';
     const strip=document.createElement('div');strip.className='ald-strip';
     strip.style.gridTemplateColumns='repeat('+r.entries.length+',minmax(0,1fr))';
     for(const e of r.entries.sort((a,b)=>a.x-b.x)){
       const b=document.createElement('button');b.type='button';
       b.title=label.textContent+' · X = '+e.x.toFixed(1)+' mm · '+e.text;
       b.setAttribute('aria-label',b.title);
       b.setAttribute('aria-pressed',String(selected===e.id));
       if(Number.isFinite(e.v)){
         const strength=purge?(limit?e.v/limit:0):stage===0?Math.max(0,Math.min(1,(e.v-.9)*10)):Math.abs(e.v);
         const hue=purge||stage===2?24:stage===0?24+144*strength:168;
         b.style.background='hsl('+hue+' 58% '+(purge?91-52*strength:stage===0?40:91-52*strength)+'%)';
       }
       if(selected===e.id)detail.textContent=b.title;
       b.onclick=()=>{const old=grid.querySelector('[aria-pressed=true]');if(old)old.setAttribute('aria-pressed','false');selected=e.id;b.setAttribute('aria-pressed','true');detail.textContent=b.title;};
       strip.append(b);
     }
     line.append(label,strip);grid.append(line);
   }
 }
 function selectStage(){
   const previous=q('[data-gas]').value;
   q('[data-gas]').replaceChildren(...(stage===3?['h2o','ch4']:['alme3','ch4']).map(k=>new Option(k==='alme3'?'TMA':k.toUpperCase(),k)));
   if([...q('[data-gas]').options].some(o=>o.value===previous))q('[data-gas]').value=previous;
   q('[data-gas]').disabled=stage%2===0;
   q('[data-time]').replaceChildren(...snapshots[stage].map(m=>new Option(time(m)+' s',time(m))));
   q('[data-time]').selectedIndex=snapshots[stage].length-1;
   root.querySelectorAll('[data-ald-stage]').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.aldStage===stage)));
   selected=null;render();
 }
 root.querySelectorAll('[data-ald-stage]').forEach(b=>b.onclick=()=>{stage=+b.dataset.aldStage;q('[data-load-status]').textContent='';selectStage();});
 q('[data-time]').onchange=()=>{selected=null;render();};
 q('[data-gas]').onchange=render;
 q('[data-current]').onclick=()=>{
   if(kind(current)!==1||matchesCycle(current)||stage%2===0)return;
   if(snapshots.some((group,i)=>i!==stage&&group.some(p=>time(p)===time(current)))){q('[data-load-status]').textContent='当前帧已归入另一阶段。';return;}
   if(!Number.isFinite(time(current))||snapshots.some((group,i)=>group.some(p=>i<stage&&time(p)>=time(current)||i>stage&&time(p)<=time(current)))){q('[data-load-status]').textContent='当前帧与已载入阶段的流动时间顺序冲突。';return;}
   if(!snapshots[stage].some(m=>time(m)===time(current)))snapshots[stage].push(current);
   snapshots[stage].sort((a,b)=>time(a)-time(b));selectStage();
 };
 q('[data-files]').onchange=async event=>{
   const target=stage;const messages=[];
   for(const file of event.target.files){
     try{
       const raw=await file.text();
       const m=read(file.name.toLowerCase().endsWith('.json')?raw:
         new DOMParser().parseFromString(raw,'text/html').querySelector('#afReportData')?.textContent||'');
       const parent=p=>String(p||'').replaceAll('\\','/').toLowerCase().split('/').slice(0,-1).join('/');
       const sameProject=parent(current.provenance?.data_path)&&parent(current.provenance?.data_path)===parent(m.provenance?.data_path);
       if(!(sameProject||matchesCycle(current)&&matchesCycle(m))||!signature(current)||signature(m)!==signature(current)||m.provenance?.mesh_faces!==current.provenance?.mesh_faces||m.provenance?.mesh_tetra_cells!==current.provenance?.mesh_tetra_cells)throw Error('工程目录或几何不匹配');
       if(!Number.isFinite(time(m))||kind(m)!==(target===3?1:target)||(matchesCycle(m)&&phase(m)?.stage!==target))throw Error('入口组分或流动时间与所选阶段不符');
       if(snapshots.some((group,i)=>i!==target&&group.some(p=>time(p)===time(m))))throw Error('该时刻已归入另一阶段');
       if(snapshots.some((group,i)=>group.some(p=>i<target&&time(p)>=time(m)||i>target&&time(p)<=time(m))))throw Error('流动时间与阶段顺序冲突');
       if(!snapshots[target].some(p=>time(p)===time(m)))snapshots[target].push(m);
       messages.push(file.name+' 已读取');
     }catch(e){messages.push(file.name+'：'+e.message);}
   }
   snapshots[target].sort((a,b)=>time(a)-time(b));event.target.value='';selectStage();
   q('[data-load-status]').textContent=messages.join(' · ');
 };
 q('[data-current]').hidden=inferred!==1||matchesCycle(current);
 q('[data-export-inventory]').onclick=()=>{
   const records=snapshots[stage].filter(s=>s.species_exposure?.gas_inventory_kg);
   if(!records.length)return;
   const baseline=records[0];
   const mass=(s,key)=>s.species_exposure.gas_inventory_kg[key];
   const numeric=v=>Number.isFinite(v)?v:'';
   const remaining=(s,key)=>{
     const first=mass(baseline,key), value=mass(s,key);
     return Number.isFinite(value)&&Number.isFinite(first)&&first>0?100*value/first:'';
   };
   const rows=[['stage','flow_time_s','reference_time_s','tma_ug','h2o_ug','tma_pct_of_reference','h2o_pct_of_reference']];
   const stageName=['TMA','N2_PURGE_1','H2O','N2_PURGE_2'][stage];
   for(const s of records)rows.push([stageName,time(s),time(baseline),numeric(mass(s,'alme3')*1e9),numeric(mass(s,'h2o')*1e9),remaining(s,'alme3'),remaining(s,'h2o')]);
   const url=URL.createObjectURL(new Blob([rows.map(r=>r.join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
   const link=document.createElement('a');link.href=url;link.download='AF_'+stageName+'_gas_inventory.csv';link.click();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 let history=null;
 const refresh=[];
 const picker=document.createElement('input');picker.type='file';picker.accept='.csv';
 const historyStatus=document.createElement('small');historyStatus.setAttribute('role','status');
 const inputsArea=document.createElement('details');
 const caption=document.createElement('summary');caption.textContent='导入入口历史';
 const guide=document.createElement('p');guide.textContent='CSV 表头：start_s,end_s,mass_flow_kg_s,tma_fraction,h2o_fraction。每行是一段恒定入口条件，使用各入口合计流量和流量加权质量分数，从 0 s 连续覆盖至保存时刻。';
 inputsArea.append(caption,guide,picker,historyStatus);
 root.prepend(inputsArea);
 function integrateHistory(text,t){
   const lines=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/);
   if(lines.shift()!=='start_s,end_s,mass_flow_kg_s,tma_fraction,h2o_fraction'||!Number.isFinite(t)||t<=0)throw Error('表头或保存时刻无效');
   const totals={alme3:0,h2o:0};
   let end=0;
   for(const line of lines){
     const cells=line.split(',').map(v=>v.trim());
     if(cells.length!==5||cells.some(v=>v===''))throw Error('某行缺少入口数据');
     const [start,stop,flow,tma,water]=cells.map(Number);
     if(![start,stop,flow,tma,water].every(Number.isFinite)||Math.abs(start-end)>1e-8||stop<=start||flow<0||tma<0||water<0||tma+water>1+1e-9)throw Error('入口时段不连续或流量、组分无效');
     const duration=Math.max(0,Math.min(stop,t)-start);
     totals.alme3+=duration*flow*tma*1e6;totals.h2o+=duration*flow*water*1e6;
     end=stop;
   }
   if(end<t-1e-8)throw Error('入口历史未覆盖到 '+t+' s');
   return totals;
 }
 picker.onchange=async()=>{
   const file=picker.files[0];if(!file)return;
   try{history=integrateHistory(await file.text(),time(current));historyStatus.textContent='已导入 '+file.name;}
   catch(e){history=null;historyStatus.textContent=e.message;}
   refresh.forEach(update=>update());picker.value='';
 };
 const frameTime=Number.isFinite(time(current))?time(current).toFixed(1)+' s':'流动时间未取得';
 const phaseLabel=recorded?.caption??(frameTime+(inferred===1?'｜纯 N₂，吹扫轮次待确认':''));
 const style=document.createElement('style');
 style.textContent='.af-use{box-sizing:border-box;margin:12px 0 18px;padding:14px;border:1px solid #dce6e9;border-radius:13px;background:#fff;font:inherit}.af-use h3{margin:0}.af-use .use-frame{color:var(--muted,#52717e);font-size:.87em}.af-use p{margin:6px 0}.af-use details{margin-top:10px}.af-use label{display:inline-flex;flex-wrap:wrap;gap:6px;margin:4px 12px 4px 0}.af-use input{width:8em;max-width:100%}:is(#wall-reaction,#wall-reaction-water,#ald-stages) .af-use table{width:100%;min-width:0;table-layout:fixed;border-collapse:collapse}:is(#wall-reaction,#wall-reaction-water,#ald-stages) .af-use :is(th,td){white-space:normal;overflow-wrap:anywhere;text-align:left;padding:7px 10px;border-bottom:1px solid #e3eaed}:is(#wall-reaction,#wall-reaction-water,#ald-stages) .af-use :is(th,td):nth-child(2){text-align:right;font-variant-numeric:tabular-nums}.af-use col:first-child{width:34%}.af-use col:nth-child(2){width:20%}';
 style.textContent+='.af-material-chart{max-width:760px;margin:14px 0 16px}.material-bars{display:grid;grid-template-columns:minmax(0,11em) minmax(24px,1fr) auto;align-items:center;gap:9px 12px}.material-bars strong{font-variant-numeric:tabular-nums;text-align:right}.material-track{height:10px;border-radius:4px;background:#eef3f4;overflow:hidden}.material-track i{height:100%;display:block;background:#227d74;border-radius:4px}.material-track[data-kind=need] i{background:#a0b4ba}.material-track[data-kind=gas] i{background:#c57839}.material-share{display:flex;justify-content:space-between;gap:10px;margin-top:13px!important}.material-track.share{height:7px}.material-track.share i{background:#c57839}@media(max-width:600px){.material-bars{grid-template-columns:minmax(0,8em) minmax(12px,1fr) auto;gap:8px 6px;font-size:.88em}}';
 root.append(style);
 const el=(tag,text)=>{const e=document.createElement(tag);if(text)e.textContent=text;return e;};
 for(const [selector,target] of [['#wall-reaction > .wafer-map',1],['#wall-reaction-water > .wafer-map',3]]){
   const anchor=document.querySelector(selector);
   const water=target===3,key=water?'h2o':'alme3';
   const host=document.querySelector(water?'#wall-reaction-water':'#wall-reaction')||root;
   const gas=current.species_exposure||{};
   const kg=gas.gas_inventory_kg?.[key], stats=gas.gas_inventory_stats?.[key];
   const flow=current.inlet_flow_kg_s;
   const inlet=gas.inlet_mass_fractions?.[key];
   const mass=Number.isFinite(kg)?kg*1e6:null;
   const card=el('article');card.className='af-use';card.id=water?'h2o-material':'tma-material';
   const stamp=el('p',phaseLabel);stamp.className='use-frame';card.append(el('h3',(water?'H₂O':'TMA')+' 用料账'),stamp);
   const table=el('table'), cols=el('colgroup');cols.append(el('col'),el('col'),el('col'));table.append(cols);
   const notes=el('details');notes.append(el('summary','计算依据与补录'),el('p','气相残余按 ∫ρY dV。投料按恒定总流量 × 组分质量分数 × 时长。补录用于本次打开的报告。'));
   // Read-only verification of the 0 s companion solution and reactive-area monitor for this exact saved frame.
   const verifiedInitial={"data_path":"E:\\THING\\W.F\\YY\\FZMN\\MODEL\\11.20\\11.27\\5.0_files\\dp0\\FFF\\Fluent\\FFF-1-00017.dat.h5","case_path":"E:\\THING\\W.F\\YY\\FZMN\\MODEL\\11.20\\11.27\\5.0_files\\dp0\\FFF\\Fluent\\FFF-Setup-Output.cas.h5","data_bytes":2960732017,"data_modified_ns":1795766595658899600,"geometry_candidate_signature_not_mesh_hash":"6dbcacba1b7f0857e088aafbc12016b06109018991fb19902bc08761d876cfb1"};
   const sameInitial=!water&&Object.entries(verifiedInitial).every(([k,v])=>current.provenance?.[k]===v);
   const initialDemand=sameInitial?6.221436560950922*8.229100068035677e-9*72.08668*1e6/2:null;
   const fields=[['总流量（kg/s）',1.25e-5],['入口质量分数',0.090909],['供料时长（s）',water?1:2],['已验证理论需求（mg）',water?0.4612622645751668:1.846]].map(([name,v],i)=>{
     const f=el('input');f.type='number';f.min='0';f.step='any';if(i===1)f.max='1';
     if(i===3&&initialDemand!==null)f.value=String(initialDemand);
     else if(recorded)f.value=String(v);
     else if(i===0&&Number.isFinite(flow)&&flow>0)f.value=String(Number(flow.toPrecision(6)));
     else if(i===1&&Number.isFinite(inlet))f.value=String(Number(inlet.toPrecision(6)));
     const l=el('label',name);l.append(f);notes.append(l);return f;
   });
   const fmt=(v,u)=>Number.isFinite(v)?v.toPrecision(4)+' '+u:'—';
   const pct=v=>Number.isFinite(v)?(100*v).toPrecision(3)+'%':'—';
   const chart=el('div');chart.className='af-material-chart';
   const details=el('details');details.append(el('summary','详细数值与来源'),table,notes);
   card.append(chart,details);
   function drawMaterial(total,d,extra,estimated){
     chart.replaceChildren();
     const items=[['累计投料'+(estimated?'（估算）':''),total,'feed'],['晶圆理论需求',d,'need'],['全腔气相余量',mass,'gas']];
     const scale=Math.max(0,...items.map(([,v])=>Number.isFinite(v)?v:0));
     if(!items.some(([,v])=>Number.isFinite(v)))return;
     const plot=el('div');plot.className='material-bars';
     for(const [label,v,kind] of items){
       const track=el('div');track.className='material-track';track.dataset.kind=kind;
       if(Number.isFinite(v)&&scale>0){const fill=el('i');fill.style.width=(100*v/scale)+'%';track.append(fill);}
       plot.append(el('span',label),track,el('strong',Number.isFinite(v)?fmt(v,'mg'):'—'));
     }
     chart.append(plot);
     if(Number.isFinite(extra))chart.append(el('p',(extra>=0?'理论投料余量 ':'低于理论需求 ')+fmt(Math.abs(extra),'mg')+(estimated?' · 按估算投料':'') ));
     if(total>0&&mass!==null){
       const ratio=mass/total;
       const line=el('p');line.className='material-share';line.append(el('span','气相余量占'+(estimated?'估算':'')+'投料'),el('strong',pct(ratio)));
       chart.append(line);
       if(ratio>=0&&ratio<=1){const track=el('div');track.className='material-track share';const fill=el('i');fill.style.width=(ratio*100)+'%';track.append(fill);chart.append(track);}
     }
   }
   function update(){
     const [a,b,c,d]=fields.map(f=>f.value!==''&&f.validity.valid?Number(f.value):null);
     const feed=history?history[key]:[a,b,c].every(Number.isFinite)?a*b*c*1e6:null;
     
     const estimate=feed===null&&Number.isFinite(a)&&a>0&&Number.isFinite(b)&&b>0&&Number.isFinite(time(current))&&time(current)>0
       ?a*b*time(current)*1e6:null;
     const total=feed??estimate;
     const estimated=feed===null&&estimate!==null;
     const extra=total!==null&&d!==null?total-d:null;
     const missing=[a===null?'入口流量':'',b===null?'入口组分':'',c===null?'供料时段':''].filter(Boolean).join('、');
     const rows=[
       ['指标','数值','时刻／来源'],
       ['当前入口质量分数',pct(gas.inlet_mass_fractions?.[key]),frameTime+' · 入口统计'],
       ['当前入口净流量',fmt(flow,'kg/s'),Number.isFinite(flow)?frameTime+' · 入口面通量':'缺入口面通量'],
       ['入口累计投料',total===0?'0 mg':fmt(total,'mg'),estimated?'估算 · 假设 0–'+frameTime+' 恒定供料':history?(feed===0?'入口历史 · 尚未供入':'入口历史积分'):feed===null?'缺'+missing:recorded?'工艺示例／补录':'恒定供料／补录'],
       ['晶圆理论需求',fmt(d,'mg'),d===null?(water?'缺切水前表面积分':'缺初始位点与反应计量'):recorded?(water?'15.0 s 表面积分／补录':'理想最低剂量／补录'):sameInitial&&d===initialDemand?'0 s OH 约 100% · 2 OH / TMA 理想口径':'本次补录'],
       ['理论投料余量',fmt(extra,'mg'),extra===null?'需投料和理论需求':estimated?'按估算投料 − 理论需求':'投料 − 理论需求'],
       ['全腔气相残余',fmt(mass===null?null:mass*1000,'µg'),mass===null?'缺密度、组分场或有效体积':frameTime+' · 流场积分'],
       ['残余占总投料',pct(mass!==null&&total>0?mass/total:null),total>0?(estimated?'按估算投料':frameTime):total===0?'不适用 · 累计投料为零':'缺累计投料'],
       ['残余占理论余量',pct(mass!==null&&extra>0?mass/extra:null),extra===null?'缺理论余量':extra<=0?'无正的理论投料余量':(estimated?'估算 · ':'')+(mass!==null&&mass>extra?'当前库存高于理论投料差额':frameTime)],
       ['体积平均质量分数',pct(stats?.volume_mean),stats?frameTime:'缺全腔体积统计'],
       ['最大质量分数',pct(stats?.max),stats?frameTime:'缺全腔峰值统计'],
       ['峰值坐标（X, Y, Z）',stats?.peak_xyz_m?.map(x=>(x*1000).toFixed(2)).join(', ')||'—',stats?'mm · 单元中心':'缺峰值坐标']
     ];
     drawMaterial(total,d,extra,estimated);
     const body=el('tbody');rows.forEach((r,i)=>{const tr=el('tr');for(const v of r){const cell=el(i?'td':'th',v);if(!i)cell.scope='col';tr.append(cell);}body.append(tr);});
     table.tBodies[0]?.remove();table.append(body);
   }
   for(const f of fields)f.oninput=update;
   refresh.push(update);update();
   if(sameInitial)notes.append(el('p','初始证据：同算例 FFF-1-00000.dat.h5，0 s，OH 约 100%；反应面积监测 6.2214 m²；FFF.set 位点密度 8.2291×10⁻⁹ kmol/m²。按 2 OH / TMA 的理想口径计算。'));
   if(recorded&&!water)notes.append(el('p','15.0 s 第一次吹扫末端，TMA 残余 14.72 µg（工艺对照记录）。'));
   if(anchor)anchor.insertAdjacentElement('beforebegin',card);else host.append(card);
   if(anchor&&target===3&&recorded){
     const summary=document.createElement('article');
     summary.className='wall-chart';
     const heading=document.createElement('h3'); heading.textContent=recorded.caption;
     summary.append(heading);
     const add=(label,text)=>{
       const line=document.createElement('p');
       const strong=document.createElement('strong');strong.textContent=label+'　';
       line.append(strong,document.createTextNode(text));summary.append(line);
     };
     if(recorded.stage===3){
       const plates=(current.wall_reaction?.wafers||[]).flatMap(row=>row.plates);
       const measured=plates.filter(p=>Number.isFinite(p.methyl_above_5_area));
       const pass=measured.filter(p=>p.methyl_above_5_area===0).length;
       if(measured.length){
         const exceeded=measured.length-pass;
         const area=exceeded===0?'超限面积占比 0%，按 5% 筛查线未检出超限区域。':
           '单片超限面积占比最高 '+(Math.max(...measured.map(p=>p.methyl_above_5_area))*100).toFixed(3)+'%。';
         add('晶圆表面反应','有数据 '+measured.length+' 片，存在超限区域 '+exceeded+' 片。'+area);
       }
       else add('晶圆表面反应','此帧未取得逐片双面甲基残留统计。');
       if(Math.abs(time(current)-30)<1e-8){
         const before=7.534385279306351e-8;
         const after=current.species_exposure?.gas_inventory_kg?.h2o;
         if(Number.isFinite(after))add('全腔气相余水','25.0 → 30.0 s，总质量 '+(before*1e9).toFixed(2)+' → '+(after*1e9).toFixed(3)+' µg（下降 '+((1-after/before)*100).toFixed(1)+'%）。按用户提供的全腔统计，体积平均质量分数 0.196% → 0.0144%，全腔最大质量分数 1.792% → 0.819%。局部峰值下降较慢。');
         add('本轮结论','30.0 s 为本次单周期保存终点。晶圆表面水反应按当前筛选口径已基本完成，气相余水仍可检出。余水对下一轮 TMA 沉积的影响待验证。');
         const water=anchor.parentElement;
         const title=water.querySelector(':scope > h3');
         const note=water.querySelector(':scope > p');
         const mapTitle=anchor.querySelector('h3');
         if(title)title.textContent='第二次 N₂ 吹扫末端｜晶圆表面含甲基位点残留（30.0 s）';
         if(note)note.textContent='供水于 16.0 s 结束，本帧为切回纯氮并吹扫 14.0 s 后的保存结果。'+
           (measured.length?'图中显示 '+measured.length+' 片有数据晶圆双面的含甲基位点残留，用于评估水半反应的完成程度。':'本帧缺少逐片双面甲基残留统计。');
         if(mapTitle)mapTitle.textContent='30.0 s · 逐片晶圆双面残留分布';
       }
     }
     anchor.insertAdjacentElement('beforebegin',summary);
   }
 }
 selectStage();
})();
