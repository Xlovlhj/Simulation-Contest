"""阶段十结果解释与论文补充；不重新选参数。"""
from __future__ import annotations

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from .io import PROJECT_ROOT, read_json, write_json, sha256
from .extension import ROOT, begin
from .workflow import setup_figures


def collect():
    begin()
    road, temp, sensor, voltage = [read_json(ROOT/(n+'_results.json')) for n in ['road', 'temperature', 'sensor', 'voltage']]
    failures = [{k: r[k] for k in ['ambient_c', 'case', 'sensor_bias_c', 'strategy', 'constraints', 'cabin_final_c', 'battery_final_c']}
                for r in sensor['trials'] if not r['practical_pass']]
    thermal_scores = {r['variant']: r['rmse_c'] for r in temp['scores'] if r['role'] == 'temporal_holdout'}
    out = {'stage': 10, 'road_external_proxy': road['proxy_not_pure_Cr'],
           'road_configurations': road['configurations'], 'road_training_cost_increase_pct': 100*road['cost_relative_increase'],
           'Cr_reliability_solved': False, 'capacity_treatment': '160Ah reference + 80Ah energy-equivalent scenario; conflict disclosed, not resolved',
           'external_temperature_holdout_rmse_c': thermal_scores, 'target_real_thermal_precision_solved': False,
           'guarded_sensor_trials': sensor['trials_total'], 'guarded_practical_pass': sensor['practical_pass'],
           'guarded_strict_terminal_pass': sensor['strict_terminal_pass'], 'guarded_pairs_pass': sensor['paired_practical_pass'],
           'positive_ideal_adjusted_pairs': sensor['positive_ideal_adjusted_pairs'],
           'practical_and_positive_ideal_adjusted_pairs': sum(r['both_practical_pass'] and r['ideal_uncertainty_adjusted_saved_kwh'] > 0 for r in sensor['comparisons']),
           'sensor_failures': failures, 'real_sensor_bias_calibrated': False,
           'voltage_metrics': voltage['metrics'], 'preferred_voltage_model': 'one_RC' if voltage['adopt_RC_as_retrospective_candidate'] else 'original_Rint',
           'voltage_comparison_gates': voltage['gates'], 'voltage_step_checks_pass': sum(r['passed'] for r in voltage['convergence']),
           'low_soc_measured_accuracy_solved': False, 'evaluation_is_untouched': False,
           'old_frozen_physics_and_control_replaced': False,
           'RC_R0_and_Cr_at_lower_bounds': True,
           'source_hashes': {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in [ROOT/(n+'_results.json') for n in ['road', 'temperature', 'sensor', 'voltage']]}}
    write_json(PROJECT_ROOT/'records/extension_summary.json', out)
    return out


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '|'+'---|'*len(headers)]+['| '+' | '.join(map(str, r))+' |' for r in rows])


def voltage_table(summary):
    labels = {'original_Rint': '原Rint', 'stage8_OCV_scaled': 'OCV缩放', 'new_one_RC': '新一阶RC'}
    return table(['路程', '模型', '电压RMSE/V', '能耗APE/%', 'SOC RMSE/百分点'],
                 [[r['trip_id'], labels[r['variant']], f"{r['voltage_rmse_v']:.3f}", f"{r['energy_ape_pct']:.3f}", f"{r['soc_rmse_pp']:.3f}"]
                  for r in summary['voltage_metrics'] if r['role'] == 'retrospective'])


def figures():
    setup_figures()
    dest = ROOT/'figures'
    dest.mkdir(exist_ok=True)
    data = pd.read_csv(ROOT/'voltage_comparison.csv')
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    labels = ['原Rint', 'OCV缩放', '一阶RC']
    names = ['original_Rint', 'stage8_OCV_scaled', 'new_one_RC']
    for ax, tid in zip(axes, [588, 600]):
        part = data[data.trip_id.eq(tid)].set_index('variant')
        ax.bar(labels, [part.loc[n, 'voltage_rmse_v'] for n in names], color=['#8192a8', '#b6c8d7', '#18806a'])
        ax.set(title=f'路程{tid}（回顾评价）', ylabel='电压RMSE / V')
        ax.grid(axis='y', alpha=.2)
    fig.tight_layout()
    fig.savefig(dest/'voltage_comparison.png')
    plt.close(fig)
    data = pd.read_csv(ROOT/'oxford_temperature_predictions.csv')
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(data.time_s, data.measured_c, label='牛津实测温度')
    ax.plot(data.time_s, data.predicted_c, label='单节点预测')
    ax.axvline(data.loc[data.role.eq('temporal_holdout'), 'time_s'].iloc[0], color='black', ls='--', lw=1, label='训练/后段分界')
    ax.set(xlabel='时间 / s', ylabel='电芯温度 / ℃', title='外部小电芯：后段预测未优于简单基线')
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(dest/'oxford_temperature.png')
    plt.close(fig)


