"""Build the bounded-optimization result brief and final-writing evidence index.

AI-assisted: OpenAI Codex; all values are read from audited experimental files.
"""
from pathlib import Path
import json
import shutil
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .paths import APP, PROJECT
from .stress import load
from .runner import digest
from .baseline_optimization import CANDIDATES as CALIBRATION_CANDIDATES


def main():
    verification=load(PROJECT/'交付准备/第十六轮核验.json')
    waiting=APP/'results/stage16_waiting_suite'
    calibration=APP/'results/stage16_calibration_suite'
    emergency=APP/'results/stage16_emergency_scope_suite'
    decisions=load(waiting/'development_decision.json')
    cal=pd.read_csv(calibration/'training_results.csv')
    scope=pd.read_csv(emergency/'paired_changes.csv')
    ed=load(emergency/'decision.json')
    assert verification['waiting_selection']['adopted'] is None
    assert verification['calibration_selection']['selected_before_holdout'] is None
    output=APP/'results/stage16_overview'
    output.mkdir(exist_ok=True)
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'], 'axes.unicode_minus':False})
    fig,axes=plt.subplots(1,2,figsize=(10,4.3),layout='constrained')
    names=list(decisions)
    axes[0].bar(names,[decisions[n]['selection_score_background_mean_pct'] for n in names],color='#237b68')
    axes[1].bar(names,[decisions[n]['worst_pair_total_change_pct'] for n in names],color='#ad623e')
    axes[0].set(title='背景成本变化：14组设计选型均值',ylabel='相对原版变化 / %')
    axes[1].set(title='总成本变化：最差一组',ylabel='相对原版变化 / %')
    for ax in axes:
        ax.axhline(0,color='#6a7682',lw=.8)
        ax.grid(axis='y',alpha=.2)
    fig.suptitle('等待保护降低背景代价，但部分条件下损害总成本')
    for ext in ['png','svg']:
        fig.savefig(output/('waiting_tradeoff.'+ext),dpi=180)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4.4),layout='constrained')
    axes[0].barh(cal.candidate,cal.score,color=['#637b86' if n=='original' else '#237b68' for n in cal.candidate])
    original=float(cal.loc[cal.candidate.eq('original'),'score'].iloc[0])
    axes[0].axvline(original*.9,color='#ad623e',ls='--',label='至少改善10%的分数线')
    axes[0].set(title='训练日多指标分数（越低越好）',xlabel='预设分数，无量纲')
    axes[0].legend(fontsize=8)
    points=axes[1].scatter(cal.flow_wmape_pct,cal.speed_mae_kmh,c=cal.occupancy_mae_percentage_points,cmap='viridis',s=55)
    fig.colorbar(points,ax=axes[1],label='占有率MAE / 百分点',shrink=.85)
    for r in cal.itertuples():
        if r.candidate in ['original','bgs600','bgs900','bgs1200']:
            axes[1].annotate(r.candidate,(r.flow_wmape_pct,r.speed_mae_kmh),fontsize=8,xytext=(3,3),textcoords='offset points')
    axes[1].set(title='降速也可能增加流量偏差',xlabel='流量WMAPE / %',ylabel='速度MAE / km/h',xlim=(-1,30))
    for ax in axes:
        ax.grid(alpha=.2)
    for ext in ['png','svg']:
        fig.savefig(output/('calibration_tradeoff.'+ext),dpi=180)
    plt.close(fig)
    lines=[]
    for key,label in [('wait120','120秒'),('wait240','240秒')]:
        d=decisions[key]
        lines.append(f"| {label}保护 | {d['selection_score_background_mean_pct']:+.2f}% | {d['worst_pair_total_change_pct']:+.2f}% | {d['worst_condition_mean_unfinished_change']:+.1f}辆 | 未采用 |")
    wait_table='\n'.join(lines)
    cal_lines=[]
    for r in cal.itertuples():
        ridge,bg,supply,tau=CALIBRATION_CANDIDATES[r.candidate]
        configuration=f'{ridge:g}/{bg}/{supply}/' + ('默认' if tau is None else str(tau))
        cal_lines.append(f'| {r.candidate}：{configuration} | {r.flow_wmape_pct:.2f}% | {r.speed_mae_kmh:.2f} | {r.occupancy_mae_percentage_points:.2f} | {r.reproduced_slow_station_windows}/5 | {r.score:.3f} |')
    cal_table='\n'.join(cal_lines)
    extra=[]
    for c,g in scope[scope.contrast.eq('AB_vs_A')].groupby('condition'):
        d=ed['conditions'][c]
        extra.append(f"| {c} | {g.task_change_pct.mean():+.2f}% | {g.task_change_pct.max():+.2f}% | {g.ordinary_change_pct.mean():+.2f}% | {'可在该设计条件下使用' if d['extra_coordination_accepted'] else '未达采用标准'} |")
    extra_table='\n'.join(extra)
    none_extra=not any(d['extra_coordination_accepted'] for d in ed['conditions'].values())
    conclusion='三项均没有达到预设采用标准，保留第十五轮正确单位的原方案。' if none_extra else '等待保护和基准均未采用；应急分支仅在达到预设标准的设计条件内解释，不替换全部情景。'
    md=f'''# 有限优化结果与定稿决定

2026-10-18。**{conclusion}** 这表示本轮有限候选没有提供可靠替换依据，不表示已证明全局最优。

## 结果先看这张表

| 方向 | 实际尝试 | 结果 | 决定 |
|---|---|---|---|
| 入口等待保护 | 120/240秒，两种子×7条件，共28次 | 背景成本降低，但部分条件总成本和未完成量明显增加 | 保留原入口控制 |
| 高峰联合校准 | 12组训练日参数组合，固定同一种子 | 没有候选满足分数及误差约束 | 保留原基准；14日未读取 |
| 应急适用范围 | 轻/中/重单车道作业×3种子×全绿/A/AB，共27次 | 额外协同按下表逐条件判断 | {'不增加额外协同收益主张' if none_extra else '仅保留通过条件的有限结论'} |

## 等待保护：减少谁的等待，也可能增加谁的等待

| 候选 | 背景成本平均变化 | 最差总成本变化 | 最差条件平均未完成增量 | 决定 |
|---|---|---|---|---|
{wait_table}

普通成本统一08—09，含全部计划车辆入口外等待及未完成。跨7条件的平均仅用于预先定义的选型分数，不是现实总体收益。未完成量为共同清空截止时的数量，不与一小时成本混作同一指标。

每30秒监测已到计划时刻的待发背景车、进路背景车累计停车加入口外等待，另限制连续控制时长；超线释放全绿。阈值是保护触发线，不保证车辆最终等待不超限。原版作为对照，车辆/道路/需求字节逐项一致；所有28次保留。由于开发已不合格，没有浪费新种子复验来尝试找好成绩。

控制使用TraCI步末接收的插入事件，较XML步起点实际出发保守0.5秒；最终成本及等待统计按XML实际出发核算。高分位/最大等待另存[逐组表](../应用/results/stage16_waiting_suite/development_paired.csv)，是全运行停车加入口外等待的删失下界，不是完整行程耗时，也不能假称待发车已经完成。

![等待保护的权衡](../应用/results/stage16_overview/waiting_tradeoff.png)

## 基准：改进单项不等于整体准确

组合名后依次为入口正则权重/背景流量/名义下游供给/驾驶时间间隔。流量单位为辆/小时，时间间隔单位为秒；供给0表示自由出口，不是道路容量为0。

| 组合 | 流量WMAPE | 速度MAE km/h | 占有率MAE 百分点 | 低速复现 | 联合分数 |
|---|---|---|---|---|---|
{cal_table}

只用了15日训练观测，原限速不变；候选覆盖入口先验、背景流、下游供给和1.5秒驾驶间隔。分数为流量WMAPE/15＋速度MAE/5＋占有率MAE/5＋低速漏报比例＋错报比例，单位统一后才加权。候选还须满足各项误差不明显退化等限制；最低分改善约{100*(1-cal.score.min()/original):.2f}%，未到10%采用线，且部分误差退化。

12次都完成车辆核算，但工程正确不等于交通精度。训练搜索没有找到合格替代方案，因此未打开14日，没有新增独立精度通过证据。现有高峰和跨日失败结论保留。

![联合校准的权衡](../应用/results/stage16_overview/calibration_tradeoff.png)

## 应急：轻、中、重全部保留

单车道作业限速6/3/1米每秒，事件08:30—08:45、调度08:35，起终点及需求固定。全绿、A和A加原安全协同三组使用同输入；这些是设计条件，不是真实事故样本。

| 条件 | 额外协同任务平均变化 | 最差任务变化 | 普通成本平均变化 | 决定 |
|---|---|---|---|---|
{extra_table}

27次任务全部到达：{ed['all_task_arrived']}；让行请求{ed['yield_requests']}次，绿灯延长{ed['green_extensions']}次。命令数不是收益，是否采用依任务和普通交通对照判断。完整[27次结果](../应用/results/stage16_emergency_scope_suite/all_runs.csv)及[18组对照](../应用/results/stage16_emergency_scope_suite/paired_changes.csv)保留。

## 现在是否还要继续优化

**建议停止这一轮调参，进入最终版论文撰写及支撑整理。** 当前数据和计算条件下，已尝试最直接的等待约束、有限联合校准、应急适用范围，没有证据支持靠继续小幅调参获得可靠提升。若继续追求现实精度，主要需要真实转向/汇入、实际配时、初态队列及下游供给等新约束，单纯增加种子或调整指标不能补出这些数据。

不能写“已经没有任何优化可能”或“已达到最优”。论文应写清适用范围、精度失败、等待转移和有限参数搜索；当前原方案是保留的比较基线，不因没有更好候选就成为现实推荐方案。

本轮新增{verification['new_optimization_runs']}次，累计核算{verification['actual_runs']}次，包含历史错误与失败版本，非现实独立样本。新试验核对{verification['vehicle_records_checked']}条跨运行车辆、{verification['waiting_decisions_checked']}条等待决策及{verification['emergency_background_control_decisions_checked']}条应急背景决策，全部0瞬移、0碰撞、守恒差0。

[冻结方案](第十六轮有限优化方案.md) · [最终核验](../交付准备/第十六轮核验.json) · [定稿依据与材料清单](../交付准备/定稿依据与材料清单.md)
'''
    (PROJECT/'docs/第十六轮优化结果与定稿决定.md').write_text(md,encoding='utf-8')
    evidence=PROJECT/'交付准备/定稿依据_第十六轮'
    evidence.mkdir(exist_ok=True)
    files=[PROJECT/'交付准备/第十五轮核验.json',PROJECT/'交付准备/第十六轮核验.json',
        PROJECT/'docs/第十六轮有限优化方案.md',waiting/'development_paired.csv',waiting/'development_decision.json',
        waiting/'final_decision.json',calibration/'training_results.csv',calibration/'selection.json',
        emergency/'all_runs.csv',emergency/'paired_changes.csv',emergency/'decision.json',output/'all_run_audits.csv']
    manifest=[]
    for p in files:
        name=p.relative_to(PROJECT).as_posix().replace('/','__')
        if p.suffix.lower()=='.md':
            name += '.snapshot'
        target=evidence/name
        shutil.copyfile(p,target)
        manifest.append({'original':p.relative_to(PROJECT).as_posix(),'snapshot':name,'sha256':digest(target)})
    (evidence/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_written':True,'evidence_files':len(manifest),'all_original_models_retained':True},ensure_ascii=False))


if __name__=='__main__':
    main()
