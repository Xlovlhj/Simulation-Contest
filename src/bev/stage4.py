"""阶段四：冻结策略的有限长行程验证、图表及恢复状态。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .durability import evaluate_durability, protocol
from .io import PROJECT_ROOT, read_json, verify_sources, write_json
from .progress import write_manifest
from .workflow import run_checks, setup_figures


def plot_durability(evaluation: dict) -> None:
    import matplotlib.pyplot as plt
    setup_figures()
    comparisons=pd.DataFrame(evaluation["comparisons"])
    temps=protocol()["temperatures_c"]
    fig,axes=plt.subplots(1,3,figsize=(13,4),sharey=True)
    for ax,temp in zip(axes,temps):
        for name,label in [("calibrated_v2_provisional","暂定校准"),("original_reference","原参考")]:
            part=comparisons[(comparisons.parameter_set==name)&(comparisons.ambient_c==temp)&(comparisons.parking_s==0)&comparisons.completed]
            ax.plot(part.repetitions*30,part.heat_corrected_saving_pct,"o-",label=label)
        ax.set_title(f"{temp:g}℃")
        ax.set_xlabel("连续驾驶时间 / min")
        ax.set_xticks([30,90,180])
        ax.grid(alpha=.25)
    axes[0].set_ylabel("含理想终态热修正的节能率 / %")
    axes[0].legend()
    fig.suptitle("延长行程：保留电量与热状态；有限时长仿真，不等于长期稳态")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/durability_duration.png")
    plt.close(fig)

    fig,axes=plt.subplots(2,3,figsize=(13,7),sharex=True)
    for col,temp in enumerate(temps):
        for repetitions in protocol()["repetitions"]:
            part=comparisons[(comparisons.parameter_set=="calibrated_v2_provisional")&(comparisons.ambient_c==temp)&(comparisons.repetitions==repetitions)&comparisons.completed]
            for row,key in [(0,"saved_energy_kwh"),(1,"battery_end_difference_c")]:
                line,=axes[row,col].plot(part.parking_s/3600,part[key],"o-",label=f"驾驶{repetitions*30}min")
                invalid=~part.valid_close_end_comparison
                axes[row,col].scatter(part.loc[invalid,"parking_s"]/3600,part.loc[invalid,key],marker="x",s=70,c="#b04040",zorder=3)
        axes[0,col].set_title(f"{temp:g}℃")
        axes[0,col].set_ylabel("累计节省电能 / kWh")
        axes[1,col].set_ylabel("优化−基准电池终温差 / ℃")
        axes[1,col].axhspan(-.2,.2,color="#29966c",alpha=.12,label="预设±0.2℃")
        axes[1,col].set_xlabel("驾驶结束后的带电停车时间 / h")
        axes[1,col].set_xticks([0,1,2])
        for ax in axes[:,col]:
            ax.grid(alpha=.25)
    axes[0,0].legend(fontsize=8)
    fig.suptitle("共同停车温控的实际耗电已计入；红叉表示热终态尚未接近，不能作已对齐比较")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/durability_recovery.png")
    plt.close(fig)

    fig,axes=plt.subplots(3,3,figsize=(14,9),sharex=True)
    for col,temp in enumerate(temps):
        for label,caption in [("baseline","反馈基准"),("optimized","优化规则")]:
            trace=pd.read_csv(PROJECT_ROOT/f"results/timeseries/durability_{temp:g}C_{label}.csv")
            axes[0,col].plot(trace.time_s/3600,trace.battery_c,label=caption,lw=1)
            axes[1,col].plot(trace.time_s/3600,trace.cabin_c,lw=1)
            axes[2,col].plot(trace.time_s/3600,np.cumsum(trace.battery_w*trace.dt_s)/3.6e6,lw=1)
        axes[0,col].set_title(f"{temp:g}℃，连续3个CLTC循环")
        for row,caption in enumerate(["电池温度 / ℃","座舱温度 / ℃","累计净端电能 / kWh"]):
            axes[row,col].set_ylabel(caption)
            axes[row,col].axvline(1.5,color="grey",ls="--",lw=1)
            axes[row,col].grid(alpha=.25)
        axes[2,col].set_xlabel("从出发起的时间 / h；虚线后为停车")
    axes[0,0].legend()
    fig.suptitle("90分钟驾驶 + 2小时共同停车温控；阶段衔接时状态和冷却启停历史不重置")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/durability_traces.png")
    plt.close(fig)

    sensors=pd.DataFrame(evaluation["sensor_sweep"])
    fig,axes=plt.subplots(1,3,figsize=(13,4),sharey=True)
    for ax,temp in zip(axes,temps):
        for strategy,label,marker in [("baseline","反馈基准","o"),("optimized","优化规则","x")]:
            part=sensors[(sensors.ambient_c==temp)&(sensors.strategy==strategy)&sensors.completed]
            ax.plot(part.sensor_bias_c,part.true_cabin_end_error_c,marker+"-",label=label)
        ideal=sensors[(sensors.ambient_c==temp)&(sensors.strategy=="optimized")&sensors.completed]
        ax.plot(ideal.sensor_bias_c,ideal.ideal_steady_error_c,"k--",lw=.8,label="解析稳态")
        ax.axhspan(-.1,.1,color="#29966c",alpha=.15)
        ax.set_title(f"{temp:g}℃；绿色为终温容差")
        ax.set_xlabel("固定测温偏差 / ℃")
        ax.grid(alpha=.25)
    axes[0].set_ylabel("真实座舱终温−24℃ / ℃")
    axes[0].legend(fontsize=8)
    fig.suptitle("同一偏差加在电池与座舱读数；±1℃时舒适温区合格，但严格终温失败")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/sensor_bias_terminal.png")
    plt.close(fig)


def write_stage_four_report(evaluation: dict, checks: dict) -> dict:
    comparisons=evaluation["comparisons"]
    nominal=[r for r in comparisons if r["parameter_set"]=="calibrated_v2_provisional"]
    reference=[r for r in comparisons if r["parameter_set"]=="original_reference"]
    sensors=evaluation["sensor_sweep"]
    completed=[r for r in comparisons if r["completed"]]
    valid=[r for r in comparisons if r.get("valid_close_end_comparison",False)]
    feasible=[r for r in comparisons if r["completed"] and r["baseline_feasible"] and r["optimized_feasible"]]
    final=[r for r in comparisons if r["parking_s"]==7200]
    sensor_pass=sum(r["feasible"] for r in sensors)
    outcomes=[o for j in evaluation["journeys"] for o in j["outcomes"] if o["completed"]]
    balances={key:max(abs(o[key]) for o in outcomes) for key in ["dc_balance_residual_kwh","battery_heat_balance_residual_j","cabin_heat_balance_residual_j"]}
    drive_rows=[]
    for r in nominal:
        if r["parking_s"]!=0 or not r["completed"]:
            continue
        original=next(x for x in reference if x["ambient_c"]==r["ambient_c"] and x["repetitions"]==r["repetitions"] and x["parking_s"]==0)
        drive_rows.append(f"| {r['ambient_c']:g} | {r['repetitions']*30} | {r['raw_saving_pct']:.3f}% | {r['heat_corrected_saving_pct']:.3f}% | {original['heat_corrected_saving_pct']:.3f}% | {r['battery_end_difference_c']:.4f} |")
    recovery_rows=[]
    domain_rows=[]
    for r in nominal:
        if r["parking_s"]!=7200:
            continue
        if not r["completed"]:
            recovery_rows.append(f"| {r['ambient_c']:g} | {r['repetitions']*30} | 未完成 | — | — | — | — | 否 |")
            continue
        recovery_rows.append(f"| {r['ambient_c']:g} | {r['repetitions']*30} | {r['baseline_energy_kwh']:.6f} | {r['optimized_energy_kwh']:.6f} | {r['saved_energy_kwh']:.6f} | {r['raw_saving_pct']:.3f}% | {r['battery_end_difference_c']:.4f} | {'是' if r['valid_close_end_comparison'] else '否'} |")
        domain_rows.append(f"| {r['ambient_c']:g} | {r['repetitions']*30} | {100*r['baseline_soc_final']:.2f}% / {100*r['optimized_soc_final']:.2f}% | {100*r['baseline_soc_outside_training_fraction']:.1f}% / {100*r['optimized_soc_outside_training_fraction']:.1f}% |")
    hot_rows=[]
    for r in nominal:
        if r["ambient_c"]==40 and r["completed"]:
            hot_rows.append(f"| {r['repetitions']*30} | {r['parking_s']/3600:g} | {r['saved_energy_kwh']:.6f} | {r['baseline_battery_final_c']:.4f} / {r['optimized_battery_final_c']:.4f} | {r['battery_end_difference_c']:.4f} | {'是' if r['valid_close_end_comparison'] else '否'} |")
    sensor_rows=[]
    for bias in protocol()["sensor_bias_sweep_c"]:
        part=[r for r in sensors if r["sensor_bias_c"]==bias]
        errors=[abs(r["true_cabin_end_error_c"]) for r in part if r["completed"]]
        sensor_rows.append(f"| {bias:+g} | {max(errors):.6f} | {sum(r.get('cabin_terminal',False) for r in part)} / {len(part)} | {sum(r.get('cabin_comfort',False) for r in part)} / {len(part)} |")
    convergence=evaluation["step_convergence"]
    ce=max(c.get("energy_relative_difference",0) for c in convergence)
    ct=max(c.get("battery_end_abs_difference_c",0) for c in convergence)
    joined=lambda rows: "\n".join(rows)
    report=fr"""# C题阶段四实际进展

