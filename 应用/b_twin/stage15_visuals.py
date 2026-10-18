"""Figures for the corrected-unit experiments; no model changes.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
import json
import numpy as np
import pandas as pd
from .paths import APP


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    out = APP / 'results/stage15_overview'
    out.mkdir(exist_ok=True)
    data = pd.read_csv(APP / 'results/stage15_conditional_suite/paired_changes.csv')
    order = ['reference', 'demand080', 'demand120', 'exit_more', 'through_more', 'supply0600', 'supply1200']
    labels = ['参考', '需求−20%', '需求+20%', '侧出增加', '直行增加', '供给600', '供给1200']
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 4.5))
    for ax, field, title in zip(axes, ['ordinary_change_pct', 'background_change_pct'], ['全部普通车辆固定时窗成本变化 / %', '入口背景车辆固定时窗成本变化 / %']):
        groups = [data[data.scenario.eq(s)][field] for s in order]
        means = np.array([g.mean() for g in groups])
        lower = means - np.array([g.min() for g in groups])
        upper = np.array([g.max() for g in groups]) - means
        ax.bar(range(7), means, yerr=[lower, upper], capsize=4,
            color=['#b45309' if v > 0 else '#0891b2' for v in means])
        ax.axhline(0, color='#64748b', lw=.8)
        ax.set_xticks(range(7), labels, rotation=25, ha='right', fontsize=9)
        ax.set_title(title, fontsize=11)
        ax.grid(axis='y', alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle('修复占有率单位后的7条件筛查：三种子均值及范围（非置信区间）', fontsize=12)
    fig.tight_layout()
    for ext in ['png', 'svg']:
        fig.savefig(out / ('corrected_governance.' + ext), dpi=185)
    plt.close(fig)

    road = pd.read_csv(APP / 'results/stage15_road_free_reference_suite/all_three_arm_results.csv')
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.6))
    x = np.arange(3)
    for i, (policy, label, color) in enumerate([('free', '全绿、无协同', '#94a3b8'), ('A_only', '治理A', '#2563eb'), ('A_plus_B', 'A＋额外应急协同', '#0891b2')]):
        g = road[road.policy.eq(policy)].sort_values('seed')
        axes[0].bar(x + (i - 1) * .24, g.rescue_elapsed_lower_bound_s, .24, label=label, color=color)
    axes[0].set_ylabel('从调度到道路目标 / 秒')
    axes[0].legend(fontsize=8)
    g = road[road.policy.eq('A_only')].sort_values('seed')
    axes[1].bar(x - .18, g.ordinary_change_from_free_pct, .36, label='全部普通车成本变化', color='#2563eb')
    axes[1].bar(x + .18, g.background_change_from_free_pct, .36, label='其中背景车成本变化', color='#b45309')
    axes[1].axhline(0, color='#64748b', lw=.8)
    axes[1].set_ylabel('固定截止成本变化 / %')
    axes[1].set_ylim(-15, 95)
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.set_xticks(x, g.seed.astype(str), fontsize=9)
        ax.set_xlabel('设计情景随机种子')
        ax.grid(axis='y', alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle('道路应急：改善主要来自A，背景车代价必须同时报告', fontsize=12)
    fig.text(.5, .015, '三个原定种子全部保留；全绿参照为事后补充诊断，不是真实救援记录。额外协同未触发。', ha='center', fontsize=9)
    fig.tight_layout(rect=[0, .035, 1, 1])
    for ext in ['png', 'svg']:
        fig.savefig(out / ('corrected_road_task.' + ext), dpi=185)
    plt.close(fig)
    return {'figures': ['corrected_governance', 'corrected_road_task']}


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False))
