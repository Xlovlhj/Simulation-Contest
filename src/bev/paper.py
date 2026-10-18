"""用冻结结果生成论文审阅稿与证据索引。AI辅助：Codex/OpenAI；型号及发布日期待核实。

本模块不调用拟合或控制寻优，不将自动生成稿标成参赛队已独立完成的最终论文。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import re

import pandas as pd

from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .portal import render_markdown


DOC_DIR = PROJECT_ROOT / "docs/论文"
DRAFT = DOC_DIR / "C题论文审阅初稿.md"
OUTPUT = PROJECT_ROOT / "results/paper"
TITLE = "电动车能量流与热管理的集中参数仿真、校准和约束节能控制"
TABLE_INPUTS = ["data_audit.json", "scenario_A_summary.json", "scenario_A_capacity80_comparison.json",
                "scenario_A_convergence.json", "scenario_A_sensitivity.csv", "scenario_A_dc_energy.csv",
                "scenario_A_losses.csv", "calibration_metrics.csv", "calibration_group_metrics.csv",
                "parameter_uncertainty.json", "nonlinear_reliability.json", "control_evaluation.json",
                "durability_evaluation.json", "verification.json", "cltc_verification.json"]


def evidence_identity() -> dict:
    paths = ["configs/reference.json", "configs/calibrated.json", "configs/calibration_protocol.json",
             "configs/control_protocol.json", "configs/durability_protocol.json", "configs/reliability_protocol.json",
             "records/source_manifest.json", "records/calibration_freeze.json", "records/control_freeze.json",
             "records/durability_freeze.json", "records/reliability_freeze.json", "records/cltc_downloads.json",
             "src/bev/model.py", "src/bev/calibration.py", "src/bev/control.py", "src/bev/durability.py", "src/bev/reliability.py"]
    paths += ["results/tables/" + name for name in TABLE_INPUTS]
    paths += [p.relative_to(PROJECT_ROOT).as_posix() for p in (PROJECT_ROOT/"results/figures").glob("*.png")]
    paths += [p.relative_to(PROJECT_ROOT).as_posix() for p in (PROJECT_ROOT/"赛题资料").rglob("*") if p.is_file()]
    paths += [p.relative_to(PROJECT_ROOT).as_posix() for p in (PROJECT_ROOT/"data/external").iterdir() if p.is_file()]
    paths += ["records/literature/scipy_least_squares.html"]
    return {name: sha256(PROJECT_ROOT/name) for name in sorted(set(paths))}


def verify_evidence() -> dict:
    verify_sources()
    for name in ["control", "durability", "reliability"]:
        frozen = read_json(PROJECT_ROOT/f"records/{name}_freeze.json")
        for path, digest in frozen["fingerprint"].items():
            if sha256(PROJECT_ROOT/path) != digest:
                raise ValueError(f"{name}冻结指纹变化：{path}")
    for result, freeze in [("control_evaluation", "control"), ("durability_evaluation", "durability"), ("nonlinear_reliability", "reliability")]:
        if read_json(PROJECT_ROOT/f"results/tables/{result}.json")["freeze_sha256"] != sha256(PROJECT_ROOT/f"records/{freeze}_freeze.json"):
            raise ValueError(f"结果与原实验冻结不符：{result}")
    calibration = read_json(PROJECT_ROOT/"records/calibration_freeze.json")
    for key, path in [("protocol_sha256", "configs/calibration_protocol.json"), ("calibrated_sha256", "configs/calibrated.json")]:
        if calibration[key] != sha256(PROJECT_ROOT/path):
            raise ValueError("校准冻结不符")
    fingerprint = evidence_identity()
    path = PROJECT_ROOT/"records/paper_evidence_freeze.json"
    if path.exists():
        if read_json(path)["source_hashes"] != fingerprint:
            raise ValueError("论文证据已变化；须保留旧快照并显式建立新的论文证据版本")
    else:
        write_json(path, {"date_beijing": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                          "status": "post_computation_paper_evidence_snapshot_v1", "source_hashes": fingerprint,
                          "note": "Evidence snapshot after experiments; not a prospective experiment registration. Draft wording may be revised without changing scientific inputs."})
    return fingerprint


def md_table(headers: list[str], rows: list[list]) -> str:
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|"*len(headers)] +
                     ["| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |" for row in rows])


def collect_content() -> tuple[dict, list[dict]]:
    load = lambda name: read_json(PROJECT_ROOT/f"results/tables/{name}.json")
    csv = lambda name: pd.read_csv(PROJECT_ROOT/f"results/tables/{name}.csv")
    base, capacity, convergence = [load(n) for n in ["scenario_A_summary", "scenario_A_capacity80_comparison", "scenario_A_convergence"]]
    audit, uncertainty, nonlinear, control, durability = [load(n) for n in ["data_audit", "parameter_uncertainty", "nonlinear_reliability", "control_evaluation", "durability_evaluation"]]
    metrics, groups = csv("calibration_metrics"), csv("calibration_group_metrics")
    parameters = read_json(PROJECT_ROOT/"configs/calibrated.json")
    ledger = []
    def add(key, question, label, value, unit, source, selector, caveat="指定模型与条件下；不是实车总体保证"):
        ledger.append({"id": key, "question": question, "label": label, "value": value, "unit": unit,
                       "source": source, "selector_or_derivation": selector, "limitation": caveat})
        return value
    def metric(trip, variant, key):
        return float(metrics.loc[metrics.trip_id.eq(trip) & metrics.variant.eq(variant), key].iloc[0])
    tokens = {}
    for key, field, scale, fmt, label, unit in [
        ("BASE_CONSUMPTION", "consumption_kwh_100km", 1, ".4f", "场景A百公里电耗", "kWh/100km"),
        ("BASE_SOC", "soc_final", 100, ".4f", "场景A终SOC", "%"),
        ("DISTANCE", "distance_km", 1, ".5f", "场景A里程", "km"),
        ("BASE_BALANCE", "dc_balance_residual_kwh", 1, ".3e", "场景A母线残差", "kWh")]:
        value = add(key, "问题一", label, float(base[field])*scale, unit, "results/tables/scenario_A_summary.json", field + (f" × {scale}" if scale != 1 else ""))
        tokens[key] = format(value, fmt)
    tokens["RECORDS"] = str(audit["records"])
    tokens["BASE_STEP_PCT"] = f"{100*convergence['energy_relative_difference']:.6f}"
    tokens["CAP80_CONSUMPTION"] = f"{capacity['consumption_kwh_100km']:.4f}"
    tokens["CAP80_SOC"] = f"{100*capacity['soc_final']:.4f}"
    tokens["TRIP_TABLE"] = md_table(["路程/角色", "记录数", "时长s", "SOC始→终%"],
        [[f"{r['trip_id']} / " + ({575:"训练",596:"训练",588:"验证",600:"回顾检验"}[r['trip_id']]), r["records"], f"{r['duration_s']:.1f}", f"{r['soc_start_pct']:.2f}→{r['soc_end_pct']:.2f}"] for r in audit["trip_summary"]])
    tokens["BASE_TABLE"] = md_table(["指标", "结果"], [["里程 / km",f"{base['distance_km']:.5f}"],["净端电能 / kWh",f"{base['net_energy_kwh']:.6f}"],
        ["百公里端电耗 / kWh/100 km",tokens["BASE_CONSUMPTION"]],["终SOC / %",tokens["BASE_SOC"]],["电池最高 / 终温℃",f"{base['battery_max_c']:.5f} / {base['battery_final_c']:.5f}"],
        ["座舱终温 / ℃",f"{base['cabin_final_c']:.5f}"]])
    dc = csv("scenario_A_dc_energy")
    tokens["DC_TABLE"] = md_table(["母线分项", "kWh", "相对净端电能%"], [[r.branch,f"{r.energy_kwh:.6f}",f"{r.share_of_net_terminal_pct:.3f}"] for r in dc.itertuples()])
    loss_labels = {"gear_loss_w":"传动损失", "motor_loss_w":"电机损失", "inverter_loss_w":"逆变器损失", "joule_heat_w":"电池焦耳热", "friction_brake_w":"摩擦制动耗散"}
    losses = csv("scenario_A_losses")
    tokens["LOSS_TABLE"] = md_table(["内部项目", "kWh", "与净端电能比值%"], [[loss_labels[r.component],f"{r.energy_kwh:.6f}",f"{100*r.energy_kwh/base['net_energy_kwh']:.3f}"] for r in losses.itertuples()])
    sensitivity = csv("scenario_A_sensitivity")
    srows = []
    for r in sensitivity[sensitivity.fractional_perturbation.eq(.05)].sort_values("energy_rank",kind="stable").itertuples():
        second=sensitivity[sensitivity.fractional_perturbation.eq(.1)&sensitivity.parameter.eq(r.parameter)].iloc[0]
        srows.append([r.label,f"{r.normalized_energy_sensitivity:.5f}",f"{second.normalized_energy_sensitivity:.5f}",f"{r.normalized_soc_draw_sensitivity:.5f}",f"{r.battery_max_slope_c_per_fraction:.4f}"])
    tokens["SENSITIVITY_TABLE"] = md_table(["参数", "端能耗S±5%", "端能耗S±10%", "SOC降幅S±5%", "最高温斜率℃/相对扰动"], srows)
    for prefix, trip, key, label, unit in [("VAL_ENERGY",588,"energy_ape_pct","588能耗APE","%"),("VAL_SOC",588,"soc_rmse_pp","588 SOC RMSE","百分点"),
        ("VAL_VOLT",588,"voltage_rmse_v","588电压RMSE","V"),("RETRO_VOLT",600,"voltage_rmse_v","600回顾电压RMSE","V")]:
        for suffix, variant in [("BEFORE","reference"),("AFTER","joint_calibrated")]:
            value=add(prefix+"_"+suffix,"问题二",label+("校准前" if suffix=="BEFORE" else "联合校准后"),metric(trip,variant,key),unit,
                      "results/tables/calibration_metrics.csv",f"trip_id={trip},variant={variant},column={key}","600为回顾；电压迁移失败须同时报告")
            tokens[prefix+"_"+suffix]=f"{value:.3f}"
    labels = {"drag_coefficient":"Cd", "rolling_coefficient":"Cr", "resistance_ref_ohm":"Rref / Ω", "capacity_ah":"有效Q / Ah", "ocv_offset_v":"OCV偏移 / V"}
    tokens["PARAMETER_TABLE"]=md_table(["参数", "冻结点估计", "95%线性条件近似区间"],
        [[labels[r["parameter"]],f"{r['estimate']:.6g}",f"[{r['ci_low']:.6g}, {r['ci_high']:.6g}]"] for r in uncertainty["primary_intervals"]])
    for name,label in labels.items():
        add("FIT_"+name,"问题二",label+"冻结值",float(parameters[name]),"见参数名","configs/calibrated.json",name,"暂定校准，Cr靠边界，Q不是铭牌容量")
    role_labels={"train":"训练575/596","validation":"验证588","test_retrospective":"回顾600"}
    variant_labels={"reference":"原参考","nuisance_only":"只调Q/OCV","joint_calibrated":"联合五参数"}
    tokens["CALIBRATION_TABLE"]=md_table(["数据/模型", "SOC宏平均RMSE/百分点", "能耗MAPE/%"],
        [[role_labels[r.role]+" / "+variant_labels[r.variant],f"{r.soc_rmse_macro_pp:.3f}",f"{r.energy_mape_pct:.3f}"] for r in groups.itertuples()])
    tokens["VOLTAGE_TABLE"]=md_table(["路程", "原参考", "只调Q/OCV", "联合五参数"], [[trip]+[f"{metric(trip,v,'voltage_rmse_v'):.3f}" for v in variant_labels] for trip in [575,596,588,600]])
    primary=[r for r in nonlinear["summary"] if r["block_s"]==40]
    tokens["NONLINEAR_TABLE"]=md_table(["参数", "2.5%—97.5%分位范围", "落原下界%"],
        [[labels[r["parameter"]],f"[{r['percentile025']:.6g}, {r['percentile975']:.6g}]",f"{100*r['at_lower_bound_fraction']:.1f}"] for r in primary])
    cr=next(r for r in primary if r["parameter"]=="rolling_coefficient")
    tokens["CR_BOUND_PCT"]=f"{add('CR_BOUND_PCT','问题二','主Cr落下界比例',100*cr['at_lower_bound_fraction'],'%','results/tables/nonlinear_reliability.json','summary: block_s=40, parameter=rolling_coefficient, at_lower_bound_fraction ×100','原工程边界；不是准确性或显著性证明'):.0f}"
    nominal=control["nominal"]
    temps=[25.,40.,-10.]
    row=lambda t,s:next(r for r in nominal if r["ambient_c"]==t and r["strategy"]==s)
    control_rows,comfort_rows,ablation_rows=[],[],[]
    for t,suffix in zip(temps,["25","40","COLD"]):
        b,o=row(t,"baseline"),row(t,"optimized")
        control_rows.append([f"{t:g}℃",f"{b['energy_kwh']:.6f}",f"{o['energy_kwh']:.6f}",f"{o['raw_saving_pct']:.3f}",f"{o['heat_corrected_saving_pct']:.3f}"])
        comfort_rows.append([f"{t:g}℃",f"{o['battery_max_c']:.3f}",f"{o['cabin_min_c']:.3f}—{o['cabin_max_c']:.3f}",f"{o['mean_abs_cabin_deviation_c']:.3f}","满足" if o["feasible"] else "失败"])
        ablation_rows.append([f"{t:g}℃"]+[f"{row(t,s)['heat_corrected_saving_pct']:.3f}" for s in ["regen_only","thermal_only","optimized"]])
        value=add("SAVE_"+suffix,"问题三",f"{t:g}℃单循环含理想热修正节能率",o["heat_corrected_saving_pct"],"%","results/tables/control_evaluation.json",f"nominal: strategy=optimized,ambient_c={t},heat_corrected_saving_pct","30分钟；SOC94%，未计预调耗电；理想终态核算")
        tokens["SAVE_"+suffix]=f"{value:.2f}"
    tokens["CONTROL_TABLE"]=md_table(["环境", "基准kWh", "优化kWh", "原始节能%", "热修正节能%"],control_rows)
    tokens["COMFORT_TABLE"]=md_table(["环境", "电池最高℃", "座舱范围℃", "平均偏离24℃", "共同约束"],comfort_rows)
    tokens["ABLATION_TABLE"]=md_table(["环境", "只改回收", "只改温控", "联合优化"],ablation_rows)
    tokens["P95_US"]=f"{max(r['controller']['decision_p95_us'] for r in nominal):.2f}"
    recovery_rows=[]
    for t,suffix in zip(temps,["25","40","COLD"]):
        selected=next(r for r in durability["comparisons"] if r["ambient_c"]==t and r["repetitions"]==3 and r["parameter_set"]=="calibrated_v2_provisional" and r["parking_s"]==7200)
        recovery_rows.append([f"{t:g}℃",f"{selected['baseline_energy_kwh']:.6f}",f"{selected['optimized_energy_kwh']:.6f}",f"{selected['saved_energy_kwh']:.6f}",f"{selected['raw_saving_pct']:.3f}"])
        value=add("REC_"+suffix,"问题三",f"{t:g}℃90分钟驾驶+两小时停车节能率",selected["raw_saving_pct"],"%","results/tables/durability_evaluation.json",f"comparisons: parameter_set=calibrated_v2_provisional, repetitions=3,ambient_c={t},parking_s=7200,raw_saving_pct","含共同带电停车，预设热终态接近；低SOC外推，无实测热验证")
        tokens["REC_"+suffix]=f"{value:.2f}"
    tokens["RECOVERY_TABLE"]=md_table(["环境", "基准总电能kWh", "优化总电能kWh", "节省kWh", "总账节能%"],recovery_rows)
    failed=next(r for r in durability["comparisons"] if r["ambient_c"]==40 and r["repetitions"]==6 and r["parameter_set"]=="calibrated_v2_provisional" and r["parking_s"]==7200)
    tokens["FAILED_THERMAL"]=f"{add('FAILED_THERMAL','问题三','高温180分钟后两小时停车余温差',failed['battery_end_difference_c'],'℃','results/tables/durability_evaluation.json','comparisons: calibrated_v2_provisional,40C,6cycles,parking_s=7200,battery_end_difference_c','超过预设0.2℃条件；不得列作热终态公平比较成功'):.6f}"
    return tokens,ledger


def build_evidence_documents(ledger: list[dict], fingerprint: dict) -> None:
    payload={"status":"review_draft_evidence_v1","date_beijing":"2026-10-18","metrics":ledger,"source_hashes":fingerprint,
             "team_review":"pending","point_estimates_replaced":False,"new_simulations":False}
    write_json(PROJECT_ROOT/"results/tables/paper_evidence.json",payload)
    pd.DataFrame(ledger).to_csv(PROJECT_ROOT/"results/tables/paper_evidence.csv",index=False,encoding="utf-8-sig")
    rows=[[r["id"],r["label"],f"{r['value']:.8g}",r["unit"],f"`{r['source']}`"] for r in ledger]
    text="""# 三问证据总表