日期：2026年10月18日，北京时间。命令：`python run.py stage4`（或`durability`）。本阶段只验证原冻结控制，不重新调参。完整实验协议与计算代码在运行前记录于`records/durability_freeze.json`。

## 先看结论

连续驾驶30、90、180分钟后，冻结规则在本模型内仍节电；将停车温控耗电计入后，收益有所减少。**90分钟驾驶+2小时共同停车温控**，25/40/−10℃分别节约约0.721/0.855/0.097kWh，总账节能率约11.835%/9.850%/0.699%，且两套策略的热终态达到预设接近条件。

**高温180分钟案例仍未完全对齐。** 40℃、暂定校准模型在停车2小时后电池终温差0.2254℃，超过运行前声明的0.2℃，不计入已对齐热终态的有效比较；没有事后放宽阈值。±1℃固定测温偏差的12个单策略案例均未达到24±0.1℃真实座舱终温；±0.1℃在精确模型中通过，但真实校准与误差预算仍待完成。

长行程中大量时间已低于训练SOC范围，热状态也没有实测验证。以下均为有限时长、给定假设下的模拟证据，不能称为实车节能率或无限长时间稳态保证。

## 实验怎样比较

- 使用同一动力/电池/双热节点模型；保持阶段三参数、控制规则、5秒反馈和30秒冷却最小开关间隔不变。
- 1/3/6个公开CLTC-P循环，30/90/180分钟、约14.480/43.439/86.879km。只合并公共零速端点；循环间不重置SOC、温度、控制启停历史。
- 初始SOC94%、电池温度=环境、座舱预调24℃，预调耗电排除。多循环仅在整个行程结束前300秒回24℃，没有在每个小循环末尾人为复位。
- 分别使用暂定校准v2与原参考160Ah参数，共36条完整行程；不将两套模型的绝对能耗混算为节能率，也不将模型差异称为置信区间。
- 停车期间两策略共用同一温控：座舱24℃；40℃环境电池恒温目标31℃、25℃环境25.1℃，回差半宽0.1℃；低温不主动冷却电池。上述均为新增工程设置。
- 停车仍保留低压负载、乘员热量和对应太阳热量，所有附件从电池取电；没有外接电源、没有充电。它表示带电温控停留，不能解释为熄火、无人、关空调停车。
- 原共同约束仍须满足；另声明热终态接近条件：两策略电池终温差≤0.2℃、座舱差≤0.02℃，且各自真实座舱终温24±0.1℃。SOC允许不同，差异对应剩余电量。
- 端电能为UI净积分。保留原理想热修正作参照；实际停车已经计入的耗电不重复加算，修正只估计检查点尚存的相对热差。

