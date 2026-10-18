"""Offline view of a completed actual-wallclock session; no background HTTP."""
# AI-assisted: OpenAI Codex; retain actual use and human review.
import json
from .paths import APP
from .live_accounting import finalize_accounting
import sumolib


def export_wallclock_dashboard(run_id):
    if not run_id.replace('_','').replace('-','').isalnum():raise ValueError('Invalid run ID')
    directory=APP/'results'/run_id
    meta=json.loads((directory/'run.json').read_text(encoding='utf-8'))
    assert meta['status']=='completed' and meta.get('wallclock_protocol')
    states=[json.loads(l) for l in (directory/'states_60s.jsonl').read_text(encoding='utf-8').splitlines()]
    collections=[json.loads(l) for l in (directory/'collection.jsonl').read_text(encoding='utf-8').splitlines()]
    net=sumolib.net.readNet(str(directory/'network.net.xml'))
    roads=[{'id':e.getID(),'name':e.getName(),'shape':e.getShape(),'lanes':e.getLaneNumber()} for e in net.getEdges()]
    pts=[p for e in roads for p in e['shape']]
    data={'run_id':run_id,'run':meta,'states':states,'collections':collections,'roads':roads,
        'bounds':[min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)],
        'metrics':finalize_accounting(directory)}
    template=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>B题 · 在线运行记录</title><style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#243b46;background:#f2f6f6}*{box-sizing:border-box}body{margin:auto;padding:28px;max-width:1400px}h1{font-size:26px;margin:10px 0}h2{font-size:17px;margin:0 0 12px}p{line-height:1.8;color:#536b75;overflow-wrap:anywhere}.tag{background:#e1eee9;color:#236955;padding:5px 10px;border-radius:18px;font-size:12px;display:inline-block}.notice{padding:12px 18px;border-left:3px solid #b88b3d;background:#fbf6eb;margin:22px 0}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card,.panel{background:white;border:1px solid #dce6e6;border-radius:10px;padding:20px;min-width:0}.number{font-size:26px;color:#226653;display:block;margin-top:8px}.label,.small{font-size:12px}.grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:20px;margin-top:20px}#map{width:100%;height:460px;background:#f6f9f9;border-radius:7px}.road{fill:none;stroke:#c6d4dc;stroke-width:1.5;vector-effect:non-scaling-stroke;stroke-linecap:round}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:15px}button{border:0;border-radius:6px;background:#266a57;color:white;padding:9px 18px;cursor:pointer}input{flex:1;min-width:60px}table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:10px 7px;border-bottom:1px solid #e7eded}.scroll{overflow:auto;max-height:370px}.legend{font-size:12px;color:#63787c;line-height:1.8}.footer{margin-top:20px;font-size:12px}@media(max-width:850px){body{padding:16px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:minmax(0,1fr)}#map{height:360px}h1{font-size:23px}}