本表把论文结论连回已有计算。证据快照是计算完成后记录的版本，不是新实验预注册。原模型、参数与控制冻结均不改。

## 三问怎样闭合

| 问题 | 主要模型/求解 | 证据与图表 | 已知限制 |
|---|---|---|---|
| 一 | 动力—机电—Rint—双热节点；0.5s积分；七参数双幅度扰动 | scenario_A_*表；场景A轨迹；能量与灵敏度 | 公开CLTC复现序列非官方原表认证；容量冲突；热无实测 |
| 二 | 五参数共享前向有界最小二乘；Q/OCV对照；块wild与非线性重拟合 | calibration_*、parameter_uncertainty、nonlinear_reliability；SOC/电压及联合分布图 | Cr边界、OCV迁移、电压失败；600仅回顾；区间无总体覆盖保证 |
| 三 | 72候选离线穷举、5s反馈；连续行驶、共同停车恢复、测温偏差扫描 | control_evaluation、durability_evaluation；控制/恢复/测温图 | 高温一组余温差未齐；±1℃严格终温失败；舒适偏差不同；低SOC外推 |

## 主要陈述的来源

CSV与JSON保留完整精度、选择条件、来源哈希和每项限制。下面给出阅读索引：

"""+md_table(["证据ID","含义","值","单位","来源"],rows)+"""

