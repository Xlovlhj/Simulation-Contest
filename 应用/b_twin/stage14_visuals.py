"""Publication figures from the archived extension results; no simulation changes.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
from __future__ import annotations
import gzip
import json
import math
import numpy as np
import pandas as pd
from .paths import APP, DATA
from .road_emergency import definition
import sumolib


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    out = APP / 'results/stage14_overview'
    out.mkdir(exist_ok=True)
    frame = pd.concat([pd.read_csv(APP / f'results/stage14_day202610{d}_suite/all_runs.csv') for d in ['12', '13']], ignore_index=True)
    groups = frame[frame.case.eq('peak')].groupby(['date', 'policy'], sort=False)
    labels = [date[5:] + '\n' + ('冻结先验' if policy == 'prior' else '历史滚动') for date, policy in groups.groups]
    fig, axes = plt.subplots(1, 3, figsize=(11.8, 4.3))
    fields = ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points']
    for ax, field, title, limit in zip(axes, fields, ['流量WMAPE / %', '速度MAE / (km/h)', '占有率MAE / 百分点'], [15, 5, 5]):
        means = np.array([x[field].mean() for _, x in groups])
        low = means - np.array([x[field].min() for _, x in groups])
        high = np.array([x[field].max() for _, x in groups]) - means
        ax.bar(range(4), means, yerr=[low, high], capsize=4, color=['#2563eb', '#0891b2'] * 2)
        ax.axhline(limit, color='#b45309', ls='--', lw=1, label='项目预设检查线')
        ax.set_xticks(range(4), labels, fontsize=9)
        ax.set_title(title, fontsize=11)
        ax.grid(axis='y', alpha=.15)
        ax.set_axisbelow(True)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=8)
    fig.suptitle('新增工作日高峰检验：三个种子均值及范围（非置信区间）', fontsize=12)
    fig.tight_layout()
    for suffix in ['png', 'svg']:
        fig.savefig(out / ('workday_errors.' + suffix), dpi=190)
    plt.close(fig)

    net = sumolib.net.readNet(str(APP / 'models/emergency_v1/corridor.net.xml'))
    with gzip.open(DATA / 'processed/official_centerline.geojson.gz', 'rt', encoding='utf-8') as h:
        official = json.load(h)['features']
    d = definition()
    fig, ax = plt.subplots(figsize=(7.6, 6.3))
    ax.add_collection(LineCollection([f['geometry']['coordinates'] for f in official], colors='#d1d5db', linewidths=.7))
    all_edges = [[net.convertXY2LonLat(*p) for p in e.getShape()] for e in net.getEdges()]
    ax.add_collection(LineCollection(all_edges, colors='#8db7cc', linewidths=1))
    route = [[net.convertXY2LonLat(*p) for p in net.getEdge(e).getShape()] for e in d['route_edges']]
    ax.add_collection(LineCollection(route, colors='#b45309', linewidths=3))
    for label, text, color, offset in [('origin', '北侧起始断面', '#2563eb', (13, 5)), ('target', '下游目标断面', '#b45309', (13, -16))]:
        c = d['coordinates'][label]
        ax.scatter(c['longitude'], c['latitude'], s=60, color=color, edgecolor='white', zorder=5)
        ax.annotate(text, (c['longitude'], c['latitude']), xytext=offset, textcoords='offset points', fontsize=10)
    ax.set_xlim(114.170, 114.185)
    ax.set_ylim(22.3015, 22.317)
    ax.set_aspect(1 / math.cos(math.radians(22.31)))
    ax.set_xlabel('经度 / °E')
    ax.set_ylabel('纬度 / °N')
    ax.ticklabel_format(useOffset=False)
    ax.grid(alpha=.15)
    ax.legend(handles=[Line2D([0], [0], color='#d1d5db', label='官方道路中心线'),
        Line2D([0], [0], color='#8db7cc', label='SUMO网络'), Line2D([0], [0], color='#b45309', lw=3, label='设计道路通行任务')], loc='upper left', fontsize=9)
    ax.set_title('道路应急任务：外部边长合计1.063 km', fontsize=12)
    fig.text(.5, .015, '不含路口内部连接长度；位置符号不代表真实驻点、停车许可或救护入口。', ha='center', fontsize=9)
    fig.tight_layout(rect=[0, .035, 1, 1])
    for suffix in ['png', 'svg']:
        fig.savefig(out / ('road_task_map.' + suffix), dpi=190)
    plt.close(fig)

    paired = pd.read_csv(APP / 'results/stage14_road_emergency_suite/paired_changes.csv')
    paired = paired[paired.policy.eq('guarded_green')]
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 4.2))
    x = np.arange(3)
    axes[0].bar(x - .18, paired.reference_task_seconds, .36, label='无协同', color='#94a3b8')
    axes[0].bar(x + .18, paired.candidate_task_seconds, .36, label='稀疏让行＋已绿延长', color='#0891b2')
    axes[0].set_ylabel('从调度到道路终点 / 秒')
    axes[0].legend(fontsize=8)
    axes[1].bar(x, paired.ordinary_change_pct, color=['#b45309' if v > 0 else '#0891b2' for v in paired.ordinary_change_pct])
    axes[1].axhline(0, color='#64748b', lw=.8)
    axes[1].set_ylabel('全部普通车辆固定截止成本变化 / %')
    for ax in axes:
        ax.set_xticks(x, paired.seed.astype(str), fontsize=9)
        ax.grid(axis='y', alpha=.15)
        ax.set_axisbelow(True)
        ax.set_xlabel('设计情景随机种子')
    fig.suptitle('道路任务9次均到达：2组更快、1组不变；普通交通存在代价', fontsize=11)
    fig.text(.5, .015, '本轮绿灯延长未触发，组合与单独稀疏让行结果相同；三个种子不是三次真实救援。', ha='center', fontsize=9)
    fig.tight_layout(rect=[0, .035, 1, 1])
    for suffix in ['png', 'svg']:
        fig.savefig(out / ('road_task_results.' + suffix), dpi=190)
    plt.close(fig)
    return {'output': str(out), 'figures': ['workday_errors', 'road_task_map', 'road_task_results']}


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False))