## 连续驾驶结果

| 环境℃ | 驾驶min | 校准模型原始节能率 | 校准模型含理想热修正 | 原参考含理想热修正 | 校准模型电池终温差℃ |
|---|---:|---:|---:|---:|---:|
{joined(drive_rows)}

表中热修正仍是核算，不表示已经实际冷却。40℃三个驾驶终点的电池热差明显，须连同下表恢复结果理解。规则可行与热终态接近是两个独立条件。

![行程长度与模型对照](figures/durability_duration.png)

## 停车2小时后的真实模拟总账

下表均为暂定校准模型，能耗含驾驶和完整2小时带电停车，未叠加理想修正。

| 环境℃ | 驾驶min | 基准kWh | 优化kWh | 节省kWh | 总账原始节能率 | 电池终温差℃ | 双方可行且热终态接近 |
|---|---:|---:|---:|---:|---:|---:|---|
{joined(recovery_rows)}

54个配对检查点中，{len(completed)}个完成、{len(feasible)}个双方均满足原共同约束；其中{len(valid)}个另外满足热终态接近条件。18个“停车2小时”配对中{sum(r.get('valid_close_end_comparison',False) for r in final)}个满足接近条件。这些检查点来自同一批行程，彼此相关，不能当作54次独立实测。

### 高温案例具体发生了什么

