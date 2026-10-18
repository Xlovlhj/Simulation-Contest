"""阶段三控制诊断、图表、报告和恢复状态；AI辅助见records。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .adequacy import run_adequacy
from .control import control_protocol, evaluate_control, optimize_rules
from .io import PROJECT_ROOT, read_json, verify_sources, write_json
from .progress import write_manifest
from .workflow import run_checks, setup_figures


def plot_control(evaluation: dict) -> None:
    import matplotlib.pyplot as plt
    setup_figures()
    temps = control_protocol()["design_temperatures_c"]
    fig, axes = plt.subplots(3,3,figsize=(14,10),sharex="col")
    for col,temp in enumerate(temps):
        for label,caption in [("baseline","反馈基准"),("optimized","优化规则")]:
            trace = pd.read_csv(PROJECT_ROOT/f"results/timeseries/control_{temp:g}C_{label}.csv")
            axes[0,col].plot(trace.time_s,trace.battery_c,label=caption)
            axes[1,col].plot(trace.time_s,trace.cabin_c,label=caption)
            axes[2,col].plot(trace.time_s,np.cumsum(trace.battery_w*trace.dt_s)/3.6e6,label=caption)
        axes[0,col].set_title(f"{temp:g}℃ CLTC；初始SOC94%、座舱24℃")
        axes[0,col].set_ylabel("电池温度 / ℃")
        axes[1,col].set_ylabel("座舱温度 / ℃")
        axes[1,col].axhline(22,color="grey",ls="--",lw=.8)
        axes[1,col].axhline(26,color="grey",ls="--",lw=.8)
        axes[2,col].set_ylabel("累计净端电能 / kWh")
        axes[2,col].set_xlabel("时间 / s")
        for ax in axes[:,col]:
            ax.grid(alpha=.25)
    axes[0,0].legend()
    fig.suptitle("相同工况、初值与约束的控制对照；图中累计端电能未加终态热修正")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/control_comparison.png")
    plt.close(fig)
    nominal = pd.DataFrame(evaluation["nominal"])
    fig,axes = plt.subplots(1,3,figsize=(12,4))
    captions = ["基准","仅优化回收","仅优化温控","联合优化"]
    for ax,temp in zip(axes,temps):
        part=nominal[nominal.ambient_c.eq(temp)]
        ax.bar(np.arange(4),part.heat_corrected_saving_pct,color=["#999999","#3384ad","#e7a34d","#2a916e"])
        ax.set_xticks(np.arange(4),captions,rotation=20,fontsize=9)
        ax.axhline(0,color="grey",lw=.7)
        ax.set_title(f"{temp:g}℃")
        ax.set_ylabel("含理想终态热修正的节能率 / %")
    fig.suptitle("消融对照：区分回收与温控的贡献；未宣称真实道路节能率")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/control_ablation.png")
    plt.close(fig)
    robust = pd.DataFrame(evaluation["robustness"])
    good = robust.baseline_feasible & robust.optimized_feasible
    fig,ax = plt.subplots(figsize=(11,6))
    labels = robust.apply(lambda r:f"{r['case']} | {r['cycle']} {r['ambient_c']:g}℃",axis=1)
    colors = ["#2a916e" if value else "#ba5555" for value in good]
    ax.barh(np.arange(len(robust)),robust.heat_corrected_saving_pct,color=colors)
    ax.set_yticks(np.arange(len(robust)),labels,fontsize=8)
    ax.set_xlabel("含理想终态热修正的节能率 / %")
    ax.set_title("参数扰动与迁移诊断；红色未满足全部约束，不计为有效节能证据")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/control_robustness.png")
    plt.close(fig)


def write_stage_three_report(freeze: dict,evaluation: dict,adequacy: dict,checks: dict) -> dict:
    selected = freeze["selected_rule"]
    optimized = [row for row in evaluation["nominal"] if row["strategy"]=="optimized"]
    baselines = {row["ambient_c"]:row for row in evaluation["nominal"] if row["strategy"]=="baseline"}
    rows="\n".join(f"| {r['ambient_c']:g}℃ | {baselines[r['ambient_c']]['energy_kwh']:.6f} | {r['energy_kwh']:.6f} | "
                    f"{r['raw_saving_pct']:.3f}% | {r['heat_corrected_saving_pct']:.3f}% | {'是' if r['feasible'] else '否'} |" for r in optimized)
    temperature_rows="\n".join(f"| {r['ambient_c']:g}℃ | {r['battery_max_c']:.3f} | {r['battery_final_c']:.3f} | "
                                f"[{r['cabin_min_c']:.3f}, {r['cabin_max_c']:.3f}] | {r['cabin_final_c']:.4f} | {r['mean_abs_cabin_deviation_c']:.3f} |" for r in optimized)
    weights=pd.DataFrame(adequacy["weight_variations"])
    failures=[r for r in evaluation["robustness"] if not r["baseline_feasible"] or not r["optimized_feasible"]]
    valid=[r for r in evaluation["robustness"] if r["baseline_feasible"] and r["optimized_feasible"]]
    p95=max(row["controller"]["decision_p95_us"] for row in evaluation["nominal"])
    robustness_rows="\n".join(f"| {r['case']} | {r['cycle']} | {r['ambient_c']:g}℃ | "
                               f"{'是' if r['baseline_feasible'] else '否'} / {'是' if r['optimized_feasible'] else '否'} | "
                               f"{r['heat_corrected_saving_pct']:.3f}% |" for r in evaluation["robustness"])
    report=f"""# C题阶段三：控制原型与模型适用性

