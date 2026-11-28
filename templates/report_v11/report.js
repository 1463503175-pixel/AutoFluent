(()=>{'use strict';
const D=JSON.parse(document.getElementById('afReportData').textContent);
const $=q=>document.querySelector(q), $$=q=>Array.from(document.querySelectorAll(q));
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const valid=v=>typeof v==='number'&&Number.isFinite(v);const pct=(v,n=2)=>valid(v)?v.toFixed(n)+'%':'未取得';const nm=(v,n=1)=>valid(v)?v.toFixed(n):'未取得';const e3=v=>valid(v)?v.toExponential(3):'未取得';const num=v=>valid(v)?v.toLocaleString('zh-CN'):'未取得';
const L={upper:'上层',lower:'下层'};
const F=D.flow, CH=D.channels,PM=D.pressureMap;
let activeBaffle=D.baffles?.upper?.available?'upper':'lower';
let HO=D.baffles[activeBaffle].holes,BD=D.baffles[activeBaffle].rows;
const getLayer=l=>CH.filter(x=>x.layer===l);
const summary=l=>{const a=getLayer(l),avg=a.reduce((s,x)=>s+x.net,0)/(a.length||1);return {rows:a,avg,min:a.reduce((p,x)=>!p||x.net<p.net?x:p,null),max:a.reduce((p,x)=>!p||x.net>p.net?x:p,null),weak90:a.filter(x=>x.ratio<90),weak95:a.filter(x=>x.ratio<95),n:a.length}};
const S={upper:summary('upper'),lower:summary('lower')};
const minLayer=(S.lower.min.ratio<S.upper.min.ratio)?'lower':'upper';
const worst=S[minLayer].min;let holeAvg=HO.reduce((t,x)=>t+x.net,0)/(HO.length||1),hMin=HO.reduce((p,x)=>!p||x.net<p.net?x:p,null),hMax=HO.reduce((p,x)=>!p||x.net>p.net?x:p,null);
const fullMass=F.upper?.mass_cut || F.lower?.mass_cut || NaN;
function getRatio(x){return Number.isFinite(x.ratio)?x.ratio:(holeAvg?x.net/holeAvg*100:NaN)}
function hexToRgb(s){const x=s.replace('#','');return [0,2,4].map(i=>parseInt(x.slice(i,i+2),16))}
function mix(a,b,t){const A=hexToRgb(a),B=hexToRgb(b);t=Math.max(0,Math.min(1,t));return '#'+A.map((v,i)=>Math.round(v+(B[i]-v)*t).toString(16).padStart(2,'0')).join('')}
let channelMode='relative';
function chColor(r,net=null){let a=CH.map(c=>channelMode==='absolute'?c.net:c.ratio),mean=channelMode==='absolute'?CH.reduce((s,c)=>s+c.net,0)/CH.length:100,v=channelMode==='absolute'&&valid(net)?net:r,delta=Math.max(...a.map(x=>Math.abs(x-mean)),1e-30);return v<=mean?mix('#d8e3df','#24629d',(mean-v)/delta):mix('#d8e3df','#c85a36',(v-mean)/delta)}
function renderChannelLegend(){const absolute=channelMode==='absolute';const min=Math.min(...CH.map(c=>c.net)),max=Math.max(...CH.map(c=>c.net));const text=absolute?'同一绝对流量色标：'+e3(min)+'～'+e3(max)+' kg/s':'两层使用同一相对色标，100%为本层通道平均流量';$('#channelScaleNote').textContent=text;$$('.channel-legend').forEach(el=>el.innerHTML='<span><i class="swatch" style="background:#24629d"></i>较低流量</span><span><i class="swatch" style="background:#d8e3df"></i>平均附近</span><span><i class="swatch" style="background:#c85a36"></i>较高流量</span><span><i class="outline-swatch"></i>低于层均值90%</span><span><i class="min-swatch"></i>最弱通道</span>');}

function threeFacts(where,items){
 const holder=$(where);if(!holder)return;
 const labels=['结果','分析','检查重点'];
 holder.innerHTML=items.map((item,i)=>`<article class="fact-item"><span class="fact-number">0${i+1} · ${labels[i]}</span><div>${esc(item)}</div></article>`).join('');
}
const atlasX=(x,min,max)=>116+(x-min)/(max-min||1)*784;
function atlasFindChannel(layer,ch){currentLayer=layer;selectedChannel=null;setChannelTabs();selectChannel(layer,ch,false);jump('channels');}
function atlasFindHole(id,board=activeBaffle){setBaffle(board);selectHole(id);jump('baffles')}
function renderAtlas(){
 $('#atlasConclusion').textContent='先看下层：'+D.analysis.channel[0];
 const points=CH.concat(CH.flatMap(c=>c.x_range_m&&c.z_range_m?[{x:c.x_range_m[0]*1000,z:c.z_range_m[0]*1000},{x:c.x_range_m[1]*1000,z:c.z_range_m[1]*1000}]:[]),Object.values(D.baffles).flatMap(b=>b.holes||[])),x0=Math.min(...points.map(p=>p.x))-12,x1=Math.max(...points.map(p=>p.x))+12,z0=Math.min(...points.map(p=>p.z))-12,z1=Math.max(...points.map(p=>p.z))+12;
 const scale=Math.min(700/(x1-x0),230/(z1-z0)),mx=x=>65+(x-x0)*scale,mz=z=>28+(z-z0)*scale,w=100+(x1-x0)*scale,h=75+(z1-z0)*scale;
 const specs=[['upper','board','上部进气挡板'],['upper','layer','上层晶圆'],['lower','layer','下层晶圆'],['lower','board','下部出气挡板']];
 const height=(key,kind)=>kind==='board'?D.baffles[key].cut_y_m:D.layerGeometry?.[key]?.y_cut_m;
 $('#heightRoute').innerHTML='<p class="tiny">沿气流从上往下<br>顺序示意，间距不按比例</p>'+specs.map(([key,kind,name],i)=>'<div class="height-stop '+(i===2?'layer-divider':'')+'"><b>'+name+'</b><small>Y = '+(valid(height(key,kind))?nm(height(key,kind)*1000,2)+' mm':'未取得')+'</small>'+(i<3?'<span class="down-arrow">↓</span>':'')+'</div>').join('');
 $('#atlasSvg').innerHTML=specs.map(([key,kind,name],i)=>{let shapes='',note='';if(kind==='board'){const b=D.baffles[key];if(b.available&&b.holes.length){const low=b.holes.reduce((a,c)=>c.net<a.net?c:a),high=b.holes.reduce((a,c)=>c.net>a.net?c:a);shapes=b.holes.map(p=>'<circle cx="'+mx(p.x)+'" cy="'+mz(p.z)+'" r="2" fill="#c6d2d5"/>').join('');for(const [p,title,col] of [[low,'最低','#245b9a'],[high,'最高','#c95731']])shapes+='<g class="atlas-pin" data-kind="hole" data-board="'+key+'" data-id="'+p.id+'" tabindex="0" role="button" aria-label="'+name+title+'孔'+p.id+'"><circle cx="'+mx(p.x)+'" cy="'+mz(p.z)+'" r="7" fill="'+col+'" stroke="white" stroke-width="2"/><text x="'+mx(p.x)+'" y="'+(mz(p.z)+(p===low?-12:23))+'" text-anchor="middle" font-size="12" fill="'+col+'">'+title+' #'+p.id+' '+pct(p.ratio)+'</text><title>X='+nm(p.x,2)+' mm，Z='+nm(p.z,2)+' mm</title></g>';note=b.holes.length+'个真实孔位；蓝点最低、橙点最高（相对本板均值）';}else note='逐孔数据未取得';}else{const geom=D.layerGeometry?.[key];for(const row of geom?.rows||[]){row.face_positions_lo_m.forEach((v,j)=>shapes+='<rect x="'+mx(v*1000)+'" y="'+mz(row.z_min_m*1000)+'" width="'+Math.max(.8,(row.face_positions_hi_m[j]-v)*1000*scale)+'" height="'+(row.z_max_m-row.z_min_m)*1000*scale+'" fill="#cbd5d8"/>');}for(const c of getLayer(key)){const xr=c.x_range_m,zr=c.z_range_m,weak=c.ratio<90,isMin=c===S[key].min;shapes+='<rect class="atlas-pin" data-kind="channel" data-layer="'+key+'" data-ch="'+esc(c.ch)+'" x="'+mx(xr?xr[0]*1000:c.x)+'" y="'+mz(zr?zr[0]*1000:c.z)+'" width="'+(xr?Math.max(2,(xr[1]-xr[0])*1000*scale):3)+'" height="'+(zr?Math.max(3,(zr[1]-zr[0])*1000*scale):5)+'" fill="'+(weak?'#b72e36':'#e8f1f0')+'" stroke="'+(isMin?'#213d48':weak?'#b72e36':'none')+'" stroke-width="'+(isMin?2:1)+'" tabindex="0" role="button" aria-label="'+L[key]+' '+esc(c.ch)+' '+pct(c.ratio)+'"><title>'+esc(c.ch)+' '+pct(c.ratio)+'，X='+nm(c.x,2)+'，Z='+nm(c.z,2)+' mm</title></rect>';}note='灰线：晶圆；浅色：片间；红色：低于本层均值90%的通道。'+L[key]+'共'+S[key].weak90.length+'条低流量通道';}
 const axes='<text x="65" y="'+(h-10)+'" font-size="12">X '+nm(x0,0)+' → '+nm(x1,0)+' mm</text><text x="'+(w-200)+'" y="'+(h-10)+'" font-size="12">Z '+nm(z0,0)+' ↓ '+nm(z1,0)+' mm</text>';
 return '<article class="locator-plane '+(i===2?'layer-divider':'')+'" data-plane="'+kind+'-'+key+'"><h4>'+name+'<small>俯视：沿−Y向下看，X→，Z↓</small></h4><div class="plan-scroll"><svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+name+'真实XZ位置">'+shapes+axes+'</svg></div><p class="tiny">'+note+'</p></article>';}).join('');
 $('#atlasInsight').innerHTML=[['最弱通道',L[minLayer]+' '+worst.ch+'：层均值的'+pct(worst.ratio)],['优先检查区段','X='+nm(worst.x,2)+' mm附近，结合该通道上下游压差判断'],['怎样找位置','左栏找高度，右图找X/Z位置；点红色通道或孔极值进入明细。']].map(([a,b])=>'<article><b>'+a+'</b><p>'+esc(b)+'</p></article>').join('');
 $('#diagnosisText').innerHTML='<p class="chart-takeaway">'+esc(D.analysis.channel[0])+'</p><p>低流量的位置已定位。挡板极值与通道同在某个X区段时，可作为后续核查压差和流路的线索；原因判断还需要对应位置的上下游数据。</p>';
 $$('#atlasSvg .atlas-pin').forEach(el=>{el.onclick=()=>el.dataset.kind==='hole'?atlasFindHole(Number(el.dataset.id),el.dataset.board):atlasFindChannel(el.dataset.layer,el.dataset.ch);el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click?el.click():el.dispatchEvent(new MouseEvent('click',{bubbles:true}))}}});
}
function jump(id){const e=document.getElementById(id);if(e)window.scrollTo({top:scrollY+e.getBoundingClientRect().top-headerOffset(),left:0,behavior:'instant'});syncNavigation()}
function setTabs(container,attr,val){document.querySelectorAll(`${container} [${attr}]`).forEach(b=>b.classList.toggle('active',b.dataset[attr.replace('data-','')]===String(val)))}
const CHAPTERS=[['overview','研究对象与主要结论'],['flow','气体流路与分配'],['species','混合气体组分与暴露证据'],['wall-reaction','壁面反应与表面覆盖率'],['ald-stages','ALD 阶段对照'],['channels','晶圆通道均匀性'],['baffles','上下挡板怎样分配气体'],['pressure','气体向下流动，压力怎样变化'],['locator','低流量通道集中在哪里'],['actions','优化方向与验证计划'],['evidence','计算方法与数据来源']].filter(([id])=>document.getElementById(id));
function setup(){
 $('#modelName').textContent=D.name.replace(/\.cas\.h5$/i,'')+' · 流动与分配分析';$('#breadcrumbModel').textContent=D.name;
 document.title=D.name+' · AutoFluent 仿真分析报告';
 $('#dataScope').textContent=D.reportMetadata.synthetic_demo?'合成测试数据':'保存解原生统计';
 $('#researchBrief').textContent=D.analysis.plain_summary+' '+D.analysis.research;
 const navLabels={"overview":"算例与结论","flow":"全腔流路","species":"气相组分","wall-reaction":"晶圆反应与用料","ald-stages":"ALD 阶段","channels":"片间流量","baffles":"挡板孔径与流量","pressure":"压力与压降","locator":"低流量通道","actions":"优化与验证","evidence":"方法与数据"};
 $('#nav').innerHTML=CHAPTERS.map(([id,title],i)=>'<a href="#'+id+'"><span>'+String(i+1).padStart(2,'0')+'</span>'+(navLabels[id]||title)+'</a>').join('');
 CHAPTERS.forEach(([id,title],i)=>{const e=$('#'+id+' > .chapter-heading');if(!e?.querySelector('.serial')||!e.querySelector('h2'))return;e.querySelector('.serial').textContent=String(i+1).padStart(2,'0');e.querySelector('h2').textContent=title;});
 $('#holeCount').textContent=HO.length+'个孔，'+BD.length+'排；蓝色较低，橙色较高。';
 $('#holeRows').innerHTML='<button data-row="0" class="active">全部孔排</button>'+BD.map(r=>'<button data-row="'+r.row+'">第'+r.row+'排</button>').join('');
 $('#pressureCount').textContent=PM.bins.length+'个X/Z分箱，颜色表示单元中心静压的算术均值。';
 $('#evidenceCount').textContent=CH.length+'条通道、'+HO.length+'个孔及其对应统计。';
 $('#printBtn').onclick=()=>window.print();$('#toEvidence').onclick=()=>jump('evidence');
 $$('#nav a').forEach(a=>a.onclick=e=>{e.preventDefault();history.pushState(null,'',a.getAttribute('href'));jump(a.hash.slice(1));});
 let queued=false;const schedule=()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;syncNavigation()})}};
 addEventListener('scroll',schedule,{passive:true});addEventListener('resize',schedule);addEventListener('popstate',()=>{jump(location.hash.slice(1)||'overview')});schedule();
 const opened=[];addEventListener('beforeprint',()=>{opened.length=0;$$('#methodDetails').forEach(d=>{if(!d.open){opened.push(d);d.open=true}})});addEventListener('afterprint',()=>opened.forEach(d=>d.open=false));
}
function headerOffset(){return innerWidth<=1024?$('.side').getBoundingClientRect().height+16:20}
function syncNavigation(){let selected=CHAPTERS[0][0];for(const [id] of CHAPTERS){if($('#'+id).getBoundingClientRect().top<=headerOffset()+70)selected=id;}if(scrollY+innerHeight>=document.documentElement.scrollHeight-2)selected=CHAPTERS[CHAPTERS.length-1][0];$$('#nav a').forEach(a=>{const active=a.hash==='#'+selected;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current')});}

function renderOverview(){
 $('#headlineText').textContent=D.analysis.overview;
 $('#headlineKpis').innerHTML=[['上层片间占入口',pct(F.upper.wafer)],['下层片间占入口',pct(F.lower.wafer)],['最弱通道 / 层均值',pct(worst.ratio)],['入口至出口总压降估计',valid(D.pressureDrop)?nm(D.pressureDrop,5)+' Pa':'未取得']].map(([a,b])=>'<div class="kpi"><label>'+esc(a)+'</label><strong>'+esc(b)+'</strong></div>').join('');
 const focus=D.analysis.summaries[D.analysis.focus],min=focus.minimum;
 $('#conclusions').innerHTML=[['整体分配','上层CV '+pct(F.upper.cv)+'，下层CV '+pct(F.lower.cv)+'。CV越小，各通道的流量越接近。'],['局部低值',L[D.analysis.focus]+' '+min.ch+'，X='+nm(min.x,2)+' mm，流量较层均值低'+pct(100-min.ratio)+'。'],['优先检查',focus.weak_count?'低于层均值90%的通道有'+focus.weak_count+'条，先对照该区段的压差与相邻流路。':'当前各通道均达到层均值90%；继续比较极值及整体流量分配。']].map(([a,b])=>'<article class="conclusion"><h3>'+esc(a)+'</h3><p>'+esc(b)+'</p></article>').join('');
}
function metric(label,value,hint,cls=''){return `<div class="metric"><label>${esc(label)}</label><strong class="${cls}">${esc(value)}</strong><small>${esc(hint)}</small></div>`}
function renderFlow(){
 const regions=[['wafer','晶圆片间','#158779'],['central','中央','#596e7b'],['left','左侧（Z−）','#78a8c5'],['right','右侧（Z+）','#766d99'],['front','前侧（X−）','#c5995c'],['back','后侧（X+）','#b86651']];
 $('#flowRoute').innerHTML=['入口','进气挡板','上层截面','下层截面','出气挡板','出口'].map((s,i)=>'<li><span>'+String(i+1).padStart(2,'0')+'</span>'+s+'</li>').join('');
 $('#flowBars').innerHTML=['upper','lower'].map(l=>{const f=F[l],six=f.six,items=six?regions:[regions[0],regions[1],['low','低Z外围','#78a8c5'],['high','高Z外围','#766d99']],values=items.map(r=>six?six[r[0]]:f[r[0]]),total=values.reduce((a,b)=>a+b,0);return '<div class="flowrow"><div class="label"><span>'+L[l]+'</span><span>片间 '+pct(f.wafer)+'</span></div><div class="flowstack">'+items.map((r,i)=>'<span class="flowpiece" style="background:'+r[2]+';width:'+values[i]/Math.max(100,total)*100+'%" title="'+r[1]+' '+pct(values[i])+'">'+(values[i]>6?pct(values[i],1):'')+'</span>').join('')+'</div><p class="tiny">占同一入口净流量；截面合计 '+pct(total)+'。</p></div>'}).join('');
 $('#flowLegend').innerHTML=regions.map(r=>'<span class="legend-item"><i class="swatch" style="background:'+r[2]+'"></i>'+r[1]+'</span>').join('');
 $('#flowInsight').textContent='每层约'+nm((F.upper.wafer+F.lower.wafer)/2,1)+'%经过晶圆片间，其余从中央及四周绕过。上下层是沿流路先后经过的两个截面。';
 $('#bypassChart').innerHTML='<table class="region-table"><thead><tr><th>气体经过哪里</th><th>上层</th><th>下层</th></tr></thead><tbody>'+regions.map(r=>'<tr data-region="'+r[0]+'"><th><i class="swatch" style="background:'+r[2]+'"></i> '+r[1]+'</th>'+['upper','lower'].map(l=>'<td>'+pct(F[l].six?F[l].six[r[0]]:null)+'</td>').join('')+'</tr>').join('')+'</tbody></table><p class="tiny">方向按报告坐标定义：前/后对应X−/X+，左/右对应Z−/Z+。前后端与左右侧相交的角部计入前后，六项互不重复。</p>'+(F.upper.six&&F.lower.six?'':'<p class="helper">此旧统计未保存六分区通量，请用本版重新分析Case/Data。</p>');
 threeFacts('#flowExplainer',F.upper.six&&F.lower.six?['上层：片间'+pct(F.upper.six.wafer)+'，中央'+pct(F.upper.six.central)+'；四周分别见六区表。','下层：左侧'+pct(F.lower.six.left)+'，右侧'+pct(F.lower.six.right)+'；前侧'+pct(F.lower.six.front)+'，后侧'+pct(F.lower.six.back)+'。','两层片间占比相差'+nm(Math.abs(F.upper.wafer-F.lower.wafer),2)+'个百分点。继续查看逐通道分配，可找到整体平均值掩盖的局部低值。']:D.analysis.flow);
}
let currentLayer=minLayer,selectedChannel=null;
const chKey=(l,c)=>`${l}:${c}`;
function setChannelTabs(){$$('#channelLayerSwitch button').forEach(b=>b.classList.toggle('active',b.dataset.layer===currentLayer));renderChannels()}
function closeChannel(){selectedChannel=null;renderChannelDetail();$$('.heatcell,.zoom-cell').forEach(x=>x.classList.remove('active'));$$('.weak-table tr').forEach(x=>x.classList.remove('active'))}
function renderChannelDetail(){let el=$('#channelDetail');if(!selectedChannel){el.className='selection';el.textContent='点击任意通道可查看编号、位置和真实净流量；再次点击同一通道即可收起。';return}
 const r=CH.find(z=>z.layer===selectedChannel.layer&&z.ch===selectedChannel.ch);if(!r){closeChannel();return}
 el.className='selection selected';el.innerHTML=`<b>${esc(L[r.layer])} · 第 ${r.row} 排 · ${esc(r.ch)}</b><button type="button" id="closeChannel">关闭 ×</button><br> X = ${nm(r.x,2)} mm，Z = ${nm(r.z,2)} mm　|　净质量流量 ${e3(r.net)} kg/s<br>相对于本层平均：<b>${pct(r.ratio)} </b>（${r.ratio<100?'低':'高'} ${pct(Math.abs(r.ratio-100))}）。正向 ${e3(r.fwd)} kg/s，反向幅值 ${e3(r.back)} kg/s。`;
 $('#closeChannel').onclick=closeChannel;
}
function selectChannel(l,ch,scroll=false){const same=selectedChannel&&selectedChannel.layer===l&&selectedChannel.ch===ch;if(same){closeChannel();return}selectedChannel={layer:l,ch};if(l!==currentLayer){currentLayer=l;setChannelTabs();}else highlightSelected();renderChannelDetail();if(scroll)window.scrollTo({top:scrollY+$('#zoomHeat').getBoundingClientRect().top-headerOffset(),left:0,behavior:'instant'})}
function highlightSelected(){const key=selectedChannel?chKey(selectedChannel.layer,selectedChannel.ch):'';$$('.heatcell,.zoom-cell').forEach(x=>x.classList.toggle('active',x.dataset.key===key));$$('.weak-table tr[data-key]').forEach(x=>x.classList.toggle('active',x.dataset.key===key));}
function calcZoomByX(a){
 // Window selection uses spatial X positions rather than G-indices. This also
 // works for staggered rows with different index counts or nonstandard IDs.
 const rows=[...new Set(a.rows.map(x=>x.row))].sort((x,y)=>x-y);
 const ref=a.rows.filter(x=>x.row===a.min.row).sort((x,y)=>x.x-y.x);
 const dx=ref.length>1?Math.max(.001,(ref[ref.length-1].x-ref[0].x)/(ref.length-1)):7;
 const weak=a.weak90.length?a.weak90:[a.min],lo=Math.min(...weak.map(x=>x.x)),hi=Math.max(...weak.map(x=>x.x));
 const span=Math.max(11*dx,(hi-lo)+5*dx);
 const allLo=Math.min(...a.rows.map(x=>x.x)),allHi=Math.max(...a.rows.map(x=>x.x));
 let x0=lo-Math.max(3*dx,(span-(hi-lo))/2),x1=x0+span;
 if(x1>allHi){x1=allHi+dx*.05;x0=x1-span}
 if(x0<allLo){x0=allLo-dx*.05;x1=x0+span}
 const out=rows.map(row=>a.rows.filter(x=>x.row===row&&x.x>=x0-dx*.05&&x.x<=x1+dx*.05).sort((x,y)=>x.x-y.x));
 const n=Math.max(...out.map(x=>x.length));return {out,n,x0,x1};
}
function renderChannels(){
 const a=S[currentLayer],ratioWorst=a.min.ratio,rows=[...new Set(a.rows.map(x=>x.row))].sort((x,y)=>x-y);const nInRow=Math.max(...rows.map(row=>a.rows.filter(r=>r.row===row).length));
 $('#heatSummary').innerHTML=metric('片间 CV',pct(F[currentLayer].cv),'越低表示整体分配越接近')+metric('低于平均值 90%',`${a.weak90.length} / ${a.n}`,'90%筛选值用于定位低流量区')+metric('本层最弱通道',pct(ratioWorst),`${a.min.ch} · X=${nm(a.min.x,1)}mm`,'danger');
 const layout=rows.map(row=>{const list=a.rows.filter(x=>x.row===row).sort((x,y)=>x.x-y.x);return `<div class="heat-row"><div class="heat-row-label">第 ${row} 排</div><div class="heat-cells" style="--n:${list.length}">`+list.map(r=>`<button type="button" class="heatcell chart-target ${r.ratio<90?'weak':''} ${r===a.min?'minimum':''}" data-key="${chKey(currentLayer,r.ch)}" data-ch="${esc(r.ch)}" style="background:${chColor(r.ratio,r.net)}" title="${esc(r.ch)} · ${pct(r.ratio)}" aria-label="${L[currentLayer]}第${row}排 ${esc(r.ch)} ${pct(r.ratio)}"></button>`).join('')+'</div></div>'}).join('');
 $('#fullHeat').innerHTML=layout;
 const first=a.rows.reduce((p,x)=>x.x<p.x?x:p,a.rows[0]),last=a.rows.reduce((p,x)=>x.x>p.x?x:p,a.rows[0]);$('#heatLeftX').textContent=`X 负方向 ${nm(first.x,0)} mm`;$('#heatRightX').textContent=`X 正方向 ${nm(last.x,0)} mm`;
 const zdata=calcZoomByX(a);$('#zoomRange').textContent=`X = ${nm(zdata.x0,0)}～${nm(zdata.x1,0)} mm（自动定位）`;
 let z=`<div class="zoom-grid" style="--zcount:${zdata.n}"><div class="zoom-label">通道</div>`+Array.from({length:zdata.n},(_,i)=>`<div class="zoom-label">${i+1}</div>`).join('');
 for(let i=0;i<rows.length;i++){let row=rows[i];z+=`<div class="zoom-label rowtitle">排 ${row}</div>`;for(let k=0;k<zdata.n;k++){
  let r=zdata.out[i][k];z+=r?`<button class="zoom-cell chart-target ${r.ratio<90?'weak':''} ${r===a.min?'minimum':''}" data-key="${chKey(currentLayer,r.ch)}" data-ch="${esc(r.ch)}" title="${esc(r.ch)}，X ${nm(r.x,1)} mm，${pct(r.ratio)}" style="background:${chColor(r.ratio,r.net)}" type="button"><span class="zoom-ch">${esc(r.ch)}</span><span class="zoom-ratio">${nm(r.ratio,1)}%</span></button>`:`<div class="zoom-cell" style="background:#eef3f4"></div>`;}}
 $('#zoomHeat').innerHTML=z+'</div><div class="zoom-callout"><b>最弱通道已圈出：'+esc(a.min.ch)+'</b> · '+pct(a.min.ratio)+'（X = '+nm(a.min.x,2)+' mm）。红框标记的其余通道低于本层平均值 90%。</div>';
 // For mobile, start at the most relevant weak end instead of hiding it offscreen.
 const zbox=$('#zoomHeat'),target=Array.from(zbox.querySelectorAll('[data-ch]')).find(e=>e.dataset.ch===a.min.ch);if(target)zbox.scrollLeft+=target.getBoundingClientRect().left-zbox.getBoundingClientRect().left-zbox.clientWidth/2;
 $$('#fullHeat [data-ch],#zoomHeat [data-ch]').forEach(e=>{e.onclick=()=>selectChannel(currentLayer,e.dataset.ch);e.onkeydown=k=>{if(k.key==='Enter'||k.key===' '){k.preventDefault();selectChannel(currentLayer,e.dataset.ch)}}});
 renderChannelDetail();highlightSelected();
 threeFacts('#channelExplainer',D.analysis.channel);
 renderChannelLegend();
}

function renderChannelContrasts(){
 const delta=Math.max(10,Math.ceil(Math.max(...CH.map(c=>Math.abs(c.ratio-100)))/5)*5),x=v=>50+v/delta*50;
 $('#extremesChart').innerHTML=['upper','lower'].map(l=>{const a=S[l],lo=a.min.ratio-100,hi=a.max.ratio-100;return '<div class="deviation-row"><div class="deviation-title"><b>'+L[l]+'</b><span>低于平均90%：'+a.weak90.length+' / '+a.n+'条</span></div><div class="deviation-track"><i class="deviation-zero"></i><i class="deviation-low" style="left:'+x(lo)+'%;width:'+Math.abs(lo)/delta*50+'%"></i><i class="deviation-high" style="left:50%;width:'+Math.abs(hi)/delta*50+'%"></i><b class="deviation-value low" style="left:'+x(lo)+'%">'+(lo>=0?'+':'')+nm(lo,2)+'%</b><b class="deviation-value high" style="left:'+x(hi)+'%">'+(hi>=0?'+':'')+nm(hi,2)+'%</b></div></div>'}).join('')+'<div class="deviation-axis"><span>−'+delta+'%</span><span>0% 本层平均</span><span>＋'+delta+'%</span></div><p class="chart-takeaway">'+L[minLayer]+'最弱通道比本层平均少'+pct(100-worst.ratio)+'。两层共用同一尺度，左侧蓝条越长，局部低流量越突出。</p>';
 const arr=[...S[minLayer].rows].sort((a,b)=>a.net-b.net).slice(0,6);
 $('#weakTable').innerHTML='<table class="weak-table"><thead><tr><th>通道</th><th>X / mm</th><th>相对均值</th></tr></thead><tbody>'+arr.map(r=>'<tr data-key="'+chKey(minLayer,r.ch)+'" data-ch="'+esc(r.ch)+'" tabindex="0" role="button"><td>'+L[minLayer]+' '+esc(r.ch)+'</td><td>'+nm(r.x,1)+'</td><td>'+pct(r.ratio)+'</td></tr>').join('')+'</tbody></table>';
 $$('#weakTable tr[data-ch]').forEach(tr=>{tr.onclick=()=>{currentLayer=minLayer;setChannelTabs();selectChannel(minLayer,tr.dataset.ch,true)};tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tr.click()}}});
}
let selectedHole=null,holeFilter='all',holeRow=0,palette='enhanced';
function holeColor(r){const dv=r-100;if(palette==='threshold')return r<90?'#234f9d':r<95?'#6ba9d1':r<=105?'#bcc9c8':r<=110?'#e5a459':'#bf462d';const lo=100-getRatio(hMin),hi=getRatio(hMax)-100;return dv<0?mix('#b9c8c7','#24589a',Math.abs(dv)/Math.max(1,lo)):mix('#b9c8c7','#c75230',dv/Math.max(1,hi))}
function closeHole(){selectedHole=null;renderHoleDetail();$$('.hole-dot').forEach(x=>x.classList.remove('active'))}
function renderHoleDetail(){const el=$('#holeDetail');if(!selectedHole){el.className='selection';el.textContent='点选孔位，查看其编号、真实坐标、净流量与相对整板逐孔平均值。再次点击可关闭。';return}
 const h=HO.find(z=>z.id===selectedHole);if(!h){closeHole();return}const row=h.row;
 el.className='selection selected';el.innerHTML=`<b>孔 #${h.id} · 第 ${row||'?'} 排</b><button type="button" id="closeHole">关闭 ×</button><br>坐标 X = ${nm(h.x,2)} mm，Z = ${nm(h.z,2)} mm；净质量流量 <b>${e3(h.net)} kg/s</b><br>网格开口尺寸 ${Number.isFinite(h.width_x_mm)&&Number.isFinite(h.width_z_mm)?nm(h.width_x_mm,2)+' × '+nm(h.width_z_mm,2)+' mm（X × Z）':'未取得'}<br>相对整板平均：<b>${pct(getRatio(h))}</b>（${getRatio(h)<100?'低':'高'} ${pct(Math.abs(getRatio(h)-100))}）。正向 ${e3(h.fwd)} kg/s，反向幅值 ${e3(h.back)} kg/s。`;
 $('#closeHole').onclick=closeHole;
}
function selectHole(id){if(selectedHole===id){closeHole();return}selectedHole=id;renderHoleDetail();$$('.hole-dot').forEach(x=>x.classList.toggle('active',Number(x.dataset.id)===id))}
function applyHoleFilter(h){const r=getRatio(h);return(holeFilter==='all'||(holeFilter==='low'&&r<95)||(holeFilter==='high'&&r>105)||(holeFilter==='extreme'&&(r<90||r>110)))}
function renderBaffleCompare(){
 $('#baffleCompare').innerHTML='<table><thead><tr><th>挡板 / 作用</th><th>孔数 / 排数</th><th>孔流量CV</th><th>最弱 / 本板均值</th><th>最强 / 本板均值</th></tr></thead><tbody>'+Object.entries(D.baffles).map(([k,b])=>{if(!b.available)return '<tr><td>'+b.label+'</td><td colspan="4">逐孔统计尚未取得</td></tr>';const lo=Math.min(...b.holes.map(h=>h.ratio)),hi=Math.max(...b.holes.map(h=>h.ratio));return '<tr><td>'+b.label+'<br><small>'+(k==='upper'?'进入晶圆区前分配气体':'离开晶圆区后分配排气')+'</small></td><td>'+b.holes.length+'孔 / '+b.rows.length+'排</td><td>'+pct(b.cv)+'</td><td>'+pct(lo)+'</td><td>'+pct(hi)+'</td></tr>'}).join('')+'</tbody></table>';
}
function setBaffle(board){
 renderBaffleCompare();
 activeBaffle=board;const b=D.baffles[board];HO=b.holes;BD=b.rows;holeAvg=HO.reduce((s,h)=>s+h.net,0)/(HO.length||1);hMin=HO.reduce((a,h)=>!a||h.net<a.net?h:a,null);hMax=HO.reduce((a,h)=>!a||h.net>a.net?h:a,null);selectedHole=null;holeFilter='all';holeRow=0;
 $$('#baffleSwitch button').forEach(e=>e.classList.toggle('active',e.dataset.board===board));
 $('#activeBaffleTitle').textContent=b.label+' · 孔位与流量';$('#holeCount').textContent=HO.length?HO.length+'个孔，'+BD.length+'排；按本板平均单孔流量比较。':b.reason;
 $('#baffleRole').textContent=board==='upper'?'上部进气挡板：看气体进入晶圆区之前，各个孔分到的气体是否接近。':'下部出气挡板：看气体离开晶圆区时，各个位置怎样排气。';
 $('#baffleRole').textContent+=' 俯视方向为沿−Y向下看，图右为+X、图下为+Z。'+(valid(b.cut_y_m)?'统计高度Y='+nm(b.cut_y_m*1000,2)+' mm。':'');
 $('#holeRows').innerHTML='<button data-row="0" class="active">全部孔排</button>'+BD.map(r=>'<button data-row="'+r.row+'">第'+r.row+'排</button>').join('');
 $$('#holeRows button').forEach(e=>e.onclick=()=>{holeRow=Number(e.dataset.row);closeHole();renderBaffles()});
 renderBaffles();renderBaffleRows();threeFacts('#baffleExplainer',D.analysis.boards[board]);
}
const highlightedDiameters=new Set(['6.0','7.0','8.0','8.5','9.0','9.5']);
const diameterRamp=['#f4f4f4','#d9d9d9','#bdbdbd','#9b9b9b','#777777','#525252'];

function renderBaffles(){
 if(!HO.length){$('#baffleStats').textContent='此挡板的逐孔统计尚未取得。';$('#holeSvg').innerHTML='';$('#holeLegend').innerHTML='';$('#holeRowTable').innerHTML='';$('#baffleInsight').textContent='';return;}
 $('#baffleStats').innerHTML=metric('最弱孔 / 整板平均',pct(getRatio(hMin)),`孔 #${hMin.id}`,'danger')+metric('最强孔 / 整板平均',pct(getRatio(hMax)),`孔 #${hMax.id}`)+metric('逐孔均值',e3(holeAvg)+' kg/s',`${HO.length} 个候选孔`);
 const xmin=Math.min(...HO.map(x=>x.x)),xmax=Math.max(...HO.map(x=>x.x)),zmin=Math.min(...HO.map(x=>x.z)),zmax=Math.max(...HO.map(x=>x.z));
 const xpix=x=>79+(x-xmin)/(xmax-xmin||1)*790,ypix=z=>36+(z-zmin)/(zmax-zmin||1)*225;
 const scale=Math.min(790/(xmax-xmin||1),225/(zmax-zmin||1));
 const measured=h=>Number.isFinite(h.width_x_mm)&&h.width_x_mm>0&&Number.isFinite(h.width_z_mm)&&h.width_z_mm>0;
 const measuredCount=HO.filter(measured).length;
 $('#holeCount').textContent+=' '+(measuredCount?'孔形按网格开口的 X/Z 尺寸等比例绘制。':'孔径未取得，点位为示意。')+(measuredCount>0&&measuredCount<HO.length?' 虚线孔位无尺寸。':'');
 let svg=`<rect x="50" y="21" width="829" height="255" fill="#fbfdfd" stroke="#dfe8eb" rx="8"/>`;
 const diameterColors=Object.fromEntries(['6.0','7.0','8.0','8.5','9.0','9.5'].map((v,i)=>[v,diameterRamp[i]]));
 const groups=new Map();
 for(const h of HO){
   if(holeRow&&h.row!==holeRow)continue;
   const x=h.x.toFixed(2);
   if(!groups.has(x))groups.set(x,new Set());
   groups.get(x).add(measured(h)&&h.width_x_mm.toFixed(1)===h.width_z_mm.toFixed(1)?h.width_x_mm.toFixed(1):'');
 }
 const columns=[...new Set(HO.map(h=>h.x.toFixed(2)))].map(Number).sort((a,b)=>a-b);
 const used=new Set();
 const bands=[];
 columns.forEach((x,i)=>{
   const sizes=groups.get(x.toFixed(2));
   const diameter=sizes?.size===1?[...sizes][0]:'';
   const fill=diameterColors[diameter];
   if(!fill)return;
   used.add(diameter);
   if(!highlightedDiameters.has(diameter))return;
   const center=xpix(x);
   const left=i?(xpix(columns[i-1])+center)/2:51;
   const right=i+1<columns.length?(xpix(columns[i+1])+center)/2:878;
   const last=bands.at(-1);
   if(last?.diameter===diameter&&Math.abs(last.right-left)<.001)last.right=right;
   else bands.push({left,right,diameter,fill});
 });
 for(const {left,right,fill} of bands)
   svg+=`<rect x="${left}" y="22" width="${right-left+.4}" height="253" fill="${fill}" pointer-events="none"/>`;
 for(let b of BD){let y=ypix(b.z);if(!highlightedDiameters.size)svg+=`<line x1="60" x2="880" y1="${y}" y2="${y}" stroke="#edf2f3" stroke-dasharray="3 5"/>`;svg+=`<text x="13" y="${y+4}" fill="#405e6b" font-size="13" font-weight="700">排${b.row}</text>`}
 for(let h of HO){const r=getRatio(h),row=h.row;const show=applyHoleFilter(h)&&(holeRow===0||holeRow===row);svg+=`<ellipse class="hole-dot chart-target ${h.id===hMin.id?'minhole':''} ${h.id===hMax.id?'maxhole':''} ${!show?'faded':''}" data-id="${h.id}" ${!show?'style="pointer-events:none"':''} rx="${measured(h)?h.width_x_mm*scale/2:3}" ry="${measured(h)?h.width_z_mm*scale/2:3}" ${measured(h)?'':'stroke-dasharray="2 2"'} cx="${xpix(h.x).toFixed(2)}" cy="${ypix(h.z).toFixed(2)}" fill="${holeColor(r)}" stroke="#fff" stroke-width="1" ${show?'tabindex="0"':'tabindex="-1"'} role="button" aria-label="孔${h.id} ${pct(r)}"/>`}
 const diameterColumns=new Map();
 for(const h of HO){
   if(holeRow!==0&&h.row!==holeRow)continue;
   const column=h.x.toFixed(2);
   if(!diameterColumns.has(column))diameterColumns.set(column,new Set());
   const sizes=diameterColumns.get(column);
   if(!measured(h)){sizes.add('—');continue;}
   const dx=h.width_x_mm.toFixed(1),dz=h.width_z_mm.toFixed(1);
   sizes.add(dx===dz?dx:dx+'×'+dz);
 }
 svg+=`<text x="4" y="291" font-size="9.5" fill="#405e6b">孔径 mm</text>`;
 for(const [x,sizes] of diameterColumns){
   const label=sizes.size===1?[...sizes][0]:'混合';
   const size=label.length>4?6.2:8.3;
   svg+=`<text x="${xpix(Number(x)).toFixed(2)}" y="291" text-anchor="middle" font-size="${size}" fill="#405e6b"><title>${[...sizes].join(' / ')} mm</title>${label}</text>`;
 }
 svg+=`<text x="60" y="312" font-size="12" fill="#657f89">X = ${nm(xmin,0)} mm</text><text x="880" y="312" font-size="12" fill="#657f89" text-anchor="end">X = ${nm(xmax,0)} mm</text>`;
 $('#holeSvg').innerHTML=svg;
 $$('#holeSvg .hole-dot').forEach(c=>{c.onclick=()=>selectHole(Number(c.dataset.id));c.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();c.click()}}});
 $('#holeLegend').innerHTML=`<span class="legend-item"><i class="swatch" style="background:#24589a"></i>低 ${pct(getRatio(hMin),1)}</span><span class="legend-item"><i class="swatch" style="background:#bcc9c8"></i>平均 100%</span><span class="legend-item"><i class="swatch" style="background:#c75230"></i>高 ${pct(getRatio(hMax),1)}</span><span class="helper">${palette==='enhanced'?'当前为基于本模型极值的连续增强色阶':'当前固定阈值：<90、90–95、95–105、105–110、>110%'}</span>`;
 $$('#holeFilters button').forEach(b=>b.classList.toggle('active',b.dataset.filter===holeFilter));$$('#holeRows button').forEach(b=>b.classList.toggle('active',Number(b.dataset.row)===holeRow));
 const sizes=[...used].sort((a,b)=>Number(a)-Number(b));
 if(sizes.length){
   const controls=document.createElement('div');
   controls.style.cssText='flex-basis:100%;display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px';
   const label=document.createElement('span');label.className='helper';label.textContent='高亮孔径（mm）';
   controls.append(label);


   for(const size of sizes){
     const button=document.createElement('button');
     button.type='button';button.className='btn';button.textContent=size;button.dataset.diameter=size;
     const selected=highlightedDiameters.has(size);
     button.setAttribute('aria-pressed',String(selected));
     button.style.cssText='padding:4px 9px;'+(selected?'background:'+diameterColors[size]+';border-color:#797979;color:'+(Number(size)>=9?'#fff':'#202020')+';font-weight:700':'');
     button.onclick=()=>{if(selected)highlightedDiameters.delete(size);else highlightedDiameters.add(size);renderBaffles();$('#holeLegend [data-diameter="'+size+'"]')?.focus();};
     controls.append(button);
   }
   const clear=document.createElement('button');clear.type='button';clear.className='btn';clear.textContent='清除';clear.disabled=highlightedDiameters.size===0;
   clear.style.padding='4px 9px';clear.onclick=()=>{highlightedDiameters.clear();renderBaffles();$('#holeLegend [data-diameter]')?.focus();};
   const all=document.createElement('button');all.type='button';all.className='btn';all.textContent='全选';
   all.disabled=sizes.every(size=>highlightedDiameters.has(size));all.style.padding='4px 9px';
   all.onclick=()=>{sizes.forEach(size=>highlightedDiameters.add(size));renderBaffles();$('#holeLegend [data-diameter]')?.focus();};
   controls.append(clear,all);$('#holeLegend').append(controls);
 }
 renderHoleDetail();if(selectedHole){$$('.hole-dot').forEach(x=>x.classList.toggle('active',Number(x.dataset.id)===selectedHole))}
}
function renderBaffleRows(){if(!BD.length){$("#baffleInsight").textContent="孔排数据未取得。";return;}let tot=BD.reduce((s,x)=>s+x.mass,0)||1;
 $('#baffleBars').innerHTML=`<div class="baffle-bars-grid">`+BD.map(r=>`<label>第 ${r.row} 排 (${r.n} 孔)</label><div class="bar-back"><div class="bar-fill" style="width:${Math.min(100,r.mass/tot*100/Math.max(1,...BD.map(y=>y.mass/tot*100))*100)}%"></div></div><b>${pct(r.mass/tot*100)}</b>`).join('')+`</div><p class="tiny">柱长按本模型最大孔排流量归一；右侧为各排占挡板净流量的比例。</p>`;
 const top=BD.reduce((a,b)=>a.mass>b.mass?a:b),bottom=BD.reduce((a,b)=>a.mass<b.mass?a:b);
 $('#baffleInsight').textContent=D.analysis.boards[activeBaffle][1];
 $('#holeRowTable').innerHTML='<table><thead><tr><th>孔排</th><th>孔数</th><th>总流量 kg/s</th><th>平均单孔 kg/s</th><th>排内CV</th></tr></thead><tbody>'+BD.map(r=>{const a=HO.filter(h=>h.row===r.row),mean=r.mass/r.n,cv=mean?Math.sqrt(a.reduce((s,h)=>s+(h.net-mean)**2,0)/a.length)/mean*100:null;return '<tr><td>'+r.row+'</td><td>'+r.n+'</td><td>'+e3(r.mass)+'</td><td>'+e3(mean)+'</td><td>'+pct(cv)+'</td></tr>'}).join('')+'</tbody></table>';

}