| 驾驶min | 停车h | 累计节省kWh | 基准/优化电池终温℃ | 温差℃ | 已达到热终态接近条件 |
|---:|---:|---:|---:|---:|---|
{joined(hot_rows)}

以40℃、90分钟驾驶为例，驾驶结束节省1.015014kWh，2小时停车后节省0.854694kWh，差额约0.160320kWh被后续恢复消耗。总账百分比还会因双方都支付停车维持耗电而降低；**百分比下降不全部是先前收益被回收**，应同时看绝对节省kWh。

热终态接近允许小幅余热差，并非严格等温。在40℃、180分钟的失败比较点，残余温差不被隐藏；需要另行声明更长恢复实验后才能判断何时达到接近条件。本阶段没有把恢复时长事后改到刚好通过。

![恢复耗电与残余温差](figures/durability_recovery.png)

![90分钟驾驶与停车连续轨迹](figures/durability_traces.png)

## 测温偏差：舒适不等于终温精确

3个环境×7个固定偏差×2种策略，共42个单策略试验；偏差同时加在电池和座舱测量值。只验证既有控制/执行器模型，不覆盖随机噪声、漂移、测点滞后或所有传感器故障。严格共同约束通过{sensor_pass}/42；±1℃的12个均失败，但舒适温区仍通过。

| 固定偏差℃ | 最大真实座舱终温误差℃ | 严格终温通过 | 舒适温区通过 |
|---:|---:|---:|---:|
{joined(sensor_rows)}

在热负荷前馈准确、参数恒定且附件不饱和的条件下，令真实座舱偏差为e、测量偏差为b、反馈增益为K、环境换热系数为H，理想稳态满足：

$$e_\infty=-\frac{{K}}{{K+H}}b\approx-0.970874b.$$

通俗地说：温度计高报1℃，控制器会把真实座舱调低约0.97℃。继续控更久也不会自动校正温度计。这个公式只是本控制器在给定模型下的偏差传播关系；没有证明所有可能的观测器都无法识别偏差。

±0.1℃偏差的最大真实终温误差约0.09735℃，距离0.1℃阈值只剩约0.00265℃；±0.05℃偏差约0.04880℃。不能用这种理想余量承诺真实车辆。真实严格终温方案需要可追溯校准、独立参照或经过验证的偏差估计，并计入模型、测点与动态误差；当前缺少这些证据，原失败保留。

![偏差传播](figures/sensor_bias_terminal.png)

## 外推范围与程序检查

训练575/596的SOC仅87.68%—100%。下表为暂定校准模型，包含驾驶和2小时停车；“低于训练下界”按时间统计，不是误差估计或失效概率。

| 环境℃ | 驾驶min | 基准/优化最终SOC | 基准/优化低于训练SOC下界的时间占比 |
|---|---:|---:|---:|
{joined(domain_rows)}