def paper_fragments():
    s = collect()
    return {
        'EXTERNAL_RC_CORRECTION': f'''### 5.6 外部路阻参照与一阶RC电压改进

从EPA官方2026测试表[7]的458条Electricity记录中，按测试重量为本题质量0.8—1.25倍筛选，以车型/测试车辆/重量合并重复测试，并要求Target阻力系数有效，得到{s['road_configurations']}个配置。Target A包含非轮胎贡献，不能直接视作纯滚阻；其等效常数阻力系数中位数为{s['road_external_proxy']:.6f}。固定此参照值、仅用575/596重新拟合其他五个OCV缩放参数，综合目标恶化{s['road_training_cost_increase_pct']:.2f}%，故不采用其替换本车Cr，也不称滚阻可靠性已解决。

保留Rint及OCV缩放方案，新增一阶RC支路：$U=U_{{oc}}-R_0I-v_p$、$\\dot v_p=(R_pI-v_p)/\\tau$。极化相当于电池对负载的短时“记忆”。子步恒流下精确积分该支路，用平均电压求解端口功率，同时区分$R_0$和$R_p$发热及电容储能变化，避免把储能全部计成热。其余车辆和双热方程沿用原模型；正向计算不逐时输入实测电流或SOC。

只用575/596、两个预定初值按原综合目标选解，冻结后再评价已被查看的588/600。比较门槛预定为两路电压RMSE各较OCV缩放低至少10%，能耗APE不高于原模型0.5个百分点、SOC RMSE不高于原模型0.2个百分点，且守恒和步长核查通过。结果见表10。

**表10 原Rint、OCV缩放与一阶RC的回顾比较**

{voltage_table(s)}

选择一阶RC作为本轮电压预测推荐模型；$R_0=0.005000$ Ω、$R_p=0.204019$ Ω、$\\tau=7.5263$ s、有效容量71.8309 Ah、OCV缩放0.57923。Cr与$R_0$仍贴工程下界，不把拟合值称真实参数。四项0.5→0.25秒检查均通过，电压预测差异最大约0.013 V；初始极化取0，另做训练内±5 V扰动，电压RMSE变化小于0.053 V。该选择来自回顾比较，低SOC准确性仍未确证。问题一及第6章原Rint控制证据保留；RC未重做规则寻优、长程和参数区间，不把旧热控制结果写成RC模型结果。
''',
        'EXTERNAL_THERMAL_SENSOR': f'''### 6.7 外部实测温度与误差裕量决策

使用牛津实测样例[8—9]，对象为40℃温箱内740 mAh电芯，并非本车电池。放电电流由mA转A，拟合$\\dot T=aI^2-b(T-40)$的正参数，仅用前60%样本训练，后40%连续预测且不以实测温度重置。后段RMSE为{s['external_temperature_holdout_rmse_c']['thermal_I_squared']:.3f}℃，前段末值保持及前段均温基线分别为{s['external_temperature_holdout_rmse_c']['train_end_persistence']:.3f}、{s['external_temperature_holdout_rmse_c']['train_mean']:.3f}℃。该简化模型未优于简单基线，因此不迁移参数，也不将其包装为本车热精度验证；目前无本车/座舱实测温度。

针对测温问题，采用保留失败、舒适温区22—26℃、读数目标留1.1℃裕量及终态余热核算的处理方案；真实24±0.1℃保留为探索条件。另以读数直接反馈、相同到达前冷却，扫描±1℃固定偏差叠加±0.05℃噪声，在三环境和五种热失配情形中做60个单策略试验，{s['guarded_practical_pass']}/60满足移除严格座舱终温后的其余约束，严格终温0/60通过。保留−10℃、座舱导热+20%、正偏差下优化策略的舒适性失败，不声称鲁棒认证。29/30配对双方可行；将剩余温差及双方误差预算加入理想热修正后，{s['practical_and_positive_ideal_adjusted_pairs']}/30同时可行且仍有正节省，低温10组修正后均无正收益。此修正依赖热容、COP和工程误差假设，不是实际硬件能耗上界，也不重复叠加到已含实际停车恢复的旧总账。
'''}


