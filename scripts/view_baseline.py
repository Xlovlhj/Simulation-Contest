#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；实现与研究使用待队员复核。
"""Build a self-contained offline viewer from actual SUMO lane/FCD outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

try:
    from .replay_baidu import ROOT, ROUTE_LABELS, clean_text, inside_workspace, number, safe_json
except ImportError:
    from replay_baidu import ROOT, ROUTE_LABELS, clean_text, inside_workspace, number, safe_json


def read_run(directory):
    def source(name):
        path = inside_workspace(directory / name)
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("仿真输出文件越出运行目录")
        return path

    manifest = json.loads(source("run_manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads(source("metrics.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise ValueError("只浏览已完成的基准输出")
    if metrics.get("model_status") != "uncalibrated_technical_baseline":
        raise ValueError("该页面只用于未标定技术基准")
    hashes = {}
    for name in ("network.net.xml", "fcd.xml", "metrics.json", "summary.xml", "route_manifest.json"):
        path = source(name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = manifest.get("artifacts", {}).get(name)
        if not expected or digest != expected:
            raise ValueError("输入文件与运行 manifest 校验值不一致")
        hashes[name] = digest
    lanes = []
    for lane in ET.parse(source("network.net.xml")).getroot().iter("lane"):
        points = []
        for token in lane.get("shape", "").split():
            coordinates = [number(v) for v in token.split(",")]
            if len(coordinates) != 2 or None in coordinates:
                raise ValueError("车道几何包含无效坐标")
            points.append(coordinates)
        if len(points) >= 2:
            lanes.append({"id": clean_text(lane.get("id"), 100), "points": points})
    if not lanes:
        raise ValueError("未找到车道几何")
    series = {}
    for _, element in ET.iterparse(source("summary.xml"), events=("end",)):
        if element.tag == "step":
            t = number(element.get("time"))
            if t is not None:
                series[t] = {k: number(element.get(k)) for k in ("loaded", "inserted", "running", "waiting", "arrived", "halting", "collisions", "teleports")}
            element.clear()
    frames = []
    vehicle_ids = set()
    for _, element in ET.iterparse(source("fcd.xml"), events=("end",)):
        if element.tag != "timestep":
            continue
        t = number(element.get("time"))
        if t is None:
            raise ValueError("FCD 时间无效")
        vehicles = []
        for vehicle in element.findall("vehicle"):
            values = [number(vehicle.get(k)) for k in ("x", "y", "angle", "speed")]
            if None in values or values[3] < 0:
                raise ValueError("FCD 车辆位置或速度无效")
            identity = clean_text(vehicle.get("id"), 100)
            vehicle_ids.add(identity)
            vehicles.append({"id": identity, "x": values[0], "y": values[1], "angle": values[2], "speed_m_s": values[3]})
        if frames and t <= frames[-1]["time_s"]:
            raise ValueError("FCD 时刻必须递增")
        frames.append({"time_s": t, "vehicles": vehicles, "summary": series.get(t, {})})
        element.clear()
    if not frames:
        raise ValueError("FCD 中无快照")
    config = manifest.get("config", {})
    config = {key: number(config.get(key)) for key in ("simulation_duration_s", "step_length_s", "fcd_period_s", "lane_count", "speed_limit_kmh", "departure_duration_s", "departure_interval_s", "seed")}
    route_info = json.loads(source("route_manifest.json").read_text(encoding="utf-8"))
    names = {r.get("id"): ROUTE_LABELS.get(r.get("source_id"), clean_text(r.get("source_id"))) for r in route_info.get("routes", [])}
    per_route = [{"id": clean_text(rid), "label": names.get(rid, clean_text(rid)), **{k: number(row.get(k)) for k in ("planned", "departed", "arrived", "unfinished_on_network")}} for rid, row in metrics.get("per_route", {}).items()]
    metrics = {k: number(metrics.get(k)) for k in ("simulation_end_s", "planned", "departed", "arrived", "unfinished_total", "total_system_vehicle_time_s", "completed_travel_time_mean_s", "total_departure_wait_time_s")}
    return {"schema_version": 1, "run_id": clean_text(manifest.get("run_id")), "model_status": "uncalibrated_technical_baseline", "physical_time_mapping": None,
            "coordinates": "SUMO 本地米制坐标（源自 BD09 路线的局部近似投影）", "lanes": lanes, "frames": frames, "config": config,
            "metrics": metrics, "per_route": per_route, "input_sha256": hashes, "distinct_vehicle_ids_in_fcd": len(vehicle_ids),
            "frame_count": len(frames), "frame_intervals_s": sorted({round(b["time_s"] - a["time_s"], 6) for a, b in zip(frames, frames[1:])})}


PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>上海瑞金片区 · 未标定技术基准</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f7;color:#172d3b;font:15px/1.6 system-ui,-apple-system,"Microsoft YaHei",sans-serif}main{max-width:1450px;margin:auto;padding:28px}header{display:flex;justify-content:space-between;gap:20px}h1{font-size:29px;margin:3px 0}h2{font-size:17px;margin:0 0 12px}.eyebrow{font-size:12px;color:#3a6ba4;letter-spacing:2px;font-weight:700}.muted{font-size:13px;color:#637785}.badge{display:inline-block;background:#fff0d8;border:1px solid #edcf99;color:#88570c;border-radius:25px;padding:5px 13px;white-space:nowrap}.card{background:#fff;border:1px solid #dce4e9;border-radius:14px;padding:19px;margin-top:18px}.assumptions{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.assumptions span{padding:4px 10px;border-radius:6px;background:#f8f0df;color:#7b5c23;font-size:12px}.layout{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(300px,1fr);gap:18px}.map{padding:0;overflow:hidden}svg{display:block;width:100%;height:590px;background:#f7f9fb}.map-head{padding:16px 20px 0}.controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}button,select{font:inherit;padding:6px 12px;border:1px solid #c5d2df;border-radius:7px;background:white;color:#23445d}button{cursor:pointer}input[type=range]{accent-color:#3a6ba4;flex:1;min-width:180px}button:disabled{opacity:.5;cursor:default}a{color:#31699c}.clock{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.stat{padding:13px;background:#edf3f7;border-radius:10px}.stat strong{display:block;font-size:27px;line-height:1.3}.stat small{font-size:12px;color:#637785}table{font-size:13px;width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 6px;border-bottom:1px solid #e2e8ec}th{color:#637785}.legend{padding:12px 20px;font-size:12px;color:#637785;display:flex;gap:17px;flex-wrap:wrap}.dot{display:inline-block;width:9px;height:9px;margin-right:5px;border-radius:50%}.foot{color:#637785;font-size:12px;margin-top:18px}p{margin:7px 0}.small{font-size:12px}.scroll{overflow:auto;max-height:265px}ul{font-size:13px;padding-left:18px}li{margin:8px 0}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #80afce;outline-offset:3px}@media(max-width:900px){main{padding:16px}.layout{grid-template-columns:1fr}header{display:block}.badge{margin-top:10px}svg{height:440px}}
</style></head><body><main><header><div><div class="eyebrow">SHANGHAI / RUIJIN · SUMO TECHNICAL BASELINE</div><h1>未标定技术基准</h1><p class="muted">读取实际 SUMO 输出，逐帧展示仿真车辆的位置与速度。</p><a href="../replay/index.html">查看百度观测数据回放 →</a></div><div><span class="badge">假设需求 · 尚未完成现实验证</span></div></header>
<section class="card"><div class="controls"><button id="play">播放</button><button id="restart">回到起点</button><input id="timeSlider" type="range" min="0" value="0" aria-label="模型时间"><label for="rate" class="small">播放倍率</label><select id="rate"><option value="5">5×</option><option value="10">10×</option><option value="20" selected>20×</option><option value="50">50×</option></select><span id="clock" class="clock"></span></div><p id="frameNote" class="muted"></p><p class="muted">时间轴为模型秒，尚未映射现实采集时刻；车辆位置只取 FCD 已保存快照，不在两帧之间插值。</p></section>
<div class="layout"><section class="card map"><div class="map-head"><h2>真实仿真输出回放</h2><p class="muted">路网使用本次 network.net.xml 的车道几何，车辆位置与朝向使用 fcd.xml。</p></div><svg id="map" viewBox="0 0 950 590" role="img" aria-label="SUMO 实际输出的路网与车辆快照"></svg><div class="legend"><span><i class="dot" style="background:#bd633d"></i>低于 10 km/h</span><span><i class="dot" style="background:#3b81bb"></i>10—20 km/h</span><span><i class="dot" style="background:#268273"></i>至少 20 km/h</span></div></section>
<section class="card"><h2>当前帧 · 仿真状态</h2><div class="stats"><div class="stat"><strong id="active"></strong><small>FCD 在网车辆</small></div><div class="stat"><strong id="meanSpeed"></strong><small>本帧平均速度 km/h</small></div><div class="stat"><strong id="arrived"></strong><small>累计到达（summary）</small></div><div class="stat"><strong id="waiting"></strong><small>等待入网（summary）</small></div></div><h2 style="margin-top:22px">本次运行的假设</h2><div class="assumptions"><span id="laneAssumption"></span><span id="speedAssumption"></span><span>无实际信号配时</span><span>未标定跟驰参数</span></div><p id="demand" class="muted"></p><ul><li>道路来自百度规划路线，模型仅允许已保留的相邻边连接；完整路网拓扑仍需核验。</li><li>RJ_YJ 为研究设定的医院外围道路接驳点，急救入口尚未验证。</li><li>车辆是仿真需求，不代表实际道路中的车辆；本页不把技术运行结果称为治理效果。</li></ul></section></div>
<section class="card"><h2>已完成运行 · 汇总</h2><p id="totals"></p><p id="travel" class="muted"></p><div class="scroll"><table><thead><tr><th>路线</th><th>计划车辆</th><th>实际出发</th><th>完成</th><th>仍在路网</th></tr></thead><tbody id="routes"></tbody></table></div></section><p class="foot" id="provenance"></p>
</main><script id="baseline-data" type="application/json">__DATA__</script><script>
"use strict";
const data=JSON.parse(document.getElementById('baseline-data').textContent),frames=data.frames,el=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';let index=0,timer=null;
const text=(n,v)=>{n.textContent=v==null?'':String(v)};const num=(v,d=0)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d}):'';
function node(tag,attrs={},label){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(label!=null)text(n,label);return n}
const svg=el('map');let minx=Infinity,maxx=-Infinity,miny=Infinity,maxy=-Infinity;for(const lane of data.lanes)for(const p of lane.points){minx=Math.min(minx,p[0]);maxx=Math.max(maxx,p[0]);miny=Math.min(miny,p[1]);maxy=Math.max(maxy,p[1])}const scale=Math.min(820/Math.max(1,maxx-minx),460/Math.max(1,maxy-miny)),cx=(minx+maxx)/2,cy=(miny+maxy)/2,xy=(x,y)=>[475+(x-cx)*scale,295-(y-cy)*scale];
for(let i=0;i<7;i++){svg.append(node('line',{x1:60+i*135,y1:40,x2:60+i*135,y2:550,stroke:'#e8eef2'}));svg.append(node('line',{x1:50,y1:50+i*82,x2:900,y2:50+i*82,stroke:'#e8eef2'}))}
for(const lane of data.lanes){const p=node('polyline',{points:lane.points.map(p=>xy(...p).join(',')).join(' '),fill:'none',stroke:'#9cabb7','stroke-width':3.5,'stroke-linecap':'round','stroke-linejoin':'round'});p.append(node('title',{},lane.id));svg.append(p)}
const vehicles=node('g');svg.append(vehicles);svg.append(node('path',{d:'M 875 75 L 875 35 M 868 46 L 875 35 L 882 46',stroke:'#637785',fill:'none','stroke-width':2}));svg.append(node('text',{x:875,y:27,'text-anchor':'middle','font-size':12,fill:'#637785'},'北 N'));
const bar=200*scale;svg.append(node('path',{d:`M 65 543 v 7 h ${bar} v -7`,stroke:'#637785',fill:'none','stroke-width':2}));svg.append(node('text',{x:65,y:534,'font-size':12,fill:'#637785'},'200 m（模型坐标）'));
function render(){const f=frames[index];el('timeSlider').value=index;text(el('clock'),'t = '+num(f.time_s)+' s');text(el('frameNote'),'第 '+(index+1)+' / '+frames.length+' 帧 · FCD 间隔 '+data.frame_intervals_s.join(' / ')+' s · 运行终点 '+num(data.metrics.simulation_end_s)+' s');text(el('active'),f.vehicles.length);text(el('meanSpeed'),f.vehicles.length?num(f.vehicles.reduce((a,v)=>a+v.speed_m_s,0)/f.vehicles.length*3.6,1):'');text(el('arrived'),num(f.summary.arrived));text(el('waiting'),num(f.summary.waiting));vehicles.replaceChildren();for(const v of f.vehicles){const p=xy(v.x,v.y),speed=v.speed_m_s*3.6,color=speed<10?'#bd633d':speed<20?'#3b81bb':'#268273';const n=node('path',{d:'M 0 -5 L 3.5 4 L -3.5 4 Z',transform:`translate(${p[0]} ${p[1]}) rotate(${v.angle})`,fill:color,stroke:'#fff','stroke-width':.7});n.append(node('title',{},v.id+' · '+num(speed,2)+' km/h'));vehicles.append(n)}}
function stop(){clearTimeout(timer);timer=null;text(el('play'),'播放')}
function schedule(){if(index>=frames.length-1){stop();return}const delay=(frames[index+1].time_s-frames[index].time_s)*1000/Number(el('rate').value);timer=setTimeout(()=>{index++;render();schedule()},delay)}
el('play').onclick=()=>{if(timer){stop();return}if(index>=frames.length-1)index=0;text(el('play'),'暂停');render();schedule()};el('restart').onclick=()=>{stop();index=0;render()};el('timeSlider').max=frames.length-1;el('timeSlider').oninput=()=>{stop();index=Number(el('timeSlider').value);render()};el('rate').onchange=()=>{if(timer){clearTimeout(timer);schedule()}};
text(el('laneAssumption'),'假设 '+num(data.config.lane_count)+' 车道');text(el('speedAssumption'),'假设限速 '+num(data.config.speed_limit_kmh)+' km/h');text(el('demand'),'前 '+num(data.config.departure_duration_s)+' s，每条路线每 '+num(data.config.departure_interval_s)+' s 安排 1 辆车。需求与车道、速度参数均为模型设定。');text(el('totals'),'计划 '+num(data.metrics.planned)+' 辆 · 出发 '+num(data.metrics.departed)+' 辆 · 完成 '+num(data.metrics.arrived)+' 辆 · 未完成 '+num(data.metrics.unfinished_total)+' 辆');text(el('travel'),'已完成车辆平均在网时间 '+num(data.metrics.completed_travel_time_mean_s,2)+' s；总系统车辆时间 '+num(data.metrics.total_system_vehicle_time_s)+' 车·秒（含入网前等待）。');
for(const r of data.per_route){const tr=document.createElement('tr');for(const v of [r.label,r.planned,r.departed,r.arrived,r.unfinished_on_network]){const td=document.createElement('td');text(td,v);tr.append(td)}el('routes').append(tr)}text(el('provenance'),'运行 '+data.run_id+' · '+data.lanes.length+' 条车道几何 · '+data.frame_count+' 帧实际 FCD 输出 · '+data.distinct_vehicle_ids_in_fcd+' 个车辆 ID · SHA-256 与运行 manifest 一致 · 未建立现实物理时间映射');render();
</script></body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description="从已完成的 SUMO 技术基准输出生成独立离线动画页；不运行仿真、不联网、不读取密钥。")
    parser.add_argument("--run-dir", type=Path, default=ROOT / "outputs/baseline/natural_surface_baseline_v1")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/baseline/index.html")
    args = parser.parse_args(argv)
    try:
        directory, output = inside_workspace(args.run_dir), inside_workspace(args.output)
        if output.is_relative_to(directory):
            raise ValueError("页面输出不能改写源运行目录")
        data = read_run(directory)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(PAGE.replace("__DATA__", safe_json(data)), encoding="utf-8")
    except (OSError, ValueError, TypeError, ET.ParseError):
        print("生成失败：请检查运行是否完成、输入格式及 manifest 校验值。", file=sys.stderr)
        return 1
    print(f"已生成 {output.relative_to(ROOT)}：{len(data['lanes'])} 条车道、{len(data['frames'])} 帧 FCD、{data['distinct_vehicle_ids_in_fcd']} 个车辆 ID。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