日期：2026年10月17日（北京时间）。`python run.py stage3`重现本阶段；`python run.py all`连同此前阶段重现。当前为控制原型，未完成最终论文与比赛视频。

## 本阶段判断

共享物理模型已接入可执行控制器，完成有限网格寻优、回收/温控消融、参数扰动与新温度/新工况检验。**三个设计场景均满足声明约束，模型内有节能收益；传感器偏差使严格终点温度条件失败，尚不能宣称所有鲁棒性检查通过。** OCV跨SOC迁移与Cr边界问题保留，未用600修改电池曲线或参数。

## 先诊断模型，保留不能解决的部分

只用575/596进行7组目标权重诊断。各组拟合的Cd约{weights.drag_coefficient.min():.4f}—{weights.drag_coefficient.max():.4f}，Cr约{weights.rolling_coefficient.min():.6f}—{weights.rolling_coefficient.max():.6f}，Rref约{weights.resistance_ref_ohm.min():.5f}—{weights.resistance_ref_ohm.max():.5f}Ω。Cr始终靠近下界，改变通道权重未消除它；不能据优化器成功认为真实滚阻已确定。

训练两路程电流积分对应的最小二乘有效容量约54.64与88.72Ah，相差显著；近零电流也不等于充分静置后的OCV。训练SOC仅87.68%—100%，不足以支持中低SOC电压修正。**没有凭空改造低SOC曲线，也没有把观测SOC/I送入前向预测来压低误差。** 冻结参数不变，增加控制接口后完整重算原训练辨识，五参数与原冻结值差异均为0。

## 公平比较设置

25、40、−10℃使用相同CLTC、同一组暂定校准参数与物理方程；所有策略初始SOC94%，电池温度=环境，座舱预调至24℃。预调温耗电未计入，不能将本表直接与问题一初始SOC80%的参考电耗作节能对照，也不能称冷浸启动全过程能耗。

共同工程约束：座舱22—26℃、电池−20—45℃、空调/电池冷却共享4kW制冷电功率预算、加热≤5kW；保持原电机/Rint/SOC与低温/高SOC回收限制。冷却启停最小间隔30s；终点座舱24±0.1℃；初始环境≥35℃时终点电池不高于初温+0.1℃。数值阈值均为本项目的声明假设，不是官方额定安全认证。