let selectedPress=null;
function pressureColor(v,min,max){return v<=((min+max)/2)?mix('#245b9a','#f3e9d9',(v-min)/(Math.max(.00001,(max-min)/2))):mix('#f3e9d9','#c95731',(v-(min+max)/2)/(Math.max(.00001,(max-min)/2)))}
function pressDetail(){let el=$('#pressureDetail');if(selectedPress===null){el.className='selection';el.textContent='点击任意色块，可查看该分箱的平均静压和实际单元中心采样数。';return}
 const p=PM.bins[selectedPress];el.className='selection selected';el.innerHTML=`<b>已选中压力分箱 #${selectedPress+1}</b><button type="button" id="closePress">关闭 ×</button><br>平均静压 <b>${nm(p.mean_pa,6)} Pa</b>，采样单元 ${num(p.n)} 个。<br>X分箱 ${nm(p.x_lo_m*1000,2)}～${nm(p.x_hi_m*1000,2)} mm；Z分箱 ${nm(p.z_lo_m*1000,2)}～${nm(p.z_hi_m*1000,2)} mm。<br>该值为分箱内单元中心静压的算术平均。`;
 $('#closePress').onclick=()=>{selectedPress=null;pressDetail();$$('.pbin').forEach(r=>r.classList.remove('active'))};
}
function renderPressure(){
 const p=D.meta.pressure||{},ref=D.pressureReference.operating_pressure_pa;
 const baseline=valid(ref)?'静压以Case中的工作压力'+nm(ref,2)+' Pa为基准；图中数值加'+nm(ref,2)+' Pa可得绝对静压。':'静压采用保存解SV_P数值，压力基准见方法页。';
 $('#totalPressureSummary').textContent=valid(D.pressureDrop)?'气体从入口到出口，总压降估计为'+nm(D.pressureDrop,5)+' Pa。整体压降用于比较同一入口流量下不同方案需要的驱动压差。':'入口至出口的总压降未取得。';
 renderPressurePath();threeFacts('#pressureExplainer',D.analysis.pressure);
 $('#pressureViewNote').textContent='观察方向：从上方向下看（沿−Y），图右为+X，图下为+Z，气体沿Y方向流动。每一色块代表该X/Z小区域的平均静压。'+baseline;
 const yr=PM.sample_y_range_m;if(yr?.every(valid))$('#pressureViewNote').textContent+='采样高度Y='+yr.map(v=>nm(v*1000,2)).join('～')+' mm，位于下挡板与腔底之间。';
 if(!PM.bins.length){$('#pressureKpis').textContent='集气腔静压分箱未取得。';$('#pressureSvg').innerHTML='';return;}
 const bins=PM.bins,min=Math.min(...bins.map(p=>p.mean_pa)),max=Math.max(...bins.map(p=>p.mean_pa));
 $('#pressureKpis').innerHTML=metric('最低区域平均静压',nm(min,5)+' Pa','图上直接标出最低位置')+metric('最高区域平均静压',nm(max,5)+' Pa','图上直接标出最高位置')+metric('区域之间相差',nm(max-min,5)+' Pa','最高区域减最低区域');
 $('#pressMin').textContent=nm(min,5)+' Pa';$('#pressMean').textContent=nm((min+max)/2,5)+' Pa';$('#pressMax').textContent=nm(max,5)+' Pa';
 const xmin=Math.min(...bins.map(b=>b.x_lo_m)),xmax=Math.max(...bins.map(b=>b.x_hi_m)),zmin=Math.min(...bins.map(b=>b.z_lo_m)),zmax=Math.max(...bins.map(b=>b.z_hi_m));
 const mx=x=>49+777*(x-xmin)/(xmax-xmin),mz=z=>41+214*(z-zmin)/(zmax-zmin);
 let svg='<rect x="49" y="41" width="777" height="214" fill="none" stroke="#647e88"/><text x="130" y="23" font-size="12">着色范围：已取得真实采样的集气腔区域</text>';
 for(let t=0;t<=4;t++){let x=xmin+(xmax-xmin)*t/4,z=zmin+(zmax-zmin)*t/4;svg+='<text x="'+mx(x)+'" y="280" text-anchor="middle" font-size="12">'+nm(x*1000,0)+'</text><text x="40" y="'+(mz(z)+4)+'" text-anchor="end" font-size="12">'+nm(z*1000,0)+'</text>';}
 svg+='<text x="430" y="305" text-anchor="middle" font-size="13">X / mm →</text><text x="4" y="20" font-size="12">Z / mm ↓</text>';
 bins.forEach((p,i)=>{svg+='<rect class="pbin chart-target" data-id="'+i+'" x="'+p.x+'" y="'+p.y+'" width="'+p.w+'" height="'+p.h+'" fill="'+pressureColor(p.mean_pa,min,max)+'" stroke="white" stroke-width=".3" tabindex="0" role="button" aria-label="压力分箱'+(i+1)+' '+nm(p.mean_pa,5)+'Pa"/>'});
 for(const [index,mark] of [[bins.findIndex(p=>p.mean_pa===min),'最低'],[bins.findIndex(p=>p.mean_pa===max),'最高']]){const p=bins[index],cx=p.x+p.w/2,cy=p.y+p.h/2,tx=Math.max(100,Math.min(760,cx));svg+='<rect x="'+p.x+'" y="'+p.y+'" width="'+p.w+'" height="'+p.h+'" fill="none" stroke="#112f3b" stroke-width="2" pointer-events="none"/><path d="M'+cx+' '+cy+' L'+tx+' '+Math.max(52,cy-22)+'" stroke="#112f3b"/><text x="'+tx+'" y="'+Math.max(49,cy-25)+'" text-anchor="middle" font-size="11" fill="#112f3b" stroke="white" stroke-width="3" paint-order="stroke">'+mark+' '+nm(p.mean_pa,5)+' Pa</text>';}
 const outs=PM.outlets||[];outs.forEach(o=>{const [x,y,z]=o.center_m;if(x<xmin||x>xmax||z<zmin||z>zmax)return;svg+='<circle class="outlet-marker" cx="'+mx(x)+'" cy="'+mz(z)+'" r="8" fill="none" stroke="white" stroke-width="4"/><circle cx="'+mx(x)+'" cy="'+mz(z)+'" r="8" fill="none" stroke="#173e50" stroke-width="2"/><text x="'+mx(x)+'" y="'+(mz(z)+24)+'" text-anchor="middle" font-size="11" fill="#173e50" stroke="white" stroke-width="3" paint-order="stroke">出口'+o.index+'投影</text>'});
 $('#pressureSvg').innerHTML=svg;
 $$('#pressureSvg .pbin').forEach(el=>{el.onclick=()=>{const id=Number(el.dataset.id);selectedPress=id===selectedPress?null:id;$$('.pbin').forEach(r=>r.classList.toggle('active',Number(r.dataset.id)===selectedPress));pressDetail()};el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.dispatchEvent(new MouseEvent('click',{bubbles:true}))}}});pressDetail();
 const inlet=D.meta.port_flux.inlet_kg_s;
 $('#outletTable').innerHTML=outs.length?'<table><thead><tr><th>实际出口</th><th>X / Z mm</th><th>净流量 kg/s</th><th>占入口流量</th><th>投影所在区域静压</th></tr></thead><tbody>'+outs.map(o=>{const [x,y,z]=o.center_m,box=bins.find(b=>b.x_lo_m<=x&&x<=b.x_hi_m&&b.z_lo_m<=z&&z<=b.z_hi_m);return '<tr><td>出口'+o.index+'</td><td>'+nm(x*1000,1)+' / '+nm(z*1000,1)+'</td><td>'+e3(o.net_kg_s)+'</td><td>'+pct(100*o.net_kg_s/inlet)+'</td><td>'+(box?nm(box.mean_pa,5)+' Pa':'投影不在采样区域')+'</td></tr>'}).join('')+'</tbody></table>':'';
 const shares=outs.map(o=>100*o.net_kg_s/inlet).filter(valid);
 $('#outletExplanation').textContent=outs.length?'圆圈是'+outs.length+'个真实出口中心在X/Z平面的投影，编号与下表一致。'+(shares.length?'各出口流量占入口的'+shares.map(v=>pct(v)).join('、')+'；最大与最小相差'+nm(Math.max(...shares)-Math.min(...shares),2)+'个百分点。':''):'当前结果未取得出口的物理位置。';
}
function renderPressurePath(){
 const stations=D.stations.filter(s=>valid(s.mean));if(stations.length<2){$('#pressurePath').innerHTML='';$('#pressurePathConclusion').textContent='沿流路测站数据不足。';return;}
 const n=stations.length,ys=stations.map((s,i)=>80+i*360/(n-1)),min=Math.min(...stations.map(s=>s.mean)),max=Math.max(...stations.map(s=>s.mean)),pad=Math.max((max-min)*.12,.001),lo=min-pad,hi=max+pad,x=p=>540+(p-lo)/(hi-lo)*400;
 let out='<defs><marker id="downArrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#147f79"/></marker></defs><text x="35" y="25" font-size="16" font-weight="700">气体向下流动 ↓</text><text x="540" y="25" font-size="15" font-weight="700">对应位置的平均静压 / Pa</text>';
 out+='<text x="540" y="45" font-size="11" fill="#637c86">'+(valid(D.pressureReference.operating_pressure_pa)?'相对工作压力 '+nm(D.pressureReference.operating_pressure_pa,2)+' Pa':'保存解静压 SV_P，基准见方法')+'</text>';
 out+='<path d="M165 55V465" stroke="#147f79" stroke-width="3" marker-end="url(#downArrow)"/>';
 if(n===4){out+='<rect x="40" y="115" width="250" height="30" rx="5" fill="#e1f0ed" stroke="#7eaaa3"/><text x="165" y="136" font-size="14" text-anchor="middle">上部进气挡板</text><rect x="70" y="240" width="190" height="45" rx="5" fill="#edf2f8" stroke="#849ab1"/><text x="165" y="267" font-size="14" text-anchor="middle">上下层晶圆区</text><rect x="40" y="375" width="250" height="30" rx="5" fill="#e1f0ed" stroke="#7eaaa3"/><text x="165" y="396" font-size="14" text-anchor="middle">下部出气挡板</text>'}
 for(let i=0;i<=4;i++){let v=lo+(hi-lo)*i/4;out+='<line x1="'+x(v)+'" x2="'+x(v)+'" y1="55" y2="470" stroke="#dce6e9"/><text x="'+x(v)+'" y="505" text-anchor="middle" font-size="12">'+nm(v,3)+'</text>'}
 out+='<polyline points="'+stations.map((s,i)=>x(s.mean)+','+ys[i]).join(' ')+'" fill="none" stroke="#267098" stroke-width="3"/>';
 stations.forEach((s,i)=>{out+='<line x1="300" x2="960" y1="'+ys[i]+'" y2="'+ys[i]+'" stroke="#dce6e9" stroke-dasharray="4 5"/><text x="310" y="'+(ys[i]-12)+'" font-size="13">'+esc(s.name)+'</text><text x="310" y="'+(ys[i]+10)+'" font-size="11" fill="#637c86">'+(valid(s.y_m)?'Y='+nm(s.y_m*1000,2)+' mm':'')+'</text><circle cx="'+x(s.mean)+'" cy="'+ys[i]+'" r="6" fill="#267098"/><text x="'+x(s.mean)+'" y="'+(ys[i]-13)+'" text-anchor="middle" font-size="13" font-weight="700">'+nm(s.mean,5)+' Pa</text>';if(i<n-1)out+='<text x="310" y="'+((ys[i]+ys[i+1])/2+8)+'" font-size="13" fill="#a14f2e">静压'+(s.mean>=stations[i+1].mean?'下降 ':'上升 ')+nm(Math.abs(s.mean-stations[i+1].mean),5)+' Pa</text>'});
 out+='<text x="40" y="540" font-size="12" fill="#607883">测站按流路顺序排列，Y标注实际位置；结构框用于说明经过的部位。</text>';
 $('#pressurePath').innerHTML=out;$('#pressurePathConclusion').textContent=D.analysis.pressure[0]+' 压差是推动气体穿过结构的驱动力之一；结合逐孔流量，可以检查气体在哪些位置分配较多或较少。';
}