最低SOC约3.49%，虽然数学模型没有越出[0,1]，但已经严重超出辨识支持，不能据此确认真实续航或低SOC电压精度。冻结OCV/Cr缺陷和30kWh/160Ah冲突均未解决。

- 自动核查{checks['tests_run']}项通过；新增6项覆盖循环里程/端点、分段状态连续、终点计划不受停车时长干扰、冷却切换间隔继承、解析传感器偏差及不可行比较排除。
- 暂定校准3循环、三温度、两策略、三个检查点，共18项0.5→0.25秒步长检查{'通过' if evaluation['all_step_checks_passed'] else '存在失败'}。总电能最大相对差{100*ce:.6f}%，电池终温最大差{ct:.7f}℃。
- 全部完成检查点的DC收支最大残差{balances['dc_balance_residual_kwh']:.3e}kWh；电池/座舱累计热平衡残差{balances['battery_heat_balance_residual_j']:.3e}/{balances['cabin_heat_balance_residual_j']:.3e}J。
- 原始9文件SHA256与阶段三控制冻结均保持不变；参数/规则未重选。没有实测热验证，没有MPC，也未完成正式论文或比赛视频。

## 文件与下一步

原始结果：`results/tables/durability_evaluation.json`，对照表：`durability_comparisons.csv`，传感器表：`sensor_bias_sweep.csv`；3循环完整轨迹在`results/timeseries/durability_*.csv`。

下一步先补问题二的训练内非线性不确定性与速度激励诊断，保留Cr/OCV局限；再冻结论文证据表、写正式论文并制作四环节视频。测温校准和模型外推仍列为未解决问题，不能因为本阶段实验完成就标为已经解决。
"""
    (PROJECT_ROOT/"results/阶段四进展.md").write_text(report,encoding="utf-8")
    previous=read_json(PROJECT_ROOT/"records/latest_state.json")
    summary={"journeys":len(evaluation["journeys"]),"paired_checkpoints":len(comparisons),
             "both_feasible":len(feasible),"near_equal_feasible":len(valid),
             "two_hour_parking_pairs":len(final),"two_hour_near_equal_feasible":sum(r.get("valid_close_end_comparison",False) for r in final),
             "sensor_trials":len(sensors),"sensor_passed":sensor_pass,"step_checks_passed":evaluation["all_step_checks_passed"],
             "ninety_minute_plus_two_hour_parking":[{key:r[key] for key in ["ambient_c","raw_saving_pct","saved_energy_kwh","battery_end_difference_c","valid_close_end_comparison"]} for r in nominal if r["repetitions"]==3 and r["parking_s"]==7200],
             "balance_maxima":balances}
    state={**previous,"date_beijing":datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
           "status":"stage4_finite_duration_recovery_with_sensor_limits","report":"results/阶段四进展.md","verification":checks,
           "durability":summary,"completed":list(dict.fromkeys(previous["completed"]+[
               "fixed-rule 1/3/6-cycle continuous driving and common powered parking evaluation",
               "42 sensor-bias trials with conditional analytic propagation and explicit failures",
               "18 extended-journey step-halving checks and continuous state handoff tests"])),
           "not_completed":["OCV cross-SOC accuracy and Cr reliability","real sensor calibration and strict true terminal guarantee",
                            "40C calibrated six-cycle near-equal thermal endpoint after two-hour recovery",
                            "MPC comparison if justified","untouched real measurement validation","final paper and submission video"],
           "next":["Training-only nonlinear uncertainty and excitation diagnostics without retuning frozen calibration",
                   "Freeze paper evidence, retain sensor and extended-SOC limits",
                   "Write final paper, reproducible support archive and complete four-part video"]}
    write_json(PROJECT_ROOT/"records/latest_state.json",state)
    write_manifest(stage="stage4_finite_duration_and_powered_recovery")
    return state


def run_stage_four(*, checks: dict|None=None) -> dict:
    verify_sources()
    checks=checks if checks is not None else run_checks()
    evaluation=evaluate_durability()
    plot_durability(evaluation)
    verify_sources()
    return write_stage_four_report(evaluation,checks)