## 论文怎样使用这些证据

1. 先写统一边界、初值和约束，再报收益。单循环理想热修正与驾驶+停车实际总账分表。
2. SOC误差写明百分点、宏平均/池化差异；单路程APE与多路程MAPE区分。
3. 好结果与失败并列：能耗变好，同时写电压变差；有界范围同时写Cr贴下界。
4. 提案与已完成工作分开：MPC、实测温度、真实传感器校准和新盲测均未完成。
5. 科研结果来自既有冻结文件；改正文不会自动更新实验。任何数值源变化必须另立证据版本。

## 现在的交付状态

[论文审阅初稿](C题论文审阅初稿.md)可在工作台阅读；[PDF审阅版](../../results/paper/C题论文审阅初稿.pdf)用于检查分页。正式支撑包、AI详情PDF、完整视频和队员独立工作/人工修改记录仍待完成，未执行竞赛上传。
"""
    (DOC_DIR/"三问证据总表.md").write_text(text,encoding="utf-8")


def build_paper() -> dict:
    fingerprint=verify_evidence()
    DOC_DIR.mkdir(parents=True,exist_ok=True)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    tokens,ledger=collect_content()
    source=PROJECT_ROOT/"paper/draft.template.md"
    text=source.read_text(encoding="utf-8")
    for key,value in tokens.items():
        text=text.replace("@@"+key+"@@",value)
    if re.search(r"@@[A-Z_0-9]+@@",text):
        raise ValueError("论文存在未替换证据占位符")
    DRAFT.write_text(text,encoding="utf-8")
    build_evidence_documents(ledger,fingerprint)
    video_source=PROJECT_ROOT/"paper/video.template.md"
    video_text=video_source.read_text(encoding="utf-8")
    for key,value in tokens.items():
        video_text=video_text.replace("@@"+key+"@@",value)
    if re.search(r"@@[A-Z_0-9]+@@",video_text):
        raise ValueError("视频脚本存在未替换证据占位符")
    video_path=PROJECT_ROOT/"docs/正式视频分镜与讲解稿.md"
    video_path.write_text(video_text,encoding="utf-8")
    abstract,body=text.split("<!-- PAPER_ABSTRACT_END -->")
    abstract_html,_=render_markdown(abstract,DRAFT,{})
    body_html,toc=render_markdown(body,DRAFT,{})
    body_html=body_html.replace('loading="lazy"','loading="eager"')
    css=(PROJECT_ROOT/"paper/style.css").read_text(encoding="utf-8")
    math=(PROJECT_ROOT/"portal/vendor/mathjax-3.2.2-full.js").read_text(encoding="utf-8")
    markup='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>'+html.escape(TITLE)+'</title><style>'+css+'</style>'
    markup+='<script>window.MathJax={startup:{typeset:false},svg:{fontCache:"local"},tex:{tags:"none"},options:{enableMenu:false}};</script><script>'+math+'</script></head><body>'
    markup+='<section class="abstract-page">'+abstract_html+'</section><section class="contents-page"><h2>目录</h2>'+toc+'</section><main>'+body_html+'</main>'
    markup+='''<script>window.paperReady=MathJax.startup.promise.then(async()=>{
      for(const el of document.querySelectorAll('.math-inline,.math-display')){
        const tex=el.textContent.trim().slice(2,-2);
        el.replaceChildren(await MathJax.tex2svgPromise(tex,{display:el.classList.contains('math-display')}));
      }
      await document.fonts.ready;
    });</script></body></html>'''
    html_path=OUTPUT/"C题论文审阅初稿.html"
    html_path.write_text(markup,encoding="utf-8")
    if evidence_identity()!=fingerprint:
        raise ValueError("生成中证据输入变化")
    result={"status":"review_draft_not_final_submission","markdown":DRAFT.relative_to(PROJECT_ROOT).as_posix(),
            "html":html_path.relative_to(PROJECT_ROOT).as_posix(),"metrics":len(ledger),
            "video_script":video_path.relative_to(PROJECT_ROOT).as_posix(),
            "evidence_freeze_sha256":sha256(PROJECT_ROOT/"records/paper_evidence_freeze.json"),
            "source_hashes":{**fingerprint,**{p:sha256(PROJECT_ROOT/p) for p in ["paper/draft.template.md","paper/video.template.md","paper/style.css","src/bev/paper.py","src/bev/portal.py","portal/vendor/mathjax-3.2.2-full.js"]}},
            "output_hashes":{p.relative_to(PROJECT_ROOT).as_posix():sha256(p) for p in [DRAFT,html_path,video_path,DOC_DIR/"三问证据总表.md",PROJECT_ROOT/"results/tables/paper_evidence.json",PROJECT_ROOT/"results/tables/paper_evidence.csv"]},
            "team_review":"pending","new_simulations":False}
    write_json(PROJECT_ROOT/"records/paper_build.json",result)
    return result


def render_review_pdf() -> dict:
    """使用实际浏览器离线排版，并核对PDF摘要页、页码、证据数字、图和边界。"""
    import os
    from pathlib import Path
    import pymupdf
    from playwright.sync_api import sync_playwright
    candidates=[Path(os.environ.get("PROGRAMFILES","C:/Program Files"))/"Google/Chrome/Application/chrome.exe",
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
    executable=next((p for p in candidates if p.exists()),None)
    if executable is None:
        raise RuntimeError("PDF审阅版需要Chrome或Edge；Markdown初稿与证据表已生成")
    html_path=OUTPUT/"C题论文审阅初稿.html"
    pdf_path=OUTPUT/"C题论文审阅初稿.pdf"
    errors,network,unexpected_files=[],[],[]
    with sync_playwright() as engine:
        browser=engine.chromium.launch(executable_path=str(executable),headless=True)
        context=browser.new_context(offline=True,viewport={"width":1000,"height":1300})
        page=context.new_page()
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("request",lambda r:network.append(r.url) if r.url.startswith(("https:","http:")) else None)
        page.on("request",lambda r:unexpected_files.append(r.url) if r.url.startswith("file:") and r.url!=html_path.as_uri() else None)
        page.goto(html_path.as_uri(),wait_until="load")
        page.evaluate("async()=>{await window.paperReady}")
        page.evaluate("async()=>{await Promise.all(Array.from(document.images).map(img=>img.decode()))}")
        assert page.locator('[data-mml-node="merror"]').count()==0,"数学排版错误"
        formula_count=page.locator(".math-inline,.math-display").count()
        assert page.locator("mjx-container").count()==formula_count
        assert page.evaluate("Array.from(document.querySelectorAll('mjx-assistive-mml')).every(el=>el.getBoundingClientRect().width<=1.1 && el.getBoundingClientRect().height<=1.1)"),"辅助朗读层不应参与可视排版"
        assert page.evaluate("Array.from(document.images).every(img=>img.complete && img.naturalWidth>0)")
        image_count=page.locator("img").count()
        page.emulate_media(media="print")
        # A4正文宽172mm约650px；大视口会漏掉长公式触发的整页缩放。
        page.set_viewport_size({"width":650,"height":1300})
        overflow=page.evaluate("""()=>Array.from(document.querySelectorAll('table,.math-display,mjx-container,img')).filter(el=>{
          const rect=el.getBoundingClientRect(); return rect.width>document.body.clientWidth+2;
        }).map(el=>({tag:el.tagName,text:el.textContent.slice(0,70),width:el.getBoundingClientRect().width}))""")
        assert not overflow,overflow
        assert page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+2"),"打印布局横向溢出会导致全篇缩小"
        last_destinations=None
        for attempt in range(3):
            page.pdf(path=str(pdf_path),format="A4",print_background=True,prefer_css_page_size=True,
                     display_header_footer=True,header_template='<span></span>',
                     footer_template='<div style="width:100%;text-align:center;font:12pt SimSun;">'+
                     '<span class="pageNumber"></span></div>',margin={"top":"22mm","bottom":"23mm","left":"19mm","right":"19mm"})
            with pymupdf.open(pdf_path) as partial:
                destinations={link['nameddest']:link['page']+1 for p in partial for link in p.get_links()
                              if link.get('nameddest') and link.get('page',-1)>=0}
            if destinations==last_destinations:
                break
            page.evaluate("""destinations=>document.querySelectorAll('.contents-page .toc a').forEach(a=>{
              const key=decodeURIComponent(a.getAttribute('href').slice(1));
              if(!(key in destinations))throw new Error('Missing contents destination '+key);
              let label=a.querySelector('.toc-page');
              if(!label){label=document.createElement('span');label.className='toc-page';a.append(label);}
              label.textContent=destinations[key];
            })""",destinations)
            last_destinations=destinations
        else:
            raise RuntimeError("目录页码未收敛")
        browser.close()
    assert not errors and not network and not unexpected_files,(errors,network,unexpected_files)
    doc=pymupdf.open(pdf_path)
    texts=[p.get_text() for p in doc]
    assert "摘要" in texts[0] and "关键词" in texts[0],"摘要未在第一页完整呈现"
    assert "目录" in texts[1],"目录未从第二页开始"
    assert all(len(t.strip())>100 for t in texts),"空白或近空白页，需调整分页"
    combined="\n".join(texts)
    assert "@@" not in combined and "Administrator" not in combined
    for value in ["14.0542","76.5305","0.826","15.038","25.659","13.54","11.41","0.85","11.83","9.85","0.70","0.225357"]:
        assert value in combined,"PDF缺少关键证据数字："+value
    outside=[]
    for n,p in enumerate(doc):
        footer=p.get_text(clip=pymupdf.Rect(0,p.rect.height-55,p.rect.width,p.rect.height)).strip()
        assert footer==str(n+1),("连续页码",n+1,footer)
        for block in p.get_text("blocks"):
            if block[0]<0 or block[1]<0 or block[2]>p.rect.width+.1 or block[3]>p.rect.height+.1:
                outside.append({"page":n+1,"box":list(block[:4])})
    assert not outside,outside
    # 从PDF核对实际字号，避免CSS正确但浏览器缩小整页。
    def text_size(needle):
        return next(line['spans'][0]['size'] for p in doc for block in p.get_text('dict')['blocks'] if 'lines' in block
                    for line in block['lines'] if needle in ''.join(span['text'] for span in line['spans']))
    actual_sizes={"body":text_size("电动车的驱动"),"title":text_size("集中参数仿真"),"heading":text_size("摘要")}
    for role,expected in [("body",12),("title",16),("heading",14)]:
        assert abs(actual_sizes[role]-expected)<.15,(role,actual_sizes[role],"实际字号不符")
    from PIL import Image,ImageDraw
    thumb_w=298;thumb_h=450;columns=4
    sheet=Image.new("RGB",(columns*thumb_w,((len(doc)+columns-1)//columns)*thumb_h),"#e5e7eb")
    drawing=ImageDraw.Draw(sheet)
    for n,p in enumerate(doc):
        pix=p.get_pixmap(matrix=pymupdf.Matrix(.45,.45),alpha=False)
        thumbnail=Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
        thumbnail.thumbnail((thumb_w-14,thumb_h-28))
        x=(n%columns)*thumb_w+(thumb_w-thumbnail.width)//2;y=(n//columns)*thumb_h+9
        sheet.paste(thumbnail,(x,y))
        drawing.text((x,y+thumbnail.height+4),f"Page {n+1}",fill="black")
    sheet.save(OUTPUT/"paper_contact_sheet.png")
    for index in sorted(set([0,2,min(7,len(doc)-1),min(11,len(doc)-1)])):
        doc[index].get_pixmap(matrix=pymupdf.Matrix(1.25,1.25),alpha=False).save(OUTPUT/f"paper_page_{index+1:02d}.png")
    result={"status":"review_pdf_rendered_not_final","pages":len(doc),"bytes":pdf_path.stat().st_size,"path":pdf_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256":sha256(pdf_path),"formulas_rendered":formula_count,"figures":image_count,"abstract_complete_on_first_page":True,
            "contents_start_on_second_page":True,"continuous_centered_page_numbers":True,"key_numbers_present":True,
            "contents_page_numbers_verified":True,"assistive_math_layer_hidden":True,
            "actual_pdf_font_sizes_pt":actual_sizes,"global_print_shrink_absent":True,
            "all_text_inside_page":True,"page_errors":errors,"http_requests":network,"unexpected_file_requests":unexpected_files,
            "team_review":"pending","visual_review":"pending","font_requirement":"CSS SimSun 12pt; title SimHei16pt; h2 SimHei14pt; no page header"}
    assert result["bytes"]<=20*1024*1024,"PDF超过20MB"
    write_json(PROJECT_ROOT/"records/paper_pdf_verification.json",result)
    doc.close()
    build=read_json(PROJECT_ROOT/"records/paper_build.json")
    build["pdf"]=result
    build["output_hashes"][pdf_path.relative_to(PROJECT_ROOT).as_posix()]=result["sha256"]
    write_json(PROJECT_ROOT/"records/paper_build.json",build)
    return result