def publish():
    s = collect()
    figures()
    failures = s['sensor_failures']
    ftext = '\n'.join(f"- 环境{f['ambient_c']:g}℃，{f['case']}，偏差{f['sensor_bias_c']:g}℃，{f['strategy']}；未通过项："+', '.join(k for k, v in f['constraints'].items() if not v and k != 'cabin_terminal') for f in failures)
    text = f'''# 五项问题的试验结果与当前选择

这轮完成了外部数据试用、测温处理试验和新电压模型比较。结论：**电压方案有改善；滚阻与本车真实热精度未解决；容量透明双口径；测温采用误差裕量并保留失败。**

## 1. 滚动阻力：试过，暂不替换

EPA的原始458行是测试记录。按预先规定的重量范围及配置去重等规则得到10个配置，不能称为10款本车同类纯电车；Electricity也可能含混动车电动模式。使用Target A/B/C，未混用测功机Set系数。

公式为$F=A+Bv+Cv^2$。统一换成N和m/s后，A对应的等效常数系数中位数为{s['road_external_proxy']:.6f}。它包含轮胎之外的阻力，所以只是外部参照。

固定此值再重新训练其余参数，训练综合误差增加{s['road_training_cost_increase_pct']:.2f}%；575能耗APE由5.41%升至11.53%，596由1.44%升至4.14%。**不用这个值替换本车Cr。** 该比较提示原低速阻力偏低，但不同车的数据不能证明本车真值。道路坡度、传动损耗、附件负载和电池模型也可能影响参数，当前不作单一归因。

## 2. 容量：透明处理已落实

160 Ah×375 V约60 kWh，与30 kWh有冲突。按用户确认：问题一保留160 Ah参考，80 Ah作为30 kWh能量等效对照，正文披露矛盾。两者不能解释成已证实的“标称/可用”关系。

25℃CLTC初始SOC80%时，两种情形能耗均约14.0542 kWh/100 km，终SOC分别约76.53%与73.04%。只说明这个工况，不能推广为所有控制与长程不受影响。辨识得到的有效容量也不当作官方容量勘误。

## 3. 真实温度：实际试用，未达到要求

下载并解析了牛津原始电芯温度、电流、电压。它来自740 mAh的另一种电芯、40℃温箱，不能直接换算成本题电池包温度。

试验用前60%动态放电数据训练单节点热模型，后40%连续预测，没有用后段真实温度修正预测。后段RMSE：物理简模{s['external_temperature_holdout_rmse_c']['thermal_I_squared']:.3f}℃；沿用训练末温度{s['external_temperature_holdout_rmse_c']['train_end_persistence']:.3f}℃；沿用训练均温{s['external_temperature_holdout_rmse_c']['train_mean']:.3f}℃。**简模没有优于简单基线，不迁移其参数，也不再加复杂度强行拟合。** 这里只有同一电芯同一循环的后段检查，连独立电芯验证都未达到，更无法证明本车0.1℃精度。

![牛津温度试验](../records/experiments/extension_v1/figures/oxford_temperature.png)

## 4. 温度传感器偏差：建议现在就这样处理

你目前做的是仿真，附件没有真实温度，因此没有依据反推出真实传感器偏差。**论文采用“误差范围内保持舒适”，把真实24±0.1℃作为探索目标，不作为必须实现的承诺。**

具体做法：假设偏差±1℃、噪声±0.05℃；目标读数留1.1℃裕量；基准和优化策略使用相同误差与相同控制边界；比较能耗时另查末温差和未消除的热量。比如读数24℃，在这个假设下，真实温度可能约22.95—25.05℃，应判断是否仍处舒适范围。

“±1℃”是工程试验设定，未被真实校准证明。原观测器仍保留作对照，未知乘员热与传感器偏差可能互相冒充，不把估出的偏差当成已查明的真值。

新60组试验中，{s['guarded_practical_pass']}/60满足舒适性、电池温度、启停间隔、功率和原电池终态条件；真实24±0.1℃为0/60。仍失败的案例：

{ftext}

30个配对中29个双方满足以上条件。额外计入剩余温差和传感器误差预算的理想热修正后，20个配对仍有正节省，这20个也都满足上述配对条件；低温10个配对均不再有正节省。**低温收益暂不作为稳健节能结论，低温舒适性失败照实保留。** 热修正依赖模型和COP，不是实际停车耗电的替代，也不是硬件上界认证。

将来若真做实物，再用有溯源精度的参考温度计，做至少两温度点的同位置、同时间对照，分离零偏、斜率与滞后；当前不要求你补不存在的官方数据。

## 5. 电压：采用表现更好的一阶RC方案

通俗地说，旧模型只计“电流一来立刻压降”；新模型还计“持续用电后积累、停用后慢慢恢复的压降”。这就是RC支路表示的极化。它仍根据车速和负载自己计算电流、SOC与电压，不逐时拿真实电流充当预测。

先处理前四项，再只用575/596训练。两个初值按训练目标选解并冻结，然后算588/600；两段数据以前都看过，所以称回顾评价。

{voltage_table(s)}

**本轮推荐电压预测用一阶RC。** 它在两个路程都比OCV缩放方案的电压RMSE降低超过10%；能耗APE略降，SOC与原模型差异小于预设0.2个百分点。原Rint及OCV缩放两版全部保留。

![三模型电压比较](../records/experiments/extension_v1/figures/voltage_comparison.png)

参数：$R_0=0.005000$ Ω、$R_p=0.204019$ Ω、时间常数约7.53 s；有效容量71.83 Ah、OCV缩放0.57923。Cr与R0仍贴搜索下界，不能宣称物理参数被准确辨识。7项解析检查及4路步长检查通过，子步最大电气守恒残差约1.4×10⁻¹¹ W。RC电容会储能，程序已把这部分与发热分开核算。

**使用范围：** 推荐用于本轮电压预测比较。旧问题一、节能控制、长程和参数区间仍由各自冻结模型支持；没有重做RC版控制寻优及300次区间，不能把旧节能率标成RC结果。低SOC、不同车辆和真实温度仍缺独立验证。

## 文件与复现

全部新实验位于`records/experiments/extension_v1/`，原数据、预运行协议和哈希、冻结参数、60组试验和所有失败均保留；输入附外部对象说明及牛津原README/许可。

```powershell
python scripts/run_extension.py
python scripts/recompute_extension.py
```

第一条核对并读取已有结果、重建说明和工作台；结果缺失时按协议运行。第二条建立隔离副本，从输入完整重算本轮实验并逐项核对。原`python reproduce.py all`仍复现旧主实验。

## 外部引用

EPA. Data on Cars Used for Testing Fuel Economy, 2026 Test Car List（2026-01-21版），https://www.epa.gov/compliance-and-fuel-economy-data/data-cars-used-testing-fuel-economy 。

Birkl C. R., Howey D. A. Oxford Battery Degradation Dataset 1, 2017, DOI:10.5287/bodleian:KO2kdmYGg；Birkl, Diagnosis and Prognosis of Degradation in Lithium-Ion Batteries, Oxford PhD thesis, 2017。ODC-ODbL1.0/DbCL1.0，原README与版权说明保留。
'''
    (PROJECT_ROOT/'docs/五项问题的试验结果与处理决定.md').write_text(text, encoding='utf-8')
    (PROJECT_ROOT/'results/阶段十进展.md').write_text('# 阶段十进展\n\n完成外部路阻与温度数据试用、60组误差裕量试验及新一阶RC比较。详细说明见[五项问题的试验结果与处理决定](../docs/五项问题的试验结果与处理决定.md)。\n\n原冻结模型、66项论文证据及官方9文件保持不变。推荐电压预测用新RC；外车滚阻替换及外部温度迁移未成功。传感器偏差没有被真实校准，59/60与低温不稳结果如实保留。\n', encoding='utf-8')
    return s
