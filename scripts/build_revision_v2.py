#!/usr/bin/env python3
"""Build the offline V2 explanation from completed, public, frozen results."""
from __future__ import annotations
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/revision_v2"
PATHS = {
    "fit": "outputs/identification_v2/calibration/fit.json",
    "evaluation": "outputs/identification_v2/calibration/evaluation.json",
    "states": "outputs/identification_v2/calibration/conditional_states.json",
    "config": "config/inverse_estimation_v2.json",
    "governance": "outputs/governance_v2/summary.json",
    "audit": "research/parameter_audit/audit_validation.json",
    "register": "research/parameter_audit/parameter_register.json",
    "sources": "research/parameter_audit/sources.json",
    "scenario": "research/parameter_audit/scenario_basis_v2.json",
    "local_eia": "research/parameter_audit/local_eia_evidence.json",
    "geometry": "data/processed/baidu/route_catalog_v2_mapping.json",
    "v1": "outputs/validation/summary.json",
}
DOWNLOADS = {
    "V2方法与结果说明.md": "outputs/identification_v2/calibration/report.md",
    "48项参数处置.md": "research/parameter_audit/parameter_resolution_v2.md",
    "48项参数处置.json": "research/parameter_audit/parameter_resolution_v2.json",
    "参数来源登记.csv": "research/parameter_audit/parameter_register.csv",
    "一手资料目录.json": "research/parameter_audit/sources.json",
    "拥堵设置依据.md": "research/parameter_audit/scenario_basis_v2.md",
    "拥堵设置参数.json": "research/parameter_audit/scenario_basis_v2.json",
    "本地历史交通计数.csv": "research/parameter_audit/local_eia_counts.csv",
    "本地历史交通来源.json": "research/parameter_audit/local_eia_evidence.json",
    "物理反演结果.json": "outputs/identification_v2/calibration/fit.json",
    "时间留出评价.json": "outputs/identification_v2/calibration/evaluation.json",
    "条件流量与等待.json": "outputs/identification_v2/calibration/conditional_states.json",
    "治理完整结果.csv": "outputs/governance_v2/summary.csv",
    "治理完整结果.json": "outputs/governance_v2/summary.json",
    "治理结果说明.md": "outputs/governance_v2/report.md",
    "为什么没有改道.md": "outputs/governance_v2/route_choice_explanation.md",
    "改道临界值.json": "outputs/governance_v2/route_choice_explanation.json",
    "几何与物理复核.md": "research/inverse_engine_geometry_review.md",
    "独立复算检查.json": "outputs/quality/v2_reproduction/audit.json",
    "direct_sumo_times.svg": "outputs/identification_v2/calibration/direct_sumo_times.svg",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="上海瑞金医院外围交通仿真V2：参数来源、直接SUMO反演和完整治理结果。">
<title>B题 · V2 研究说明</title><style>
:root{--ink:#18323b;--muted:#536c75;--teal:#007d83;--blue:#3963a2;--amber:#92571a;--line:#d8e3e5;--paper:#fff;--back:#f2f6f6}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--back);font:16px/1.75 system-ui,-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:var(--ink)}
header{background:#11333c;color:white;padding:32px max(24px,calc((100vw - 1160px)/2));border-bottom:5px solid #18b7ab}header .eyebrow{font-size:13px;letter-spacing:2px;color:#a9dedc}h1{font-size:32px;line-height:1.35;margin:10px 0 15px}header p{max-width:970px;margin:0;color:#dcebed}nav{position:sticky;top:0;z-index:9;background:#fffffff2;border-bottom:1px solid var(--line);padding:10px max(24px,calc((100vw - 1160px)/2));display:flex;gap:26px;backdrop-filter:blur(8px)}nav a{color:var(--ink);text-decoration:none;font-size:14px;font-weight:650;white-space:nowrap}nav a:hover{color:var(--teal)}main{max-width:1208px;margin:auto;padding:28px 24px 70px}section{scroll-margin-top:65px;margin-bottom:30px}.box{background:white;border:1px solid var(--line);border-radius:12px;padding:25px;margin-bottom:20px}h2{font-size:23px;margin:0 0 10px;line-height:1.4}h3{font-size:17px;margin:24px 0 10px}.muted{color:var(--muted)}p{margin:8px 0 14px}.intro{font-size:18px;line-height:1.8}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.metric{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px}.metric strong{font-size:30px;display:block;line-height:1.3;color:var(--teal)}.metric span{font-size:13px;color:var(--muted)}.pill{display:inline-block;padding:3px 10px;border-radius:30px;background:#e5f4f1;color:#126758;font-size:12px;font-weight:650}.pill.warn{background:#fbecdb;color:#875019}.callout{border-left:4px solid var(--teal);background:#edf8f5;padding:14px 18px;border-radius:0 8px 8px 0}.warning{border-color:#b77937;background:#fff6e9}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}.two .box{margin:0}select{font:inherit;border:1px solid #7d9da6;border-radius:6px;padding:8px 36px 8px 12px;background:white;color:var(--ink);max-width:100%}.control{display:flex;align-items:center;gap:15px;flex-wrap:wrap;margin:16px 0}table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;font-weight:700;background:#edf3f3;padding:11px 12px;white-space:nowrap}td{padding:11px 12px;border-bottom:1px solid #e5ecec;vertical-align:top}td.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tr.selected{background:#eaf7f5}tr.crossday{background:#fff7ea}.table-wrap{overflow-x:auto}svg{width:100%;height:auto;display:block}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;margin:10px 0}.legend span:before{content:"";width:13px;height:13px;display:inline-block;margin-right:7px;vertical-align:-1px;background:var(--color);border-radius:2px}.substats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}.substats div{background:#f3f7f7;padding:14px;border-radius:8px}.substats strong{display:block;font-size:22px}.substats small{display:block;color:var(--muted);font-size:12px}.good{color:#086b56}.bad{color:#9a5018}.details{font-size:14px}.download-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.download-grid a{background:#f3f7f7;border:1px solid var(--line);border-radius:6px;padding:11px 13px;color:#245d77;text-decoration:none;font-size:14px}.download-grid a:hover{background:#e6f2f0}a{color:#1b647f}footer{color:var(--muted);font-size:13px;margin-top:30px}.formula{font-family:ui-monospace,monospace;background:#f3f6f6;padding:12px;border-radius:6px;font-size:14px}.nowrap{white-space:nowrap}
@media(max-width:800px){h1{font-size:26px}.grid{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}.download-grid{grid-template-columns:1fr 1fr}.substats{grid-template-columns:1fr}nav{gap:18px;overflow-x:auto}.box{padding:18px}main{padding:20px 14px}table{font-size:13px}header{padding:26px 20px}}
</style></head><body>
<header><div class="eyebrow">全国大学生仿真建模应用挑战赛 · B题 · V2</div>
<h1>让仿真自己产生通行时间，再与观测比较</h1>
<p>上海瑞金医院外围：把每个输入的来源查清，修正重叠道路，在 SUMO 内部估计有效运行约束，并重新检验拥堵治理。页面展示已保存的计算结果，可离线打开。</p></header>
<nav aria-label="章节导航"><a href="#overview">改了什么</a><a href="#sources">数据依据</a><a href="#times">时间与估计</a><a href="#governance">治理结果</a><a href="#downloads">下载证据</a></nav>
<main><section id="overview"><div class="box"><h2>先说清楚这次修订</h2><p class="intro">原来的经验公式只是把时间“换算得更像”。现在调整的是仿真内部的道路运行约束：每试一组参数，都让车辆在 SUMO 中实际走完，再比较结果。地图位置正确、程序能运行、时间接近观测，是需要分别检验的事情。</p><div class="callout">百度提供的是<strong>平台预计通行时间（ETA）</strong>。本页的直接 SUMO 时间没有乘系数、加常数，也没有人为停车补时间。良好的时间一致性，仍不能唯一恢复真实车流、信号配时和排队原因。</div></div><div class="grid" id="overviewMetrics"></div></section>
<section id="sources"><div class="box"><h2>每个数值，都要交代它是什么</h2><p class="muted" id="auditIntro"></p><div class="table-wrap"><table><thead><tr><th>输入类型</th><th>本轮怎样使用</th><th>解释边界</th></tr></thead><tbody>
<tr><td>本地百度观测</td><td>路线几何、四组 OD 的 ETA、完整路段步骤及局部路况</td><td>预计时间不是逐车计时；步骤时间不是额外独立样本。</td></tr>
<tr><td>本地历史公开资料</td><td id="eiaFact"></td><td>年份、时段、断面与方向不同，用作交通量级参考。</td></tr>
<tr><td>仿真内部估计</td><td>瑞金二路、建国西路、其他走廊三组有效运行约束</td><td>综合吸收尚未分解的运行干扰，不能称实际限速或真实信号。</td></tr>
<tr><td>实验设计变量</td><td>需求剖面、事件时长、任务时刻、45秒等待与180秒协调窗口</td><td>保留设计身份及敏感性结果，不伪装成现场测量。</td></tr></tbody></table></div>
<h3>修复了两处“同一条路被画成两条”的采样问题</h3><p id="geometryFact"></p>
<h3>拥堵强度改由同一段道路的训练观测支持</h3><p id="scenarioFact"></p><p class="muted">倍率施加于已校准的道路运行约束。600秒主事件、300／900秒敏感性窗口仍属于实验设计；观测间隔无法证明真实事件起止。</p>
<details><summary>查看方法与公开来源</summary><ul id="sourceLinks"></ul></details></div></section>
<section id="times"><div class="box"><h2>直接 SUMO 时间，接近到什么程度？</h2><p>选择一种需求假设，查看它在相同观测上的表现。多个需求假设都能解释相近时间，正是“真实车流尚未唯一确定”的证据。</p>
<div class="control"><label for="profile">需求剖面</label><select id="profile" aria-label="选择需求剖面"></select><span id="screenPill" class="pill"></span></div><p id="profileExplanation" class="muted"></p>
<div class="legend"><span style="--color:#687b83">百度训练中位 ETA</span><span style="--color:#007d83">V2 直接 SUMO</span><span style="--color:#b8cbd9">V1 未标定 SUMO参考</span></div>
<svg id="timeChart" viewBox="0 0 1000 355" role="img" aria-label="四条路线的观测中位时间与SUMO直接时间比较"></svg>
<p class="muted details">每条路线重复观测，不代表48条独立路线。V1与V2还有路网与需求差异，时间接近不能解释成治理收益。</p>
<h3>用后续时间窗口检验，参数不再调整</h3><div class="table-wrap"><table><thead><tr><th>窗口</th><th>样本</th><th>当前剖面 MAE（秒）</th><th>训练中位数基准 MAE（秒）</th><th>当前剖面平均偏差（秒）</th></tr></thead><tbody id="errorRows"></tbody></table></div>
<div class="callout warning" id="holdoutConclusion" style="margin-top:16px"></div><p class="details muted">这是回顾性的时间留出评价：旧版本已经查看过留出结果。本轮不使用留出窗口重新拟合，也不根据跨日误差重新挑选需求剖面。</p>
<h3>在该需求假设下，模型产生什么车流和等待？</h3><div class="substats" id="stateStats"></div><p class="details muted" id="stateBoundary"></p>
<div class="formula">候选需求 → 三组有效运行约束 → 实际 SUMO 运行 → 比较四条训练中位 ETA → 保留物理有效的结果</div></div></section>
<section id="governance"><div class="box"><h2>治理效果要和同条件对照比较</h2><p>A 在出发时比较可行路径；B 再让部分尚未入网、与保障路线冲突的普通车辆短暂等待。下表显示策略相对参照组的<strong>时间变化</strong>：负值表示减少，正值表示增加。入口等待一直计入总耗时。</p>
<div class="callout" id="governanceConclusion"></div><p class="muted" id="governanceCount"></p>
<div class="control"><label><input type="checkbox" id="onlyCurrent"> 只看当前选中的需求剖面</label></div>
<h3>通过训练一致性筛选：有条件的治理结果</h3><p class="details muted">仍依赖模型和实验设定，不能直接宣称现实上海道路会产生相同比例的收益。</p><div class="table-wrap" id="acceptedTable"></div>
<h3>未通过训练一致性筛选：单独保留的压力测试</h3><p class="details muted">物理运行有效与匹配训练观测是两道门槛。这些结果不能作为已拟合情景的治理结论。</p><div class="table-wrap" id="rejectedTable"></div>
<details style="margin-top:20px"><summary>查看全部运行记录（包括基准与无效标记）</summary><div class="table-wrap" id="allRunTable"></div></details></div></section>
<section id="downloads"><div class="box"><h2>下载可检查的小文件</h2><p class="muted">以下材料均来自本工作区的公开结果与来源登记。此入口不会启动百度采集、重新拟合或运行仿真。</p><div class="download-grid">__DOWNLOADS__</div></div></section>
<footer>页面数据由已保存结果生成；详情、来源和检查结论以下载文件为准。旧版论文、视频与展示仍保留历史身份，V2结果不能与旧版收益混用。队员仍需按赛方规定独立开展必要核心工作与实质复核。</footer></main>
<script id="data" type="application/json">__DATA__</script><script>
'use strict';const D=JSON.parse(document.getElementById('data').textContent);const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(x,d=2)=>x===null||x===undefined?'—':Number(x).toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:d});
const names={east_to_hospital_road:'东侧 → 医院外围',north_to_south:'北端 → 南端',south_to_north:'南端 → 北端',west_to_hospital_road:'西侧 → 医院外围'};
const profileName=id=>({'headway_60':'较低需求 · 每OD 60辆/小时','headway_30':'中等需求 · 每OD 120辆/小时','historical_scale_18':'历史量级参考 · 每OD 200辆/小时','headway_15':'较高需求 · 每OD 240辆/小时'}[id]||id);
const presetName=id=>({'step_q25':'下四分位扰动','step_q10':'下十分位扰动','none':'无事件'}[id]||id);
const phaseName={training:'训练窗口',same_day_exploratory:'同日后续短窗口',cross_day_holdout:'次日留出窗口'};
function table(headers,rows){return '<table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>'}
function renderStatic(){
const nAccepted=D.profiles.filter(p=>p.passes_consistency_screen).length;
$('overviewMetrics').innerHTML=[['160','有效路线快照 · 4组OD'],[String(D.candidates),'实际搜索候选'],[nAccepted+' / '+D.profiles.length,'通过训练一致性筛选'],[String(D.governance.runs.length),'治理与基准运行']].map(x=>'<div class="metric"><strong>'+esc(x[0])+'</strong><span>'+esc(x[1])+'</span></div>').join('');
$('auditIntro').textContent=`本轮登记 ${D.audit.checks.parameter_rows} 项参数或隐含假设、核对 ${D.audit.checks.config_leaves} 个配置叶字段，检索 ${D.audit.checks.source_rows} 项一手资料。软件默认值、当地观测、估计值和控制设计分别记录。`;
$('eiaFact').innerHTML=`2025年瑞金二路昼间20分钟计数 ${D.eia.total_vehicles_20min} 辆，折算 ${D.eia.equivalent_hourly_rate_veh_h} 辆/小时。<a href="${esc(D.eia_url)}" target="_blank" rel="noopener">政府公示原文</a>`;
$('geometryFact').textContent=`两处同向、同名、相同端点的重叠折线规范化后，外部有向边 ${D.geometry.old_edge_count} → ${D.geometry.canonical_edge_count}，允许转向 ${D.geometry.old_allowed_connection_count} → ${D.geometry.canonical_allowed_connection_count}。保留旧边映射、几何残差和36组终端可达性审计；碰撞检测始终开启。`;
$('scenarioFact').textContent=`采用瑞金二路完整步骤：平台距离 ${num(D.scenario.event_geometry.platform_distance_m,0)} 米、模型长度 ${num(D.scenario.event_geometry.model_length_m,2)} 米。12个训练样本的低速分位数与中位数相比，得到运行约束倍率 ${D.scenario.presets.map(p=>num(p.speed_multiplier,5)).join(' / ')}。这些数值是有观测支持的情景代理。`;
const chosenSources=D.sources.sources.filter(s=>['HP_EIA','FHWA_CAL','DEMANDIFY','SUMO_VTYPE','BAIDU_DIRECTION'].includes(s.id));
$('sourceLinks').innerHTML=chosenSources.map(s=>`<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>：${esc(s.usable_evidence)}</li>`).join('');
$('profile').innerHTML=D.profiles.map(p=>`<option value="${esc(p.id)}">${esc(profileName(p.id))}${p.id===D.selected?' · 训练选定':''}</option>`).join('');$('profile').value=D.selected;
const accepted=D.governance.comparisons.filter(r=>r.eligible_for_fitted_scenario_conclusion && (r.policy==='A' || r.reference_policy==='A'));
const a=accepted.filter(r=>r.policy==='A'),b=accepted.filter(r=>r.policy==='B'&&r.reference_policy==='A');
const changedRoutes=D.governance.runs.filter(r=>r.profile_passes_training_screen&&r.policy==='A').reduce((sum,r)=>sum+r.changed_route_count,0);
const allBNoTaskGain=b.length&&b.every(r=>r.task_time_delta_s===null||r.task_time_delta_s>=0);
const allBOrdinaryCost=b.length&&b.every(r=>r.ordinary_system_time_delta_s>0);
$('governanceConclusion').textContent=changedRoutes===0&&allBNoTaskGain&&allBOrdinaryCost?'本轮通过训练筛选的两种需求下：A没有发生改道；B没有缩短保障任务时间，普通车辆总耗时在四组对照中均增加。依据更完整以后，旧版的正收益没有保留下来。完整负结果如下。':`通过训练筛选的 ${accepted.length} 项策略对照全部列出。请同时看普通交通代价和保障任务时间，不能将其中某一个指标的变化解释成全面改善。`;
$('governanceCount').textContent=`已完成 ${D.governance.runs.length} 组运行；${D.governance.runs.filter(r=>r.comparison_eligible).length} 组通过物理与完成性检查。完整数值和状态另存CSV与JSON。`;
$('allRunTable').innerHTML=table(['需求','场景','扰动','策略','到达/计划','普通车总耗时(车·秒)','任务时间(秒)','物理/完成'],D.governance.runs.map(r=>`<tr><td>${esc(profileName(r.profile))}</td><td>${esc(r.scenario_family)}</td><td>${esc(presetName(r.preset))}</td><td>${esc(r.policy)}</td><td>${r.arrived}/${r.planned}</td><td class="number">${num(r.ordinary_system_time_s,0)}</td><td class="number">${num(r.task_release_to_arrival_s,0)}</td><td>${r.comparison_eligible?'通过':'未通过'}</td></tr>`));
}
function chart(p){
const ods=Object.keys(D.targets);const max=Math.ceil(Math.max(...Object.values(D.targets),...Object.values(p.best.predictions_s))/200)*200;const x0=190,width=710;
let content='';for(let tick=0;tick<=max;tick+=200){let x=x0+tick/max*width;content+=`<line x1="${x}" y1="20" x2="${x}" y2="305" stroke="#e2eaea"/><text x="${x}" y="337" text-anchor="middle" fill="#536c75" font-size="13">${tick}</text>`}
ods.forEach((od,i)=>{const y=36+i*70;content+=`<text x="10" y="${y+22}" fill="#18323b" font-size="15">${esc(names[od]||od)}</text>`;[[D.targets[od],'#687b83'],[p.best.predictions_s[od],'#007d83'],[D.v1[od],'#b8cbd9']].forEach(([value,color],j)=>{const yy=y+j*15;const w=value/max*width;content+=`<rect x="${x0}" y="${yy}" width="${w}" height="11" rx="2" fill="${color}"/><text x="${x0+w+7}" y="${yy+10}" fill="#243d47" font-size="12">${num(value,1)}</text>`})});content+='<text x="975" y="337" text-anchor="end" fill="#536c75" font-size="13">秒</text>';$('timeChart').innerHTML=content;
}
function renderProfile(){const p=D.profiles.find(p=>p.id===$('profile').value);const state=D.states.states.find(s=>s.profile_id===p.id);const accepted=p.passes_consistency_screen;const badge=$('screenPill');badge.className='pill'+(accepted?'':' warn');badge.textContent=accepted?'通过训练一致性筛选':'未通过训练一致性筛选';
$('profileExplanation').textContent=`每组OD按${num(p.headway_s,0)}秒间隔提出背景请求；当前四路线最大中位相对误差 ${num(Math.max(...Object.values(p.relative_errors).map(Math.abs))*100)}%。15%是本次工程筛选标准，不能解释成现场验证置信水平。`;
chart(p);const metrics=D.evaluation.profiles[p.id];$('errorRows').innerHTML=['training','same_day_exploratory','cross_day_holdout'].map(phase=>{const row=metrics[phase],reference=D.evaluation.training_median_reference[phase];return `<tr class="${phase==='cross_day_holdout'?'crossday':''}"><td>${phaseName[phase]}</td><td>${row.n}</td><td class="number">${num(row.mae_s)}</td><td class="number">${num(reference.mae_s)}</td><td class="number">${num(row.bias_s)}</td></tr>`}).join('');
const selected=D.evaluation.profiles[D.selected].cross_day_holdout;const reference=D.evaluation.training_median_reference.cross_day_holdout;
$('holdoutConclusion').textContent=`训练选定剖面的跨日平均绝对误差仍为 ${num(selected.mae_s)} 秒，中位数基准为 ${num(reference.mae_s)} 秒。直接SUMO与训练观测更接近，尚未证明次日预测更准确。未通过训练筛选的剖面即使跨日误差较小，也不会因此被反向选为最优。`;
const queue=state.unique_network_halting_vehicles;$('stateStats').innerHTML=[['全网背景请求',num(state.conditional_network_demand_vehicles_per_min,1)+' 辆/分','需求假设，不是真实流量估计'],['普通车平均停车等待',num(state.completed_background_mean_stopped_time_s)+' 秒','直接仿真结果，完成车辆口径'],['全网停车车辆数',num(queue.mean)+' / '+num(queue.max,0),'逐秒均值 / 最大值，含探针、不含未入网车辆']].map(x=>`<div><small>${x[0]}</small><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join('');
$('stateBoundary').textContent=`瑞金二路所选模拟断面的背景通量为 ${num(state.screenline.background_flow_vph,0)} 辆/小时；没有和2025年历史计数断面完成一致性匹配。当前没有恢复真实红绿灯，也没有把道路边累计停车指标冒充唯一排队车辆数。`;
renderGovernance();}
function renderGovernance(){const current=$('profile').value,only=$('onlyCurrent').checked;const rows=D.governance.comparisons.filter(r=>(r.policy==='A'||r.reference_policy==='A')&&(!only||r.profile===current));
const headers=['需求','场景 / 扰动','策略对照','普通车总耗时变化(车·秒)','任务时间变化(秒)','物理对照'];
function render(pass){const selected=rows.filter(r=>r.profile_passes_training_screen===pass);if(!selected.length)return '<p class="muted">当前筛选没有记录。</p>';return table(headers,selected.map(r=>{let val=r.ordinary_system_time_delta_s,t=r.task_time_delta_s;return `<tr class="${r.profile===current?'selected':''}"><td>${esc(profileName(r.profile))}</td><td>${esc(r.scenario_family)} / ${esc(presetName(r.preset))}</td><td>${esc(r.policy)} 相对 ${esc(r.reference_policy)}</td><td class="number ${val<0?'good':val>0?'bad':''}">${num(val,0)}</td><td class="number ${t<0?'good':t>0?'bad':''}">${num(t,0)}</td><td>${r.comparison_valid?'有效':'无效，保留记录'}</td></tr>`}))}
$('acceptedTable').innerHTML=render(true);$('rejectedTable').innerHTML=render(false);}
renderStatic();renderProfile();$('profile').addEventListener('change',renderProfile);$('onlyCurrent').addEventListener('change',renderGovernance);
window.revisionV2={data:D,renderProfile,select(id){$('profile').value=id;renderProfile();},ready:true};
</script></body></html>'''


def main():
    required = {**PATHS, **{f"download:{name}": value for name, value in DOWNLOADS.items()}}
    files = {relative: ROOT / relative for relative in required.values()}
    for relative, path in files.items():
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Missing or unsafe public input: " + relative)
        if path.name.endswith(".local.json") or path.stat().st_size > 2_000_000:
            raise ValueError("Private or unexpectedly large view input: " + relative)
    hashes = {relative: sha(path) for relative, path in files.items()}
    objects = {key: json.loads((ROOT / relative).read_text()) for key, relative in PATHS.items()}
    if objects["governance"]["status"] != "completed":
        raise ValueError("Governance calculations are still partial; build after the complete summary is saved")
    if objects["evaluation"]["fit_sha256"] != hashes[PATHS["fit"]]:
        raise ValueError("Evaluation and fit come from different saved results")
    fit = objects["fit"]
    if fit["config_sha256"] != hashes[PATHS["config"]]:
        raise ValueError("Fit does not match the frozen protocol")
    for key in ("fit", "config"):
        if objects["governance"]["input_hashes"][PATHS[key]] != hashes[PATHS[key]]:
            raise ValueError("Governance belongs to another fitted mechanism")
    eia = next(row for row in objects["local_eia"]["observations"]
               if row["road_name"] == "瑞金二路" and row["period"] == "daytime")
    data = {"profiles": fit["profiles"], "selected": fit["selected_for_time_prediction"],
            "candidates": fit["candidates"], "targets": objects["config"]["target_median_eta_s"],
            "v1": {od: row["physical_sumo_s"] for od, row in objects["v1"]["models"]["per_od"].items()},
            "evaluation": objects["evaluation"], "states": objects["states"],
            "governance": objects["governance"], "audit": objects["audit"], "sources": objects["sources"],
            "scenario": objects["scenario"], "eia": eia, "eia_url": objects["local_eia"]["url"],
            "geometry": {key: objects["geometry"][key] for key in ("old_edge_count", "canonical_edge_count", "old_allowed_connection_count", "canonical_allowed_connection_count")}}
    links = ''.join(f'<a href="downloads/{html.escape(name, quote=True)}" download>{html.escape(name)}</a>' for name in DOWNLOADS)
    document = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    document = document.replace("__DOWNLOADS__", links)
    if any(sha(ROOT / path) != value for path, value in hashes.items()):
        raise ValueError("A source changed while reading; rerun after calculations are complete")
    (OUT / "downloads").mkdir(parents=True, exist_ok=True)
    for name, relative in DOWNLOADS.items():
        shutil.copyfile(ROOT / relative, OUT / "downloads" / name)
    (OUT / "index.html").write_text(document, encoding="utf-8")
    manifest = {"schema_version": 2, "status": "built_from_completed_results",
                "inputs_sha256": hashes,
                "builder_sha256": sha(Path(__file__)),
                "artifacts": {str(path.relative_to(OUT)): sha(path) for path in sorted(OUT.rglob('*'))
                              if path.is_file() and path.name != "manifest.json"},
                "governance_runs": len(data["governance"]["runs"]),
                "offline": True, "collection_or_simulation_started": False}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({"status": manifest["status"], "page": str((OUT / "index.html").relative_to(ROOT)),
                      "governance_runs": manifest["governance_runs"], "downloads": len(DOWNLOADS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
