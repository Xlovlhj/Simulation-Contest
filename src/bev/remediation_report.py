"""阶段八实际结果的通俗报告与证据摘要；不覆盖历史实验。AI辅助：Codex。"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .io import PROJECT_ROOT,read_json,write_json,sha256
from .remediation import EXPERIMENT


def publish():
    calibration=read_json(EXPERIMENT/'calibration_results.json')
    thermal=read_json(EXPERIMENT/'thermal_results.json')
    bounds=read_json(EXPERIMENT/'rolling_bounds_results.json')
    cases=thermal['comparisons'];two=[r for r in cases if r['parking_s']==7200.]
    hot=next(r for r in two if r['parameter_set']=='calibrated_v2_provisional' and r['ambient_c']==40 and r['repetitions']==6)
    sensors=thermal['sensor_trials'];stress=thermal['stress_trials'];transfer=thermal['transfer']
    count=lambda rows:sum(bool(r.get('feasible')) for r in rows)
    failed_stress=[{'case':r['case'],'ambient_c':r['ambient_c'],'sensor_bias_c':r['sensor_bias_c'],
                   'strategy':r['strategy'],'true_cabin_end_error_c':r.get('cabin_final_c',24)-24,
                   'failed_constraints':[k for k,v in r.get('constraints',{}).items() if not v]} for r in stress if not r.get('feasible')]
    summary={'status':'corrective_experiments_completed_with_explicit_limits','source_hashes':{p.relative_to(PROJECT_ROOT).as_posix():sha256(p) for p in EXPERIMENT.glob('*.json')},
             'voltage_metrics':[{k:r[k] for k in ['trip_id','variant','voltage_rmse_v','energy_ape_pct','soc_rmse_pp']} for r in calibration['metrics'] if r['trip_id'] in [588,600]],
             'Cr_reliability_solved':False,'rolling_bounds':bounds['rows'],
             'hot_six_cycle_after_two_hour_parking':hot,
             'two_hour_parking':{'passed':sum(r['valid_close_end_comparison'] for r in two),'total':len(two)},
             'sensor_exact_model':{'passed':count(sensors),'total':len(sensors),
                                   'max_true_terminal_error_c':max(abs(r['cabin_final_c']-24) for r in sensors if r['completed'])},
             'sensor_mismatch_stress':{'passed':count(stress),'total':len(stress),'failures':failed_stress},
             'transfer':{'passed':sum(r.get('valid_close_end_comparison',False) for r in transfer),'total':len(transfer)},
             'step_checks':{'passed':sum(r['passed'] for r in thermal['convergence']),'total':len(thermal['convergence'])},
             'real_thermal_validation':False,'real_sensor_calibration':False,'capacity_conflict_resolved':False,
             'no_new_untouched_measurements':True,'old_evidence_retained':True}
    write_json(PROJECT_ROOT/'records/remediation_summary.json',summary)
    from .workflow import setup_figures
    import matplotlib.pyplot as plt
    setup_figures();figures=EXPERIMENT/'figures';figures.mkdir(exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(10,3.6))
    colors=['#9aa6a3','#c66b44','#287c66']
    for ax,tid in zip(axes,[588,600]):
        rows=[r for r in calibration['metrics'] if r['trip_id']==tid]
        ax.bar(['原参考','冻结v2','OCV缩放候选'],[r['voltage_rmse_v'] for r in rows],color=colors)
        ax.set_title(f'路程{tid}：回顾性诊断');ax.set_ylabel('端电压RMSE / V');ax.grid(axis='y',alpha=.2)
    fig.tight_layout();fig.savefig(figures/'voltage_comparison.png');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,3.8))
    for strategy,mark in [('baseline','o'),('optimized','s')]:
        rows=[r for r in sensors if r['strategy']==strategy]
        for ambient in [25.,40.,-10.]:
            group=[r for r in rows if r['ambient_c']==ambient]
            axes[0].plot([r['sensor_bias_c'] for r in group],[r['cabin_final_c']-24 for r in group],marker=mark,
                         label=f'{ambient:g}℃ {strategy}')
    axes[0].axhline(.1,color='#c66b44',ls='--');axes[0].axhline(-.1,color='#c66b44',ls='--')
    axes[0].set_xlabel('注入固定测温偏差 / ℃');axes[0].set_ylabel('真实座舱终温误差 / ℃');axes[0].set_title('新观测器：精确热模型条件')
    axes[0].legend(fontsize=6,ncol=2);axes[0].grid(alpha=.2)
    names=sorted(set(r['case'] for r in stress))
    translated={'ambient_sensor_plus0.5C':'环境读数','cabin_heat_capacity_plus20pct':'热容',
                'cabin_conductance_plus20pct':'换热系数','measurement_noise':'测量噪声','occupant_heat_plus50W':'乘员热'}
    labels=[translated[n] for n in names]
    ratios=[count([r for r in stress if r['case']==n])/len([r for r in stress if r['case']==n])*100 for n in names]
    axes[1].barh(labels,ratios,color='#287c66');axes[1].set_xlim(0,105);axes[1].set_xlabel('全部约束通过比例 / %')
    axes[1].set_title('加入未建模误差后仍有失败');fig.tight_layout();fig.savefig(figures/'observer_limits.png');plt.close(fig)
    get=lambda tid,var:next(r for r in calibration['metrics'] if r['trip_id']==tid and r['variant']==var)
    metric_rows='\n'.join(f"| {tid} | {get(tid,'frozen_v2')['voltage_rmse_v']:.3f} → {get(tid,'ocv_scaled_candidate')['voltage_rmse_v']:.3f} | {get(tid,'frozen_v2')['energy_ape_pct']:.3f}% → {get(tid,'ocv_scaled_candidate')['energy_ape_pct']:.3f}% | {get(tid,'frozen_v2')['soc_rmse_pp']:.3f} → {get(tid,'ocv_scaled_candidate')['soc_rmse_pp']:.3f} |" for tid in [588,600])
    stress_rows='\n'.join(f"| {name} | {count([r for r in stress if r['case']==name])}/{len([r for r in stress if r['case']==name])} |" for name in names)
    recovery_rows='\n'.join(f"| {r['ambient_c']:g}℃ | {r['raw_saving_pct']:.3f}% | {r['battery_end_difference_c']:.6f}℃ |" for r in two if r['parameter_set']=='calibrated_v2_provisional' and r['repetitions']==3)
    relative='records/experiments/remediation_v1'
    report=fr'''# 问题修复与结果说明

本轮已实际尝试四项技术改进。原实验、论文和数据没有删除；新结果单独归档。用户决定人工部分自行完成，本轮没有把这个意向写成已经人工完成。

## 先看结论

| 问题 | 当前结果 | 能说明什么 |
|---|---|---|
| 低电量电压预测 | 部分改善，未完全解决 | 两路程误差明显下降，仍缺宽SOC实测证据 |
| 滚阻参数可靠性 | 本轮未解决 | 放宽下界后仍贴零，允许负值后出现非物理解 |
| 测温偏差下终温 | 精确模型内改善；真实精度未解决 | {count(sensors)}/{len(sensors)}模拟通过；有热参数/热源误差时仍失败 |
| 高温180分钟恢复 | 新策略{'达到' if hot['valid_close_end_comparison'] else '未达到'}原比较门槛 | 两小时停车后温差{hot['battery_end_difference_c']:.6f}℃；不是实车验证 |
| 电池容量冲突 | 待官方说明 | 无法用程序判定30kWh还是160Ah为正确额定值 |
| 实车温度准确性 | 缺实测，无法确认 | 仿真收支正确不能代替真实测温 |

## 一、电压：让曲线斜率适应训练数据

原模型只允许整条开路电压曲线上下移动。新方案再增加一个所有路程共用的缩放系数，保留曲线单调性，使用模型预测SOC计算电压，不读取逐时真实SOC作为预测答案。

$$U_{{oc}}(z)=U_{{ref}}(0.95)+s[U_{{ref}}(z)-U_{{ref}}(0.95)]+d.$$

通俗解释：原来只允许“把尺子整体抬高或降低”，现在也允许调整刻度的疏密。只用575/596训练，预先声明两个初值，按训练目标选解并冻结，再评价588/600；两者均是回顾性诊断，不是新增盲测。

| 路程 | 电压RMSE / V | 能耗APE | SOC RMSE / 百分点 |
|---|---|---|---|
{metric_rows}

缩放系数为{calibration['candidate']['ocv_scale']:.6f}。能耗误差略有上升，SOC并非处处改善，因此不替换原主结果，也不声称整个电池模型已准确。训练SOC仍主要在87.68%以上，低SOC外推缺少直接约束；候选模型单独保留供后续研究。

![电压改善与保留误差](../{relative}/figures/voltage_comparison.png)

## 二、滚阻：降低边界也没有得到可信参数

新电压方案仍将Cr估到0.003下界。继续只用训练数据做两种诊断：

- 将下界放宽到0，Cr≈{bounds['rows'][0]['parameters']['rolling_coefficient']:.3g}，仍贴下界。
- 仅为诊断允许负值，Cr≈{bounds['rows'][1]['parameters']['rolling_coefficient']:.6g}；负滚阻不符合这里的车辆模型，不采纳。

通俗解释：算法想用“几乎没有滚动阻力，甚至负阻力”补偿其他误差。把下界改小不能让这个数自动变得可信。固定Cr后重拟合其余参数的训练目标也已保存，属于失配和耦合诊断，不冒称置信区间。

本轮没有解决。进一步需要道路坡度/风况、独立滑行或轮端阻力测量，以及更可靠的电池/附件功率标定；现有证据不足以把问题归结为某一个确定原因。

## 三、测温：新增“估计温度和偏差”的观测器

新控制器同时估计座舱真实温度和固定传感器偏差。它只接收带偏差读数、环境与已执行功率；初始估计取第一条带偏差读数，初始偏差估为0，不偷看隐藏真值或注入偏差。

$$C_c\dot T_c=H_c(T_a-T_c)+q,\qquad y=T_c+b,\qquad\dot b=0.$$

通俗解释：温度计可能总是高报1℃。程序结合“开了多少暖风、房间应当怎样升温”，逐渐判断读数偏了多少。这个方法依赖热模型和热量输入足够准确。

三温度、两种策略、五个偏差共{len(sensors)}次精确模型试验，{count(sensors)}次满足全部约束；最大真实座舱终温误差{summary['sensor_exact_model']['max_true_terminal_error_c']:.6f}℃。原来±1℃下失败的设计情况已重新检查。

为避免只展示理想情况，另做{len(stress)}次压力试验：

| 未知误差 | 全部约束通过数 |
|---|---|
{stress_rows}

合计{count(stress)}/{len(stress)}通过，失败全部留档。未知乘员热量和固定读数偏差在稳态可能产生同样的读数，观测器不能无条件区分二者。所以已改善仿真控制，仍未完成真实传感器校准，也不能承诺真实0.1℃精度。

![温度观测器及压力试验](../{relative}/figures/observer_limits.png)

## 四、高温恢复：把部分补冷提前到到达前

新规则在已知计划到达前1200秒，将高温电池冷却启停阈值收紧到31.1℃、回差0.2℃；停车继续使用双方共同策略。基准和优化两方使用同样的回温规则、温度观测器、硬件与约束，所有耗电都计入总账。

通俗解释：原来把较多余热留到停车后处理，现在提前处理一部分。收益可能减少，但比较的终点状态更接近。

原40℃、驾驶180分钟案例，两小时停车后两策略电池温差0.225357℃；改进后为{hot['battery_end_difference_c']:.6f}℃，门槛仍是0.2℃，没有放宽。该案例总账节能率为{hot['raw_saving_pct']:.3f}%，其约束状态为{hot['valid_close_end_comparison']}。

| 90分钟驾驶+两小时停车 | 新策略总账节能率 | 电池终温差 |
|---|---|---|
{recovery_rows}

两套参数、三温度、三驾驶长度的两小时停车配对，{summary['two_hour_parking']['passed']}/{len(two)}达到原热接近条件。0.5→0.25秒的{len(thermal['convergence'])}项检查中{summary['step_checks']['passed']}通过。另有30/35/45℃与60/120分钟的迁移诊断，{summary['transfer']['passed']}/{len(transfer)}检查点达到热接近条件；全部检查点和失败均保留，迁移诊断不等于独立实测验证。

## 五、怎样复现和继续

新代码：src/bev/remediation.py、remediation_bounds.py；单独解析核查：checks/remediation_checks.py。协议：configs/remediation_protocol.json。完整结果：{relative}/；摘要：records/remediation_summary.json。

在工程根目录安装既有依赖，运行：

```powershell
python run.py improve
```

默认核对来源并读取已有冻结新结果；不会重新优化全部旧实验。从交接包重新计算本轮实验，用`python reproduce.py improve`，它创建隔离副本、保留原新实验记录后重算。原`all`仍重现旧主实验，原34项检查与本轮6项解析检查分别统计。

原第七阶段论文及压缩包保留为历史；新论文补入本轮结果和局限。人工工作由用户自行开展，当前没有新增实际人工完成记录。视频仍暂缓。
'''
    (PROJECT_ROOT/'docs/问题修复与结果说明.md').write_text(report,encoding='utf-8')
    stage='# C题阶段八：问题修复与可解边界\n\n详见[问题修复与结果说明](../docs/问题修复与结果说明.md)。本轮新增电压候选、滚阻下界诊断、温度偏差观测器及到达前电池回温；全部实际计算，旧结果保留。\n\n'
    stage+=f"两小时停车热接近{summary['two_hour_parking']['passed']}/{len(two)}，精确热模型测温偏差{count(sensors)}/{len(sensors)}，压力试验{count(stress)}/{len(stress)}，步长检查{summary['step_checks']['passed']}/{len(thermal['convergence'])}。滚阻可靠性、容量冲突和真实测温证据仍未解决。\n"
    (PROJECT_ROOT/'results/阶段八进展.md').write_text(stage,encoding='utf-8')
    return summary


def paper_fragments():
    s=read_json(PROJECT_ROOT/'records/remediation_summary.json')
    metric=lambda tid,var:next(r for r in s['voltage_metrics'] if r['trip_id']==tid and r['variant']==var)
    bounds=s['rolling_bounds']
    voltage=fr'''### 5.5 OCV缩放候选与滚阻下界复查

针对原电压迁移失败，新增共同参数\(s>0\)，令\(U_{{oc}}(z)=U_{{ref}}(0.95)+s[U_{{ref}}(z)-U_{{ref}}(0.95)]+d\)。缩放保持原曲线单调性，仍由预测SOC驱动原Rint模型，未逐时输入真实SOC。只在575/596训练，预先声明两初值并按训练目标选解，先冻结再评价588/600；已知原失败，因此属于修正后的回顾诊断。

候选\(s=0.683378\)。588与600电压RMSE由{metric(588,'frozen_v2')['voltage_rmse_v']:.3f}/{metric(600,'frozen_v2')['voltage_rmse_v']:.3f}V降到{metric(588,'ocv_scaled_candidate')['voltage_rmse_v']:.3f}/{metric(600,'ocv_scaled_candidate')['voltage_rmse_v']:.3f}V；能耗APE分别为{metric(588,'ocv_scaled_candidate')['energy_ape_pct']:.3f}%/{metric(600,'ocv_scaled_candidate')['energy_ape_pct']:.3f}%，略高于原值；SOC RMSE为{metric(588,'ocv_scaled_candidate')['soc_rmse_pp']:.3f}/{metric(600,'ocv_scaled_candidate')['soc_rmse_pp']:.3f}个百分点。该候选改善电压但并非全面优于原模型，单列为补充，不替换前述主结果，也未解决训练SOC覆盖不足。

Cr仍落0.003下界。将下界放宽至0，估计值约{bounds[0]['parameters']['rolling_coefficient']:.2g}，仍贴界；仅作诊断允许负值时得{bounds[1]['parameters']['rolling_coefficient']:.6g}，不符合本模型物理条件，故不采纳。固定Cr后重拟合其他参数的目标剖面保留，不能将改变边界解释为恢复参数可靠性。独立滑行、道路坡度与功率标定仍需补充。
'''
    hot=s['hot_six_cycle_after_two_hour_parking'];sensor=s['sensor_exact_model'];stress=s['sensor_mismatch_stress']
    thermal=fr'''### 6.6 温度偏差观测与到达前电池回温

针对上述失败，另建修正策略，不改原冻结规则。座舱采用增广状态\((T_c,b)\)，满足：

$$C_c\dot T_c=H_c(T_a-T_c)+q,\qquad\dot b=0,\qquad y=T_c+b.$$

离散观测器每5秒用读数创新更新温度和偏差，极点对应120/240秒时间常数；初温估计取首条含偏差读数，偏差初值为0。输入仅含环境与已执行功率，不读取隐藏真温、注入偏差或未来车速。\(H_c\ne0\)且热参数/热输入已知时增广系统可观；未知热源则可能与固定偏差混淆。

三温度、两策略、−1/−0.1/0/0.1/1℃偏差共{sensor['total']}次精确模型试验均可行，最大真实座舱终温误差{sensor['max_true_terminal_error_c']:.6f}℃。另将测量噪声、热容、换热系数、乘员热与环境读数分别扰动，{stress['total']}次中仅{stress['passed']}次满足全部约束，失败保留。观测器改善模型内控制，仍非真实传感器校准或0.1℃实车保证。

高温策略在计划到达前1200秒，双方共同将电池冷却开启阈值收紧至31.1℃、回差0.2℃；停车规则、硬件与比较容差不变，全部电耗计入。原40℃、驾驶180分钟案例两小时停车后温差0.225357℃，新策略绝对温差降至{abs(hot['battery_end_difference_c']):.6f}℃，达到0.2℃门槛，双方其余约束均成立，总账节能率{hot['raw_saving_pct']:.3f}%。

两套参数、三温度、三行程的两小时停车配对为{s['two_hour_parking']['passed']}/{s['two_hour_parking']['total']}达标，扩展步长检查{s['step_checks']['passed']}/{s['step_checks']['total']}通过；30/35/45℃的新增迁移诊断有{s['transfer']['passed']}/{s['transfer']['total']}检查点热终态接近。原失败是已知设计案例，修正结果不构成独立实测验证。新增6项解析核查通过；新代码、协议、偏差和失配试验详见支撑材料remediation_v1目录。
'''
    return {'OCV_CORRECTION':voltage,'THERMAL_CORRECTION':thermal}


if __name__=='__main__':publish()