原简单温控在低温下不能保证舒适，因此控制对照采用**热负荷前馈+温度反馈的可行基准**（24℃目标、35开/32关、回收0.65）。新的优化策略与此基准共用执行器、信息和约束，未拿失去舒适性的“全关闭”方案比较。

终态余热可能使有限行程看起来省电。因此同时报告原始端电耗和理想终态热修正：环境≥25℃时，将候选比基准更热的电池所存热量除COP，另加入两策略座舱终温差的保守换算。**这只是理想化核算诊断，不是实际回温过程，也不证明长时间循环同样省电。** 基准若结束得更冷，也不会无条件把它的温控电耗全算作无效。

## 算法与选定规则

离线穷举{freeze['candidates']}组回收强度、冷却开关阈值/回差、座舱节能偏移；在三个设计温度上按等权的修正能耗比例选取可行最小值。{freeze['feasible_candidates']}组可行。设计耗时约{freeze['elapsed_s']:.2f}s，属离线计算。**只证明枚举候选中的最优，未证明连续控制空间全局最优，也没有把它称为MPC。**

选定名义规则：回收强度{selected['regen_fraction']:.2f}、冷却开启阈值{selected['cooling_on_c']:.2f}℃、回差{selected['cooling_hysteresis_c']:.2f}℃、请求座舱偏移{selected['cabin_economy_offset_c']:.2f}℃。实际执行还有约束保护：热环境阈值取名义值与“初始测温+0.05℃”中的较小者，所以40℃初始时约40.05℃开启、35℃初始时约35.05℃开启；不是一律等到42℃。座舱为1℃测温不确定性留余量，节能目标限制在23—25℃，故请求偏移1.5℃实际最多1℃。

每5s根据当前SOC/温度/环境计算动作，不读取未来车速。温控先利用自然升/降温，不为进入节能温区启动反季节设备；计划到达前300s回24℃。**因此需要已知的计划到达时间**；若实际到达时间未知，须另做滚动终态处理或取消严格终态承诺。记录中最大的策略决策P95约{p95:.2f}微秒（本机观测），远小于5s周期，但不等于车载最坏执行时间认证。

控制v1/v2迭代已保留在`records/experiments/control_rule_v1/`和`control_rule_v2/`：v1存在为改变节能设定点而主动反向用热设备的问题；v2在35℃迁移时缺少相对终温保护。v3修正逻辑、保留测温偏差失败，不删除历史。

## 三个设计场景的实际结果

| 温度 | 基准净端电能/kWh | 联合优化/kWh | 原始节能率 | 含理想终态热修正 | 共同约束满足 |
|---|---:|---:|---:|---:|---|
{rows}

| 温度 | 电池最高/℃ | 电池终点/℃ | 座舱范围/℃ | 座舱终点/℃ | 平均偏离24℃/℃ |
|---|---:|---:|---|---:|---:|
{temperature_rows}

设计场景修正节能率宏平均约{np.mean([r['heat_corrected_saving_pct'] for r in optimized]):.3f}%。该数值是指定仿真和约束内的结果，**不是独立实车节能率，也不是所有气候的平均续航提升**。座舱温区虽然相同，舒适偏差仍不同，表中明确给出。−10℃回收被低温限制，大部分收益来自温控；常温/高温的主要收益来自回收，消融图单列。

## 扰动与迁移检验

在冻结规则后检查参考参数、Cr=0.008、内阻+25%、电池热容−20%、COP−20%、反馈测温偏差+1℃；再检查CLTC及附件3参考E的30/35℃新场景。同一条规则没有按各检验结果单独重调。

| 检验 | 工况 | 温度 | 基准/优化均满足全部约束 | 修正节能率（失败时仅供诊断） |
|---|---|---|---|---:|
{robustness_rows}

