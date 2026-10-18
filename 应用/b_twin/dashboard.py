"""Export a self-contained review page for a completed TraCI diagnostic."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from .paths import APP
import sumolib


def export_dashboard(run_id):
    if not run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid run ID")
    directory = APP / "results" / run_id
    states_file = directory / "states_60s.jsonl"
    if not states_file.exists():
        raise FileNotFoundError("Run with --backend traci to create state replay")
    states = [json.loads(line) for line in states_file.read_text(encoding="utf-8").splitlines()]
    net = sumolib.net.readNet(str(directory / "network.net.xml"))
    roads = [{"id": e.getID(), "name": e.getName(), "lanes": e.getLaneNumber(), "shape": e.getShape()}
             for e in net.getEdges()]
    xs = [p[0] for e in roads for p in e["shape"]]
    ys = [p[1] for e in roads for p in e["shape"]]
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    live_file = APP / "runtime/live/state.json"
    live = json.loads(live_file.read_text(encoding="utf-8")) if live_file.exists() else {"stations": {}}
    content = {"run_id": run_id, "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "states": states, "roads": roads, "bounds": bounds,
        "metrics": json.loads((directory / "metrics.json").read_text(encoding="utf-8")),
        "run": json.loads((directory / "run.json").read_text(encoding="utf-8")), "live": live}
    emergency_definition = directory / "emergency_network_definition.json"
    if emergency_definition.exists():
        content["emergency_network"] = json.loads(emergency_definition.read_text(encoding="utf-8"))
    template = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>B题 · 运行与观测</title>
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#193340;background:#eef3f5}*{box-sizing:border-box}body{margin:0;padding:28px;max-width:1540px;margin:auto}h1{font-size:25px;margin:0 0 8px}p{line-height:1.6;margin:8px 0;color:#506674}.tag{font-size:12px;padding:5px 9px;border-radius:20px;background:#e5eef2;color:#335565;display:inline-block}.grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:20px;margin-top:20px}.panel{min-width:0;background:white;border:1px solid #d7e1e6;border-radius:12px;padding:20px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}.metric{padding:16px;background:white;border:1px solid #d7e1e6;border-radius:10px}.number{font-size:25px;font-weight:650;margin-top:6px}.label{font-size:12px;color:#5e7280}h2{font-size:17px;margin:0 0 8px}#map{width:100%;height:510px;background:#f6f9fa;border-radius:8px}.road{fill:none;stroke:#c6d4dc;stroke-width:1.5;vector-effect:non-scaling-stroke;stroke-linecap:round}.monitored{stroke-width:4}.controls{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin-top:16px}button{background:#174d62;color:white;border:none;padding:8px 18px;border-radius:6px;cursor:pointer}input{flex:1;min-width:50px}.clock{font-variant-numeric:tabular-nums;font-size:14px}.scroll{max-height:370px;overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:9px 6px;text-align:left;border-bottom:1px solid #e7eef1}th{position:sticky;top:0;background:white}td{font-variant-numeric:tabular-nums}.legend{font-size:12px;display:flex;flex-wrap:wrap;gap:14px;margin:12px 0}.dot{display:inline-block;width:9px;height:9px;border-radius:100%;margin-right:4px}.note{font-size:12px}.notice{border-left:3px solid #c59637;padding-left:12px}.meta{font-size:11px;color:#6b7e87;word-break:break-all}.status{color:#996d23}#details{font-size:12px;min-height:32px;margin-top:10px}@media(max-width:950px){body{padding:16px}.grid{grid-template-columns:minmax(0,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}#map{height:400px}}
</style>
<header><span class="tag" id="runType">B题开发 · 基准诊断</span><h1>九龙交通：仿真状态与官方观测</h1><p>公主道—康庄道及下游道路 · 历史模型状态回放 · UTC+8</p></header>
<div class="notice"><p><strong>真实高峰基准尚未验证通过。</strong><span id="scope"></span></p><p id="scenario"></p></div>
<section class="panel" id="emergencyPanel" hidden style="margin-top:18px"><h2>设计救援任务</h2><p id="emergencyEndpoint"></p><p id="emergencyStatus" aria-live="polite"></p><p id="guardRule" class="note" hidden>稀疏让行只请求最近前车，先检查空位与后续路线；同车同路段最多一次。4秒位置确认不等于因果收益，离开路段记无法判断。</p><p class="note">虚线为固定任务路线，红点为救援代理。车辆保留默认通行与安全检查；让行是请求，绿灯协同只延长已有绿灯。该回放不是实际事故或实时调度。</p></section>
<section class="panel" id="assimilationPanel" hidden style="margin-top:18px"><h2>观测如何更新后续车辆</h2><p id="updateStatus" aria-live="polite"></p><p id="updateTimes" class="note"></p><p id="updatePlan" class="note"></p><p class="note">归档快照时间是历史可用时刻代理；仅增量更新遵守该时序，均值先验仍含同日信息。这里只改变下一批需求，旧车继续行驶；不是持续在线的实时孪生，也不是治理收益对照。</p></section>
<div class="metrics"><div class="metric"><div class="label">评估时段流量 WMAPE</div><div class="number" id="flow"></div></div><div class="metric"><div class="label">速度 MAE</div><div class="number" id="speed"></div></div><div class="metric"><div class="label">评估时段末未发车</div><div class="number" id="pending"></div></div><div class="metric"><div class="label">瞬移 / 碰撞</div><div class="number" id="safety"></div></div></div><p id="accounting" class="note"></p>
<div class="grid"><section class="panel"><h2>历史仿真状态回放</h2><p class="note">地图使用SUMO道路几何；彩色路段为有检测器对应的路段，颜色表示当步仿真速度。</p><svg id="map" aria-label="交通路网状态"></svg><div class="legend"><span><i class="dot" style="background:#bd584d"></i>低于20 km/h</span><span><i class="dot" style="background:#c59d38"></i>20—40 km/h</span><span><i class="dot" style="background:#388b85"></i>高于40 km/h</span><span><i class="dot" style="background:#aebbc2"></i>无车</span></div><div class="controls"><button id="play">播放</button><input type="range" id="slider" min="0" value="0"><span id="phase" class="tag"></span></div><p id="clock" class="clock"></p><div id="details">点击彩色路段查看检测器、坐标和状态。</div></section>
<section class="panel"><h2>当前回放帧</h2><p id="frameStats"></p><div class="scroll"><table><thead><tr><th>检测点</th><th>速度 km/h</th><th>车辆</th><th>停车</th></tr></thead><tbody id="simRows"></tbody></table></div><p class="note">速度为路段当步均值；表中的停车车辆数不是排队长度。无车路段显示“—”。</p><p class="meta" id="run"></p></section>
<section class="panel" style="grid-column:1/-1"><h2>最近取得的官方观测快照</h2><p id="liveMeta"></p><p class="note">这是页面导出时保存的观测，页面不会自动请求接口。数据年龄随打开时间增长；10分钟陈旧标记是工程展示规则。30秒样本小时率不等于15分钟估计流量。</p><div class="scroll"><table><thead><tr><th>检测点</th><th>观测结束时间</th><th>当前年龄</th><th>速度 km/h</th><th>30秒计数</th><th>映射状态</th></tr></thead><tbody id="liveRows"></tbody></table></div></section></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('payload').textContent), $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const fixed=(v,d=1)=>v==null||!Number.isFinite(v)?'—':v.toFixed(d);
const m=data.metrics.primary_metrics||data.metrics,a=data.metrics.accounting;
$('flow').textContent=fixed(m.flow_wmape_pct,2)+'%';$('speed').textContent=fixed(m.speed_mae_kmh,2)+' km/h';$('pending').textContent=(a?a.evaluation_end.pending:data.metrics.vehicles.waiting)+' 辆';$('safety').textContent=data.metrics.teleports+' / '+data.metrics.collisions;$('run').textContent='运行ID：'+data.run_id+'；需求：'+(data.run.demand_mode||'legacy')+'；页面导出：'+data.created_at;
const bg=data.run.background_vph_scenario||0,supply=data.run.residual_supply_vph_scenario||0;
const policy=data.run.meter_policy;
const emergency=data.run.emergency_policy;
const assimilation=data.run.assimilation_policy;
$('runType').textContent=emergency?'设计情景 · 应急协同实验':policy?'设计情景 · 入口协调实验':'B题开发 · 基准诊断';
$('scope').textContent=emergency?'本次策略：'+({baseline:'无专门应急协同',yield:'周边车辆让行',combined:'让行＋既有绿灯延长',guarded:'检查空位与路线的稀疏让行',guarded_green:'稀疏让行＋按到达估计延绿'})[emergency]+'。保留反馈入口控制；反馈来自模拟传感器，官方观测尚未同化。救护入口与实际应急效果未验证。':policy?'本次策略：'+({free:'同设施全绿参照',fixed:'固定放行',feedback:'占有率反馈＋入口溢出保护'})[policy]+'。反馈来自模拟传感器，官方观测尚未同化；真实场景效果与应急协同仍待验证。':'此页展示一次已完成运行与官方观测快照；本次没有入口协调，观测尚未同化。';
if(data.run.guard_protocol)$('guardRule').hidden=false;
if(assimilation){$('assimilationPanel').hidden=false;$('runType').textContent='历史观测 · 未来需求更新';$('scope').textContent='每5分钟按已可用观测更新下一批车辆。观测年龄、覆盖率和映射不合格时保留原计划；当前没有施加治理控制或观测速度。';}
$('scenario').textContent='网络：'+(data.run.network_version||'历史版本')+'；背景汇入总量 '+fixed(bg,0)+' veh/h（设计假设）；'+(supply?'名义外部供给 '+fixed(supply,0)+' veh/h（数值边界，实际模拟出流另行统计；不代表真实信号灯或治理方案）。':'自由出口，未施加外部供给约束。')+' 核心流量与速度指标使用原核心检测站的合格窗口。';
$('accounting').textContent=a?'评估时段末仍在网内 '+a.evaluation_end.running+' 辆，另有 '+(a.evaluation_end.loaded_not_yet_attempted||0)+' 辆尚未尝试发车；随后清空 '+(data.run.clearance_seconds||0)+' 秒，剩余待发 '+a.after_clearance.pending+' 辆、在网 '+a.after_clearance.running+' 辆、尚未尝试 '+(a.after_clearance.loaded_not_yet_attempted||0)+' 辆。清空阶段不计入观测误差。'+(data.metrics.trip_accounting?' 全部计划车辆 '+data.metrics.trip_accounting.planned+' 辆，平均经历时间下界 '+fixed(data.metrics.trip_accounting.mean_experienced_time_lower_bound_s,1)+' 秒；未完成行程保留在核算中。':''):'该历史运行尚未设置清空阶段。';
const b=data.bounds,w=b[2]-b[0],h=b[3]-b[1],pad=Math.max(w,h)*.035;
$('map').setAttribute('viewBox',`${-pad} ${-pad} ${w+2*pad} ${h+2*pad}`);
const paths=new Map();for(const road of data.roads){const p=document.createElementNS(NS,'path');p.setAttribute('d',road.shape.map((pt,i)=>(i?'L':'M')+(pt[0]-b[0])+','+(b[3]-pt[1])).join(' '));p.setAttribute('class','road');const title=document.createElementNS(NS,'title');title.textContent=road.name+' · '+road.id;p.appendChild(title);$('map').appendChild(p);paths.set(road.id,p)}
let emergencyMarker=null;if(emergency){$('emergencyPanel').hidden=false;$('emergencyEndpoint').textContent='08:35由公主道出发 → 伊利沙伯医院路公共道路末端。未核验救护入口，不计医院内部或病人交接。';for(const edge of data.run.emergency_scenario.fixed_route_edges){const p=paths.get(edge);if(p){p.style.stroke='#8b69ab';p.style.strokeDasharray='7 4';p.style.strokeWidth='3';}}emergencyMarker=document.createElementNS(NS,'circle');emergencyMarker.id='emergencyMarker';emergencyMarker.setAttribute('r',Math.max(w,h)*.008);emergencyMarker.setAttribute('fill','#c14b43');emergencyMarker.setAttribute('stroke','white');emergencyMarker.setAttribute('stroke-width','3');$('map').appendChild(emergencyMarker);}
function renderEmergency(s){if(!emergency)return;const e=s.emergency;if(!e)return;const status=({not_dispatched:'尚未调度',pending:'等待进入路网',running:'正在行驶',arrived:'已到公共道路终点'})[e.status]||e.status;$('emergencyStatus').textContent=status+' · 单车道限速事件'+(e.incident_active?'进行中':'未生效 / 已结束')+' · 累计让行请求 '+e.yield_requests+' 次 · 绿灯延长 '+e.green_extensions+' 次'+(data.run.guard_protocol?' · 4秒位置确认 '+e.observed_requested_lane_responses+' 次':'');emergencyMarker.style.display=e.status==='running'?'':'none';if(e.status==='running'){emergencyMarker.setAttribute('cx',e.x-b[0]);emergencyMarker.setAttribute('cy',b[3]-e.y);}}
let index=0,timer=null,current=new Map();$('slider').max=data.states.length-1;
function render(){const s=data.states[index];current=new Map(s.edges.map(e=>[e.edge_id,e]));$('slider').value=index;$('phase').textContent=({warmup:'初始化 / 预热',evaluation:'正式评估',clearance:'清空 / 不计入观测误差'})[s.phase]||s.phase;$('clock').textContent='物理时刻 '+s.physical_time.replace('T',' ')+' · 仿真 '+s.simulation_seconds+' s';$('frameStats').textContent='在网车辆 '+s.running+' 辆 · 待发车 '+s.pending+' 辆';$('simRows').replaceChildren();for(const e of s.edges){const color=e.vehicles===0?'#aebbc2':e.speed_kmh<20?'#bd584d':e.speed_kmh<40?'#c59d38':'#388b85';const path=paths.get(e.edge_id);if(path){path.classList.add('monitored');path.style.stroke=color;path.onclick=()=>{$('details').textContent=`${e.detector_id} · 路段 ${e.edge_id} · 经度 ${e.longitude.toFixed(6)}，纬度 ${e.latitude.toFixed(6)} · ${e.mapping_requires_review?'映射待复核':'自动交叉核对，未现场验证'}`}}const row=document.createElement('tr');for(const v of [e.detector_id,e.vehicles?fixed(e.speed_kmh):'—',e.vehicles,e.halting_vehicles]){const td=document.createElement('td');td.textContent=v;row.appendChild(td)}$('simRows').appendChild(row)}}
function renderUpdate(s){if(!assimilation||!s.assimilation)return;const d=s.assimilation,fmt=t=>t?t.replace('T',' '):'无合格观测';$('updateStatus').textContent='第 '+(d.slot+1)+' 次计划 · 合格检测站 '+d.constraints+' 个 · '+(d.action==='frozen_prior'?'保持冻结先验':d.constraints?'更新后续需求':'证据不足，保持上次计划');$('updateTimes').textContent='观测结束 '+fmt(d.latest_used_observation_end)+'；可用时刻 '+fmt(d.latest_used_available_at)+'；决策时刻 '+fmt(d.decision_at);$('updatePlan').textContent='本批 '+d.future_vehicles_added+' 辆 · 发车区间 '+d.effective_begin_seconds+'—'+d.effective_end_seconds+' 仿真秒 · 边界需求 '+fixed(d.admitted_boundary_vph,0)+' veh/h。'+(s.phase==='clearance'?'已进入清空期，不再生成新车辆。':'');}
const renderBase=render;render=()=>{renderBase();renderEmergency(data.states[index]);renderUpdate(data.states[index]);};$('slider').oninput=e=>{index=Number(e.target.value);render()};$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='播放';return}if(index===data.states.length-1)index=0;timer=setInterval(()=>{render();if(index>=data.states.length-1){clearInterval(timer);timer=null;$('play').textContent='播放'}else index++},350);$('play').textContent='暂停'};render();
function renderLive(){$('liveRows').replaceChildren();const stations=Object.values(data.live.stations);$('liveMeta').textContent=stations.length?'共 '+stations.length+' 个检测点；取得时间 '+data.live.checked_at:'尚无成功取得的观测，请运行 collect-live 后重新导出页面。';for(const e of stations.sort((a,b)=>a.detector_id.localeCompare(b.detector_id))){const age=(Date.now()-Date.parse(e.observation_end))/1000,row=document.createElement('tr');for(const v of [e.detector_id,e.observation_end.replace('T',' '),fixed(age/60)+'分钟'+(age>600?' · 已陈旧':''),fixed(e.speed_kmh),e.observed_count_30s,e.mapping_requires_review?'待复核':'自动核对']){const td=document.createElement('td');td.textContent=v;row.appendChild(td)}$('liveRows').appendChild(row)}}renderLive();setInterval(renderLive,30000);
</script></html>'''
    encoded = json.dumps(content, ensure_ascii=False).replace("<", "\\u003c")
    target = directory / "dashboard.html"
    target.write_text(template.replace("__PAYLOAD__", encoded), encoding="utf-8")
    return {"dashboard": str(target), "state_frames": len(states), "roads": len(roads),
        "observed_stations_in_export": len(live["stations"]),
        "mode": "offline_historical_replay_and_saved_observation_NOT_wallclock_live_control"}