</style><header><span class="tag" id="runType">真实时间运行 · 离线记录</span><h1>九龙交通：在线运行记录</h1><p>查看本次真实采集、仿真配速与需求更新。页面打开后不会请求接口。</p></header>
<div class="notice"><p id="scope">程序已按真实时间采集并驱动SUMO；当前时段需求和初始排队尚未验证，不能据此判断当前路况精度。</p><p id="scenario">98道路 · 自由出口 · 从空网启动 · 沿用历史普通时段先验</p></div>
<div class="cards"><div class="card"><span class="label">按真实时间运行</span><strong class="number" id="duration"></strong></div><div class="card"><span class="label">HTTP成功 / 自然失败 / 注入失败</span><strong class="number" id="acquisitions"></strong></div><div class="card"><span class="label">计划次数 / 有合格约束</span><strong class="number" id="decisions"></strong></div><div class="card"><span class="label">瞬移 / 碰撞</span><strong class="number" id="safety"></strong></div></div>
<section class="panel" id="wallclockPanel" style="margin-top:20px"><h2>本帧时间与后续计划</h2><p id="updateStatus"></p><p class="small" id="updateTimes"></p><p class="small" id="sourceTimes"></p><p class="small" id="updatePlan"></p><p class="small">每60秒采集、每300秒决策。低覆盖、过期或断流时保持计划；保持不表示真实需求没变。</p></section>
<div class="grid"><section class="panel"><h2>仿真状态回放</h2><svg id="map" aria-label="九龙道路仿真状态"></svg><p class="legend">红：低于20 km/h · 黄：20—40 · 绿：高于40 · 灰：无车。颜色为仿真值。</p><div class="controls"><button id="play">播放</button><input type="range" id="slider" min="0" value="0" aria-label="回放时刻"><span class="tag" id="phase">在线运行记录</span></div><p id="clock" class="small"></p></section>
<section class="panel"><h2>当前回放帧</h2><p id="frameStats"></p><div class="scroll"><table><thead><tr><th>检测站</th><th>仿真速度 km/h</th><th>车辆</th></tr></thead><tbody id="simRows"></tbody></table></div><p class="small" id="details">点击道路查看路段ID。停车车辆数不能当真实排队长度。</p></section></div>
<p class="footer" id="accounting"></p><p class="footer" id="run"></p>
<script id="payload" type="application/json">__PAYLOAD__</script><script>
const data=JSON.parse(document.getElementById('payload').textContent),$=id=>document.getElementById(id),f=(x,n=1)=>x==null?'—':Number(x).toFixed(n),fmt=t=>t?t.replace('T',' '):'无合格观测';
const m=data.metrics,c=data.collections,allDecisions=new Map(data.states.filter(s=>s.assimilation).map(s=>[s.assimilation.slot,s.assimilation]));
$('duration').textContent=f(data.run.paced_elapsed_seconds,1)+' 秒';$('acquisitions').textContent=c.filter(x=>x.success).length+' / '+c.filter(x=>!x.success&&x.source==='official_http').length+' / '+c.filter(x=>x.source==='injected_transport_outage').length;$('decisions').textContent=data.run.update_count+' / '+[...allDecisions.values()].filter(x=>x.constraints>0).length;$('safety').textContent=m.teleports+' / '+m.collision_vehicle_events;
$('accounting').textContent='计划合成车辆 '+m.planned+' 辆，已到达 '+m.arrived+' 辆；固定截止仍在网 '+m.running_at_cutoff+' 辆、待发 '+m.pending_at_cutoff+' 辆、已加载尚未尝试 '+(m.loaded_not_yet_attempted_at_cutoff||0)+' 辆。未做额外清空；未完成全部保留。本页不报告路况精度或治理收益。';$('run').textContent='运行ID：'+data.run_id+' · 物理起点 '+fmt(data.run.physical_origin)+' · '+(data.run.designed_dropout_after_seconds==null?'真实官方采集':'预设 '+data.run.designed_dropout_after_seconds+' 秒后注入断流');
const b=data.bounds,w=b[2]-b[0],h=b[3]-b[1],pad=Math.max(w,h)*.04,NS='http://www.w3.org/2000/svg',paths=new Map();$('map').setAttribute('viewBox',`${-pad} ${-pad} ${w+2*pad} ${h+2*pad}`);
for(const r of data.roads){const p=document.createElementNS(NS,'path');p.setAttribute('d',r.shape.map((pt,i)=>(i?'L':'M')+(pt[0]-b[0])+','+(b[3]-pt[1])).join(' '));p.setAttribute('class','road');p.onclick=()=>{$('details').textContent=r.name+' · '+r.id};$('map').appendChild(p);paths.set(r.id,p)}
let index=0,timer=null;$('slider').max=data.states.length-1;
function render(){const s=data.states[index],d=s.assimilation;$('slider').value=index;$('clock').textContent='物理时刻 '+fmt(s.physical_time)+' · 仿真 '+s.simulation_seconds+' s · 采样滞后 '+f(s.simulation_lag_seconds,3)+' s';$('frameStats').textContent='在网车辆 '+s.running+' 辆 · 待发车 '+s.pending+' 辆';$('simRows').replaceChildren();for(const r of s.edges){const p=paths.get(r.edge_id);if(p){p.style.stroke=r.vehicles===0?'#aebbc2':r.speed_kmh<20?'#bd584d':r.speed_kmh<40?'#c59d38':'#388b85';p.style.strokeWidth='4'}const row=document.createElement('tr');for(const v of [r.detector_id,r.vehicles?f(r.speed_kmh):'—',r.vehicles]){const td=document.createElement('td');td.textContent=v;row.appendChild(td)}$('simRows').appendChild(row)}if(d){$('updateStatus').textContent='第 '+(d.slot+1)+' 次计划 · 合格检测站 '+d.constraints+' 个 · '+(d.constraints?'更新后续需求':'证据不足，保持上次计划');$('updateTimes').textContent='计划时刻 '+fmt(d.decision_at)+'；实际决策 '+fmt(d.decided_at_wallclock)+'；已用观测结束 '+fmt(d.latest_used_observation_end)+'；已用观测可用 '+fmt(d.latest_used_available_at);$('updatePlan').textContent='本批 '+d.future_vehicles_added+' 辆 · 只安排 '+d.effective_begin_seconds+'—'+d.effective_end_seconds+' 仿真秒的未来出发；不删除或搬移旧车辆。'}const known=c.filter(x=>x.processed_elapsed_seconds<=s.wallclock_elapsed_seconds&&x.success),last=known[known.length-1];$('sourceTimes').textContent=last?'最近响应：观测结束 '+fmt(last.newest_observation_end)+'；HTTP接收 '+fmt(last.received_at)+'；不保证已经满足覆盖要求。':'本帧尚无已完成的HTTP响应；采集与SUMO分开运行。';}
$('slider').oninput=e=>{index=Number(e.target.value);render()};$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='播放';return}if(index>=data.states.length-1)index=0;$('play').textContent='暂停';timer=setInterval(()=>{index=Math.min(index+1,data.states.length-1);render();if(index===data.states.length-1){clearInterval(timer);timer=null;$('play').textContent='播放'}},400)};render();
</script></html>'''
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    (directory/'dashboard.html').write_text(template.replace('__PAYLOAD__',encoded),encoding='utf-8')
    return {'dashboard':str(directory/'dashboard.html'),'state_frames':len(states),'roads':len(roads),
        'mode':'offline_record_of_actual_wallclock_session_NOT_active_HTTP'}
