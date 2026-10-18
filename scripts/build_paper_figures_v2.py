#!/usr/bin/env python3
# AI-assisted: OpenAI Codex / OpenAI, 2026-10-18; team review required.
"""Draw paper figures only from frozen V2 inputs, without changing scientific inputs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/paper_v2/figures"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
INK = "#172633"
MUTED = "#51606d"
GRID = "#d7dfe5"
BLUE = "#1d608e"
ORANGE = "#b46218"
RED = "#ad3541"
PURPLE = "#694783"
SOURCES: dict[str, str] = {}
OD_NAMES = {"north_to_south": "北→南", "south_to_north": "南→北", "west_to_hospital_road": "西→医院外围", "east_to_hospital_road": "东→医院外围"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path):
    p = ROOT / path
    SOURCES[path] = sha(p)
    return p


def load(path):
    return json.loads(source(path).read_text(encoding="utf-8"))


def font(size=36):
    return ImageFont.truetype(FONT, size)


def canvas(title, subtitle, height=1100):
    im = Image.new("RGB", (1800, height), "white")
    dr = ImageDraw.Draw(im)
    dr.text((48, 20), title, font=font(45), fill=INK)
    dr.text((48, 85), subtitle, font=font(32), fill=MUTED)
    return im, dr


def text_center(dr, x, y, value, size=34, fill=INK):
    dr.text((x, y), str(value), anchor="mt", font=font(size), fill=fill)


def label(dr, xy, value, size=34, fill=INK):
    dr.text(xy, str(value), font=font(size), fill=fill)


def save(im, name, values, note):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    im.save(p, dpi=(300, 300))
    metadata = {"figure": str(p.relative_to(ROOT)), "figure_sha256": sha(p), "pixel_dimensions": list(im.size), "source_sha256": dict(sorted(SOURCES.items())), "values": values, "interpretation": note, "builder_sha256": sha(Path(__file__))}
    p.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.clear()
    return metadata


def network():
    catalog = load("data/processed/baidu/route_catalog_v2.json")
    basis = load("research/parameter_audit/scenario_basis_v2.json")
    explanation = load("outputs/governance_v2/route_choice_explanation.json")
    comparison = explanation["profiles"][0]["comparisons"][0]
    common = comparison["shared_event_edges"]
    avoidable = comparison["avoidable_default_event_edges"]
    event = basis["event_geometry"]["edge_ids"]
    assert set(event) == set(common) | set(avoidable)
    im, dr = canvas("观测有向路网与完整事件范围", "上海瑞金医院外围 · BD09局部投影 · 灰线为观测子图，未宣称完整街区路网", 1350)
    nodes = {r["id"]: r["xy_m"] for r in catalog["network"]["nodes"]}
    edges = {r["id"]: r for r in catalog["network"]["edges"]}
    xs, ys = [p[0] for p in nodes.values()], [p[1] for p in nodes.values()]
    sc = min(1120/(max(xs)-min(xs)), 1000/(max(ys)-min(ys)))
    cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    def point(p):
        return 650 + (p[0]-cx)*sc, 675 - (p[1]-cy)*sc
    def lines(ids, color, width):
        for identity in ids:
            edge = edges[identity]
            dr.line([point(nodes[edge["from"]]), point(nodes[edge["to"]])], fill=color, width=width)
    default = next(d for d in catalog["demand_ods"] if d["id"] == "west_to_hospital_road")["baseline_edge_ids"]
    alternate = next(r for r in catalog["route_candidates"] if r["demand_od_id"] == "west_to_hospital_road" and r["edge_ids"] != default)["edge_ids"]
    lines(edges, "#c7ced4", 4)
    lines(alternate, BLUE, 9)
    lines(default, ORANGE, 6)
    for ids, color in ((alternate, BLUE), (default, ORANGE)):
        for i, identity in enumerate(ids):
            edge = edges[identity]
            a, b = point(nodes[edge["from"]]), point(nodes[edge["to"]])
            dx, dy = b[0]-a[0], b[1]-a[1]
            length = math.hypot(dx, dy)
            if i % 4 or length < 25:
                continue
            ux, uy, x, y = dx/length, dy/length, (a[0]+b[0])/2, (a[1]+b[1])/2
            dr.polygon([(x+13*ux,y+13*uy),(x-9*ux+9*uy,y-9*uy-9*ux),(x-9*ux-9*uy,y-9*uy+9*ux)], fill=color)
    lines(common, PURPLE, 14)
    lines(avoidable, RED, 14)
    end = point(nodes[edges[default[-1]]["to"]])
    dr.ellipse((end[0]-11,end[1]-11,end[0]+11,end[1]+11), fill=INK)
    label(dr, (end[0]+21,end[1]-14), "医院外围接驳点", 33)
    label(dr, (end[0]+21,end[1]+30), "RJ_YJ（非已核验院内入口）", 27, MUTED)
    # A compact event decomposition makes the complete shared segment unmistakable.
    dr.rounded_rectangle((1260, 200, 1755, 1055), radius=15, fill="#f4f6f8", outline=GRID, width=2)
    label(dr, (1290,225), "完整北向事件段", 37)
    label(dr, (1290,285), f"{len(event)}条有向边", 35)
    label(dr, (1290,335), f"{basis['event_geometry']['model_length_m']:.3f} m（模型）", 35)
    label(dr, (1290,385), f"{basis['event_geometry']['platform_distance_m']:.0f} m（平台步骤）", 35)
    for y, color, heading, value in ((495,PURPLE,"两路线共同必经",comparison["shared_event_length_m"]),(675,RED,"默认路线可绕段",comparison["avoidable_default_event_length_m"])):
        dr.line((1290,y+23,1355,y+23), fill=color, width=14)
        label(dr, (1370,y), heading, 31)
        label(dr, (1290,y+57), f"{value:.3f} m", 38, color)
    label(dr, (1290,870), "备选全程额外增加", 32)
    label(dr, (1290,925), f"{comparison['alternative_additional_full_route_length_m']:.3f} m", 38, BLUE)
    dr.line((1180,260,1180,180), fill=INK, width=4)
    dr.polygon([(1180,164),(1170,188),(1190,188)], fill=INK)
    label(dr, (1140,125), "北 N", 32)
    dr.line((75,1110,75+200*sc,1110), fill=INK, width=4)
    label(dr, (75,1124), "200 m（模型坐标）", 29)
    for i,(color, txt) in enumerate(((ORANGE,"西侧默认路线"),(BLUE,"经思南路备选"),(PURPLE,"事件：共同段"),(RED,"事件：可绕段"))):
        x = 70 + (i%2)*610
        y = 1220 + (i//2)*58
        dr.line((x,y+19,x+70,y+19), fill=color, width=10)
        label(dr, (x+88,y-5), txt, 34)
    return save(im,"network_v2.png",{"external_edges":len(edges),"total_connections":len(catalog['network']['connections']),"allowed_turns":sum(c['eligible_for_policy_path'] for c in catalog['network']['connections']),"event_edge_ids":event,"event_model_length_m":basis['event_geometry']['model_length_m'],"shared_event_length_m":comparison['shared_event_length_m'],"avoidable_event_length_m":comparison['avoidable_default_event_length_m'],"alternative_extra_distance_m":comparison['alternative_additional_full_route_length_m']},"Directed observed subgraph. Endpoint is a research rendezvous, not a verified hospital entrance. Event covers all 19 observed step edges.")


def grouped_bars(dr, box, groups, series, maximum, ticks, ylabel):
    left, top, width, height = box
    label(dr, (left,top-65), ylabel, 35)
    for tick in ticks:
        y = top+height-height*tick/maximum
        dr.line((left,y,left+width,y), fill=GRID, width=2)
        dr.text((left-16,y),str(tick),anchor="rm",font=font(30),fill=MUTED)
    for i, (key, title) in enumerate(groups):
        cx = left+width*(i+.5)/len(groups)
        bw = width/len(groups)*.29
        label_top = top+height-height*max(s[1][key] for s in series)/maximum-45
        for j, (color, values) in enumerate(series):
            value = values[key]
            x = cx + (j-.5)*bw*1.3
            y = top+height-height*value/maximum
            dr.rectangle((x-bw/2,y,x+bw/2,top+height),fill=color)
            label_y = label_top-42 if j==0 and width<700 else label_top if width<700 else y-44
            text_center(dr,x,label_y,f"{value:.1f}",31,color)
        for j, row in enumerate(title.split("\n")):
            text_center(dr,cx,top+height+20+j*43,row,31)


def calibration():
    fit = load("outputs/identification_v2/calibration/fit.json")
    evaluation = load("outputs/identification_v2/calibration/evaluation.json")
    training = load("data/processed/identification_v2/training_summary.json")
    chosen = fit['selected_for_time_prediction']
    if isinstance(chosen,dict):
        chosen = chosen.get('profile_id',chosen.get('id'))
    assert chosen == 'headway_30', chosen
    selected = next(p for p in fit['profiles'] if p['id']==chosen)
    predictions = selected['best']['predictions_s']
    observed = {k:v['eta_s']['median'] for k,v in training['route_eta'].items()}
    im,dr = canvas("直接SUMO时间与观测约束、时间留出误差", "h = 30 s由训练目标选择；蓝色为百度训练中位参考，橙色为直接SUMO输出", 1080)
    groups = [(k,OD_NAMES[k].replace('医院外围','医院\n外围')) for k in OD_NAMES]
    grouped_bars(dr,(105,250,900,570),groups,[(BLUE,observed),(ORANGE,predictions)],850,[0,200,400,600,800],"(a) 四OD时间（秒）")
    phases = [('training','训练\n48条'),('same_day_exploratory','同日探索\n16条'),('cross_day_holdout','跨日留出\n96条')]
    med = {k:evaluation['training_median_reference'][k]['mae_s'] for k,_ in phases}
    direct = {k:evaluation['profiles'][chosen][k]['mae_s'] for k,_ in phases}
    grouped_bars(dr,(1160,250,570,570),phases,[(BLUE,med),(ORANGE,direct)],240,[0,60,120,180,240],"(b) 逐条ETA误差 MAE（秒）")
    dr.line((1090,195,1090,900),fill=GRID,width=2)
    for x,color,txt in ((100,BLUE,"百度训练中位 / 中位预测基准"),(920,ORANGE,"内部标定后的直接SUMO时间")):
        dr.rectangle((x,956,x+45,988),fill=color)
        label(dr,(x+63,948),txt,33)
    label(dr,(100,1015),"跨日为回顾性时间留出；训练中位接近不等于跨时段预测有效。",32,MUTED)
    return save(im,"calibration_v2.png",{"selected_profile":chosen,"observed_training_median_s":observed,"direct_sumo_s":predictions,"direct_mae_s":direct,"median_baseline_mae_s":med},"Panel a compares medians with direct SUMO probe means; panel b compares frozen predictors against individual snapshots. These are provider ETA estimates, not measured individual trips. No affine output correction.")


def governance():
    summary = load("outputs/governance_v2/summary.json")
    pairs = [c for c in summary['comparisons'] if c['policy']=='B' and c['reference_policy']=='A' and c['eligible_for_fitted_scenario_conclusion']]
    assert len(pairs)==4
    im,dr = canvas("源端协同B相对路径引导A的代价", "仅列通过训练一致性筛选的4个应急情景；差值 = B − A，正值代表时间增加", 1040)
    label(dr,(455,175),"普通车辆总系统时间差（车·秒）",35)
    label(dr,(1325,175),"任务全程时间差（秒）",34)
    # Independent horizontal axes avoid mixing car-seconds with seconds.
    axes = [(460,1110,1600,[0,400,800,1200,1600],'ordinary_system_time_delta_s',ORANGE),(1340,1710,40,[0,10,20,30,40],'task_time_delta_s',RED)]
    for x0,x1,maximum,ticks,_,_ in axes:
        for tick in ticks:
            x=x0+(x1-x0)*tick/maximum
            dr.line((x,250,x,790),fill=GRID,width=2)
            text_center(dr,x,805,str(tick),30,MUTED)
    plotted=[]
    for i,pair in enumerate(pairs):
        y=290+i*145
        h = pair['profile'].split('_')[-1]
        rho = '.824' if pair['preset']=='step_q25' else '.705'
        label(dr,(70,y-32),f"h = {h} s",38)
        label(dr,(70,y+16),f"速度倍率 ρ = {rho}",32,MUTED)
        for x0,x1,maximum,_,key,color in axes:
            value=pair[key]
            end=x0+(x1-x0)*value/maximum
            if value:
                dr.rectangle((x0,y-30,end,y+30),fill=color)
                label(dr,(end+14,y-26),f"+{value:.0f}",36,color)
            else:
                dr.ellipse((x0-6,y-6,x0+6,y+6),fill=color)
                label(dr,(x0+22,y-26),"0",36,color)
        plotted.append({k:pair[k] for k in ['profile','preset','ordinary_system_time_delta_s','task_time_delta_s']})
    label(dr,(75,895),"A在全部情景中均未改道；B在上述情景中没有任务收益，普通交通成本均增加。",34)
    label(dr,(75,955),"总系统时间含源端控制等待、SUMO入网等待和在网时间；h为每个OD的设计发车间隔。",31,MUTED)
    return save(im,"governance_v2.png",{"accepted_B_minus_A":plotted},"Only training-screen accepted conditional profiles are plotted. Positive deltas mean worse travel time, not positive benefit. All original release-to-arrival costs are included.")


def timeseries():
    summary=load("outputs/governance_v2/summary.json")
    series={}
    for policy in ('A','B'):
        p=source(f"outputs/governance_v2/runs/headway_30_step_q10_S3_{policy}/fcd.xml")
        values=[]
        for _,e in ET.iterparse(p,events=('end',)):
            if e.tag=='timestep':
                t=float(e.attrib['time'])
                if 1500<=t<=2700:
                    values.append([t,len(e)])
                e.clear()
        series[policy]=values
    queue=load("outputs/governance_v2/runs/headway_30_step_q10_S3_B/source_queue_trace.json")
    queue=[[r['time_s'],r['source_waiting']] for r in queue if 1500<=r['time_s']<=2700]
    event=load("outputs/governance_v2/runs/headway_30_step_q10_S3_B/event.json")
    im,dr=canvas("时序对应：在网车辆与B源端等待", "h = 30 s，ρ = 0.705；实际SUMO轨迹计数与控制日志；放大仿真1500—2700 s", 1200)
    left,width=150,1570
    minimum,maximum=1500,2700
    def xp(t):
        return left+(t-minimum)/(maximum-minimum)*width
    upper=math.ceil(max(v for values in series.values() for _,v in values)/20)*20
    qupper=max(10,math.ceil(max(v for _,v in queue)/5)*5)
    for index,(title,top,limit,values) in enumerate((("(a) 在网车辆数（辆，含任务）",235,upper,series),("(b) B源端等待数（辆，尚未入网）",710,qupper,{'B':queue}))):
        height=280
        label(dr,(left,top-70),title,37)
        dr.rectangle((xp(event['begin_s']),top,xp(event['end_s']),top+height),fill="#f3f0ea")
        for tick in (0,limit/2,limit):
            y=top+height-tick/limit*height
            dr.line((left,y,left+width,y),fill=GRID,width=2)
            dr.text((left-20,y),f"{tick:g}",anchor='rm',font=font(32),fill=MUTED)
        for t in (1500,1800,2100,2400,2700):
            text_center(dr,xp(t),top+height+15,str(t),31,MUTED)
        for t in (1800,1920,2100,2400):
            x=xp(t)
            for y in range(top,top+height,18):dr.line((x,y,x,min(y+10,top+height)),fill="#8996a1",width=2)
        for policy,points in values.items():
            coords=[(xp(t),top+height-v/limit*height) for t,v in points]
            dr.line(coords,fill=BLUE if policy=='A' else ORANGE,width=5 if policy=='B' else 4)
            if index==1:
                for x,y in coords:dr.ellipse((x-4,y-4,x+4,y+4),fill=ORANGE)
        if index==0:
            for t,txt,y in ((1800,"事件开始",top+95),(1920,"任务发布",top+145),(2100,"控制窗结束",top+95),(2400,"事件结束",top+145)):
                label(dr,(xp(t)+9,y),txt,29,MUTED)
    label(dr,(780,1045),"仿真时间（秒）",34)
    for x,c,txt in ((185,BLUE,"路径引导A"),(655,ORANGE,"源端协同B")):
        dr.line((x,1130,x+65,1130),fill=c,width=5)
        label(dr,(x+85,1105),txt,34)
    label(dr,(1115,1105),"浅色背景：事件有效期",32,MUTED)
    return save(im,"timeseries_v2.png",{"fcd_in_network_counts":series,"source_queue_30s_samples":queue,"event_begin_s":event['begin_s'],"event_end_s":event['end_s'],"task_release_s":summary['timing']['task_release_s'],"control_window_end_s":summary['timing']['task_release_s']+summary['coordination']['maximum_task_control_window_s'],"display_window_s":[1500,2700]},"Counts are actual frozen FCD snapshots (5-second cadence) and source-queue log samples (30-second cadence). Lines connect samples for display, not additional observations. In-network count excludes source queue. These are counterfactual model times, not a recorded real incident.")


def main():
    records=[network(),calibration(),governance(),timeseries()]
    (OUT/'manifest.json').write_text(json.dumps({'schema_version':2,'builder':str(Path(__file__).relative_to(ROOT)),'builder_sha256':sha(Path(__file__)),'figures':records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for r in records:print(r['figure'],r['pixel_dimensions'])


if __name__=='__main__':
    main()