共{len(evaluation['robustness'])}组配对检验，{len(valid)}组双方均可行，{len(failures)}组未满足全部约束。测温偏差场景仍保持舒适/安全温区，但双方均未满足严格24±0.1℃真实终温，**这些组不计入有效节能证据**。单个有偏温度测量无法支持0.1℃真实精度承诺；需要校准传感器、增加独立观测或重新论证与测量误差相容的终态标准。没有静默放宽阈值。

## 验证、适用范围与下一步

{checks['tests_run']}项自动核查通过。新增检查包括无动作控制器不改变原模型、已知负载与控制不能重复叠加、非法执行器动作/共享制冷超限拒绝、冷热环境座舱解析热平衡、控制器无法绕过低温/高SOC回收保护、自然温漂时不启用反季节设备。0.5→0.25s步长检查在三个设计场景均通过。

控制试验使用的94%起始SOC尽量贴近训练高SOC域，但低温长程仍可能离开该域；每场景输出域外时长比例。环境/热模型无独立温度实测，性能仍依赖假设。内阻/OCV不确定性不能靠控制结果反向证明已消除。当前也尚未做带老化约束、实际充电功率边界、完整制动安全认证、长时间热循环或MPC对照，不宣称已有相关证据。

下一步优先：传感器误差与终态条件的可实现性、长时间热状态/恢复能量验证、必要的非线性参数不确定性检验；再增加滚动优化/MPC对照，整理论文与3分钟正式视频。MP4开发样片如已生成，只用于验证编码与动态展示，不等于最终视频四环节已经齐备。

图表：[控制轨迹](figures/control_comparison.png)、[消融](figures/control_ablation.png)、[扰动与迁移](figures/control_robustness.png)、[训练权重稳定性](figures/training_weight_stability.png)。完整数值见`results/tables/control_evaluation.json`与相应CSV；原理通俗说明见`docs/节能控制原理与使用说明.md`。
"""
    (PROJECT_ROOT/"results/阶段三进展.md").write_text(report,encoding="utf-8")
    previous=read_json(PROJECT_ROOT/"records/latest_state.json")
    completed=previous.get("completed",[])
    additions=["training-only battery adequacy and seven weight diagnostics","finite-grid causal control optimization with shared physical plant",
               "control ablations, thermal terminal accounting, robustness and scenario transfer"]
    state={**previous,"status":"stage3_control_prototype_with_explicit_failures","verification":checks,
           "completed":list(dict.fromkeys(completed+additions)),
           "not_completed":["OCV cross-SOC accuracy and Cr reliability","sensor-bias compatible terminal guarantee",
                            "long-duration thermal recovery and MPC comparison","untouched real measurement validation","final paper and submission video"],
           "control_rule":selected,"control_nominal_heat_corrected_savings_pct":{str(r["ambient_c"]):r["heat_corrected_saving_pct"] for r in optimized},
           "control_robustness":{"paired_cases":len(evaluation["robustness"]),"both_feasible":len(valid),"failed":len(failures)},
           "report":"results/阶段三进展.md",
           "next":["Resolve sensor bias versus terminal tolerance; validate thermal recovery over extended cycles",
                   "Retain OCV/Cr adequacy limits and add defensible nonlinear uncertainty",
                   "MPC tradeoff if useful; final paper, reproducible archive and three-minute video"]}
    write_json(PROJECT_ROOT/"records/latest_state.json",state)
    write_manifest(stage="stage3_constrained_rule_control_prototype")
    return state


def run_stage_three(*,checks: dict|None=None) -> dict:
    verify_sources()
    checks=checks if checks is not None else run_checks()
    adequacy=run_adequacy()
    freeze=optimize_rules()
    evaluation=evaluate_control()
    plot_control(evaluation)
    verify_sources()
    return write_stage_three_report(freeze,evaluation,adequacy,checks)