function renderActions(){
 $('#actionCards').innerHTML=D.analysis.actions.map((a,i)=>'<article class="action"><div class="action-no">'+String(i+1).padStart(2,'0')+'</div><div><h3>'+esc(a.title)+'</h3><p><b>结果依据：</b>'+esc(a.evidence)+'</p><div class="how">'+[['原因假设',a.hypothesis],['验证办法',a.check],['单变量方向',a.variable],['评价指标',a.accept]].map(([k,v])=>'<div><b>'+k+'</b><span>'+esc(v)+'</span></div>').join('')+'</div><button class="btn action-evidence" data-target="'+a.target+'" data-board="'+esc(a.board||'')+'">查看对应证据</button></div></article>').join('');$$('.action-evidence').forEach(b=>b.onclick=()=>{if(b.dataset.board)setBaffle(b.dataset.board);jump(b.dataset.target)});
}
function renderEvidence(){const p=D.meta.provenance||{};const checks=[
 ['进出口质量守恒','净差 '+pct(D.massImbalance)+'；该值不能代替求解收敛。',valid(D.massImbalance)?'已计算':'未取得'],
 ['求解是否收敛','未提供独立残差及监控历史，不能由保存解快照确认。','待核验'],
 ['几何识别与分区',D.meta.geometry?.status||'候选几何，需独立核对','待核验'],
 ['Case/Data共享网格身份',p.case_data_identity_evidence||'未取得独立证明',p.case_data_pair_independently_verified===true?'已计算':'待核验'],
 ['压力与材料口径',D.meta.pressure?.definition||'缺少可靠压力定义','待核验']
 ];
 $('#auditCards').innerHTML=checks.map(r=>`<div class="auditline"><div><b>${esc(r[0])}</b><p>${esc(r[1])}</p></div><span class="audit-label ${r[2]==='已计算'?'':'wait'}">${esc(r[2])}</span></div>`).join('');
 const glossary=[['片间占比','本层片间通道净流量之和 / 入口净质量流量。上下截面在串联流路上，不能累加。'],['CV（变异系数）','通道流量标准差除以平均值，用来衡量整体离散程度；越低通常代表相互越接近。'],['相对平均值','某条通道或某个孔的净流量 / 其所在统计群体的平均净流量 × 100%。100% 是平均，不是“通过率”。'],['弱流阈值','此报告为了定位而选用的 90% 或 95% 相对平均值阈值，不是工艺验收规范。'],['旁路','没有经过被算法定义为晶圆片间通道的其他流路。端部子集包含在外围分区内。'],['静压与总压','静压是局部热力学压力分量；总压还涉及流动动能。不同口径/测站不可混为一谈。'],['分箱','把空间数据划分成小区域后做统计。此图颜色表示每个分箱内单元中心静压的算术平均，不是 Fluent 原生等值面。']];
 $('#glossary').className='glossary';$('#glossary').innerHTML=glossary.map(([h,t],i)=>`<details ${i===0?'open':''}><summary>${esc(h)}</summary><p>${esc(t)}</p></details>`).join('');
 let archive='<div class="raw">';
 for(let i=0;i<D.rawTables.length;i++){const t=D.rawTables[i];archive+=`<details><summary>表 ${i+1} · ${esc(t.section)} <span class="sub">(${t.rows.length} 行)</span></summary><div class="raw-table-wrap"><table><thead><tr>${t.head.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${t.rows.map(r=>`<tr>${r.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`}
 for(let i=0;i<D.rawFigures.length;i++){const f=D.rawFigures[i];archive+=`<details><summary>图 ${i+1} · ${esc(f.section)} <span class="sub">(原始 SVG，保留归档)</span></summary><div class="raw-svg">${f.svg}</div></details>`}
 archive+='<details><summary>来源信息 · 几何候选与质量检查 <span class="sub">(JSON 元数据)</span></summary><pre class="raw-meta">'+esc(JSON.stringify(D.meta,null,2))+'</pre></details>';
 $('#originalArchive').innerHTML=archive+'</div>';
 $('#rawShow').onclick=()=>$$('#originalArchive details').forEach(x=>x.open=true);$('#rawHide').onclick=()=>$$('#originalArchive details').forEach(x=>x.open=false);
 $('#exportChannels').onclick=()=>downloadCsv('AF_channels.csv',['层','排','通道','X_mm','Z_mm','正向_kg_s','反向幅值_kg_s','净_kg_s','相对本层均值_pct'],CH.map(r=>[L[r.layer],r.row,r.ch,r.x,r.z,r.fwd,r.back,r.net,r.ratio]));
 $('#exportHoles').onclick=()=>downloadCsv('AF_'+activeBaffle+'_holes.csv',['挡板','孔号','X_mm','Z_mm','正向_kg_s','反向幅值_kg_s','净_kg_s','相对整板逐孔均值_pct'],HO.map(r=>[D.baffles[activeBaffle].label,r.id,r.x,r.z,r.fwd,r.back,r.net,getRatio(r)]));
 $('#exportPressure').onclick=()=>downloadCsv('AF_pressure_bins.csv',['分箱编号','单元平均静压_Pa','样本数','X下界_m','X上界_m','Z下界_m','Z上界_m'],PM.bins.map((r,i)=>[i+1,r.mean_pa,r.n,r.x_lo_m,r.x_hi_m,r.z_lo_m,r.z_hi_m]));
}
function downloadCsv(name,header,rows){const enc=arr=>arr.map(c=>'"'+String(c??'').replaceAll('"','""')+'"').join(',');const text='\ufeff'+[enc(header),...rows.map(enc)].join('\r\n');let url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));let a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000)}
function setupEvents(){
 $$('#baffleSwitch button').forEach(b=>b.onclick=()=>setBaffle(b.dataset.board));
 $$('#channelMetric button').forEach(b=>b.onclick=()=>{channelMode=b.dataset.mode;$$('#channelMetric button').forEach(x=>x.classList.toggle('active',x===b));renderChannels();renderAtlas()});
 $$('#channelLayerSwitch button').forEach(b=>b.onclick=()=>{currentLayer=b.dataset.layer;selectedChannel=null;setChannelTabs()});
 $$('#holeFilters button').forEach(b=>b.onclick=()=>{holeFilter=b.dataset.filter;closeHole();renderBaffles()});
 $$('#holeRows button').forEach(b=>b.onclick=()=>{holeRow=Number(b.dataset.row);closeHole();renderBaffles()});
 $('#paletteEnhanced').onclick=()=>{palette='enhanced';$('#paletteEnhanced').classList.add('active');$('#paletteThreshold').classList.remove('active');closeHole();renderBaffles()};
 $('#paletteThreshold').onclick=()=>{palette='threshold';$('#paletteThreshold').classList.add('active');$('#paletteEnhanced').classList.remove('active');closeHole();renderBaffles()};
 document.addEventListener('click',e=>{if(selectedChannel&&!e.target.closest('#fullHeat,#zoomHeat,#channelDetail,#channelLayerSwitch,#weakTable,#atlasSvg,#channelMetric'))closeChannel();if(selectedHole&&!e.target.closest('#holeSvg,#holeDetail,#holeFilters,#holeRows,#atlasSvg,#baffleSwitch,[data-atlas],[data-action-evidence]'))closeHole();if(selectedPress!==null&&!e.target.closest('#pressureSvg,#pressureDetail,#atlasSvg,[data-atlas],[data-action-evidence]')){selectedPress=null;pressDetail();$$('.pbin').forEach(r=>r.classList.remove('active'))}});
 // Inline details never cover the chart: keep selection while scrolling to it.
 // Clicking the same point, switching filters/rows, and explicit Close still dismiss it.
}
setup();renderOverview();renderFlow();renderChannels();setBaffle(activeBaffle);renderAtlas();renderChannelContrasts();renderPressure();renderActions();renderEvidence();setupEvents();
})();

;(()=>{
 const root=document.getElementById('species'); if(!root)return;
 function choose(value){root.querySelectorAll('[data-species-panel]').forEach(panel=>{panel.hidden=panel.dataset.speciesPanel!==value});root.querySelectorAll('[data-species-choice]').forEach(btn=>{const selected=btn.dataset.speciesChoice===value;btn.classList.toggle('active',selected);btn.setAttribute('aria-pressed',String(selected))});const select=root.querySelector('#speciesSelect');if(select)select.value=value;}
 root.querySelector('#speciesSelect')?.addEventListener('change',e=>choose(e.target.value));
 function activate(target){const button=target.closest('[data-species-choice]');if(button){choose(button.dataset.speciesChoice);return;}const channel=target.closest('[data-species-detail]');if(channel){const detail=root.querySelector('#speciesDetail'+channel.dataset.speciesIndex);if(detail){detail.textContent=channel.dataset.speciesDetail;detail.classList.add('selected')}}}
 root.addEventListener('click',e=>activate(e.target));
 root.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-species-detail]')){e.preventDefault();activate(e.target)}});
})();
