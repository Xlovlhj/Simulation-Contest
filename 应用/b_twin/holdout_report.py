"""Audit all frozen cross-day runs and report every result without selection."""
# AI-assisted: OpenAI Codex; retain actual usage and human review records.
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP
from .runner import digest
from .holdout import PROTOCOL, assert_frozen, suite_dir, make_plan, save_json, latest_revisions
from .evaluation import aggregate_e1, primary_metrics


def report_holdout(prefix='stage10_main'):
    suite = suite_dir(prefix)
    manifest = assert_frozen(suite)
    output = APP / 'results' / (prefix + '_report')
    output.mkdir(exist_ok=True)
    extraction = json.loads((suite / 'extraction.json').read_text(encoding='utf-8'))
    assert digest(suite / 'observation_revisions.csv.gz') == extraction['stream_sha256']
    assert digest(suite / 'validation_15min.csv') == extraction['truth_sha256']
    stream = pd.read_csv(suite / 'observation_revisions.csv.gz')
    final = latest_revisions(stream)
    assert not final.duplicated(['detector_id', 'observation_start', 'observation_end']).any()
    all_rows, audit, windows = [], [], []
    total_decisions = used_constraints = 0
    for case in PROTOCOL['cases']:
        for policy in PROTOCOL['policies']:
            for seed in PROTOCOL['seeds']:
                run = APP / 'results' / f'{prefix}_{case}_s{seed}_{policy}'
                meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
                metrics = json.loads((run / 'metrics.json').read_text(encoding='utf-8'))
                assert meta['status'] == 'completed'
                assert meta['protocol'] == PROTOCOL
                for name in ['network.net.xml', 'detectors.add.xml', 'network_definition.json']:
                    assert digest(run / name) == digest(suite / 'frozen_inputs' / name), (run.name, name)
                assert digest(run / 'demand.rou.xml') == meta['demand_sha256']
                assert digest(run / 'vehicles.csv') == meta['vehicle_manifest_sha256']
                # Recalculate the complete future plan, not just selected logs.
                agents, segments, decisions, constraints = make_plan(suite, case, policy, seed)
                actual = pd.read_csv(run / 'vehicles.csv', dtype={'origin_edge': str, 'original_destination_edge': str})
                pd.testing.assert_frame_equal(agents.reset_index(drop=True), actual.reset_index(drop=True),
                                              check_dtype=False, rtol=1e-10, atol=1e-9)
                saved_segments = pd.read_csv(run / 'demand_segments.csv')
                pd.testing.assert_frame_equal(segments, saved_segments, check_dtype=False, rtol=1e-10, atol=1e-9)
                assert decisions == json.loads((run / 'decisions.json').read_text(encoding='utf-8'))
                total_decisions += len(decisions)
                if policy == 'rolling':
                    selected = constraints[constraints.constraint_used]
                    saved = pd.read_csv(run / 'constraints.csv', dtype={'sumo_edge': str})
                    pd.testing.assert_frame_equal(constraints, saved, check_dtype=False, rtol=1e-10, atol=1e-9)
                    used_constraints += len(selected)
                    for row in selected.itertuples():
                        decision = pd.Timestamp(decisions[row.slot]['decision_at'])
                        assert pd.Timestamp(row.latest_available_at) <= decision
                        assert pd.Timestamp(row.last_observation_end) <= decision
                        assert row.coverage >= .8 and row.age_seconds <= 600
                        assert not row.requires_review
                else:
                    assert all(d['constraints'] == 0 for d in decisions)
                demand = ET.parse(run / 'demand.rou.xml').getroot()
                vehicles = demand.findall('vehicle')
                assert [v.get('id') for v in vehicles] == agents.vehicle_id.tolist()
                assert np.allclose([float(v.get('depart')) for v in vehicles], agents.depart, rtol=0, atol=1e-9)
                route_ids = set(agents.route_id)
                assert set(v.get('route') for v in vehicles).issubset(route_ids)
                for vehicle, a in zip(vehicles, agents.itertuples()):
                    assert vehicle.get('route') == a.route_id
                    assert vehicle.get('type') == f'stream_{a.base_type}_{round(a.speed_factor_assumed*1000)}'
                expected_start = pd.Timestamp(meta['physical_start']).tz_localize(None).isoformat()
                _, simulated = aggregate_e1(run / 'e1.xml', expected_start, meta['evaluation_seconds'])
                saved_sim = pd.read_csv(run / 'simulated_15min.csv')
                pd.testing.assert_frame_equal(simulated, saved_sim, check_dtype=False, rtol=1e-10, atol=1e-9)
                compared = pd.read_csv(run / 'comparison_15min.csv')
                recalculated = primary_metrics(compared)
                for k in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points']:
                    assert np.isclose(recalculated[k], metrics['primary_metrics'][k], atol=1e-9)
                assert metrics['accounting']['conservation_residual'] == 0
                stats = ET.parse(run / 'statistics.xml').getroot()
                assert int(stats.find('teleports').get('total')) == metrics['teleports']
                assert int(stats.find('safety').get('collisions')) == metrics['collisions']
                m = metrics['primary_metrics']
                t = metrics['trip_accounting']
                row = {'run_id': run.name, 'case': case, 'policy': policy, 'seed': seed,
                       **{k: m[k] for k in ['station_windows', 'stations', 'flow_wmape_pct', 'speed_mae_kmh',
                          'occupancy_mae_percentage_points', 'observed_slow_station_windows', 'reproduced_slow_station_windows',
                          'missed_slow_station_windows', 'false_slow_station_windows', 'speed_missing_simulated_windows']},
                       'planned': t['planned'], 'pending_at_horizon': t['pending_at_horizon'],
                       'running_at_horizon': t['running_at_horizon'], 'unfinished_after_clearance': t['unfinished_after_clearance'],
                       'teleports': metrics['teleports'], 'collisions': metrics['collisions'],
                       'conservation_residual': metrics['accounting']['conservation_residual'],
                       'passed_joint_check': metrics['passed_joint_check'],
                       'error_checks_passed': metrics['joint_check']['passed_error_checks']}
                all_rows.append(row)
                compared['run_id'] = run.name
                compared['case'] = case
                compared['policy'] = policy
                compared['seed'] = seed
                windows.append(compared)
                audit.append({'run_id': run.name, 'vehicles_recomputed': len(actual),
                              'decisions_recomputed': len(decisions), 'network_equal_to_frozen': True,
                              'vehicle_sha256': digest(run / 'vehicles.csv'),
                              'comparison_sha256': digest(run / 'comparison_15min.csv')})
    runs = pd.DataFrame(all_rows)
    runs.to_csv(output / 'all_runs.csv', index=False, encoding='utf-8-sig')
    pd.concat(windows, ignore_index=True).to_csv(output / 'all_station_windows.csv.gz', index=False, compression='gzip')
    summary = []
    for (case, policy), g in runs.groupby(['case', 'policy'], sort=False):
        row = {'case': case, 'policy': policy, 'runs': len(g), 'passed_runs': int(g.passed_joint_check.sum())}
        for name in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points', 'reproduced_slow_station_windows',
                     'observed_slow_station_windows', 'false_slow_station_windows', 'planned', 'unfinished_after_clearance']:
            row[name + '_mean'] = float(g[name].mean())
            row[name + '_minimum'] = float(g[name].min())
            row[name + '_maximum'] = float(g[name].max())
        summary.append(row)
    summary = pd.DataFrame(summary)
    summary.to_csv(output / 'summary.csv', index=False, encoding='utf-8-sig')
    quality = json.loads((suite / 'extraction.json').read_text(encoding='utf-8'))
    verification = {'actual_runs': len(runs), 'runs_checked': len(runs), 'all_plans_recomputed': True, 'all_inputs_verified': True,
        'decisions_checked': total_decisions, 'used_constraints_checked': used_constraints,
        'all_zero_teleports': bool(runs.teleports.eq(0).all()), 'all_zero_collisions': bool(runs.collisions.eq(0).all()),
        'all_conservation_zero': bool(runs.conservation_residual.eq(0).all()),
        'passed_joint_runs': int(runs.passed_joint_check.sum()),
        'traffic_baseline_validated': bool(runs.passed_joint_check.all()),
        'note': 'Engineering verification passing is separate from traffic validation; one Saturday cannot establish high-peak generalization.'}
    save_json(output / 'verification.json', verification)
    save_json(output / 'audit_manifest.json', {'freeze': manifest, 'extraction': quality, 'runs': audit})
    plot(summary, output)
    write_report(summary, runs, verification, quality, output, prefix)
    return {'report': str(output), **verification}


def plot(summary, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6))
    labels = [('普通' if r.case == 'ordinary' else '08—09时段') + '\n' + ('冻结先验' if r.policy == 'prior' else '滚动更新')
              for r in summary.itertuples()]
    for ax, name, title, limit in zip(axes,
            ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points'],
            ['流量WMAPE（%）', '速度MAE（km/h）', '占有率MAE（百分点）'], [15, 5, 5]):
        values = summary[name + '_mean'].to_numpy()
        low = values - summary[name + '_minimum'].to_numpy()
        high = summary[name + '_maximum'].to_numpy() - values
        ax.bar(range(len(summary)), values, yerr=[low, high], capsize=4,
               color=['#2563eb' if x == 'prior' else '#0891b2' for x in summary.policy])
        ax.axhline(limit, ls='--', color='#b45309', label='预设检查线')
        ax.set_xticks(range(len(summary)), labels, fontsize=9)
        ax.set_title(title)
        ax.set_ylim(bottom=0)
        ax.grid(axis='y', alpha=.2)
        ax.legend(fontsize=9)
    fig.suptitle('10月17日异日验证：均值及三种子范围（非置信区间）')
    fig.tight_layout()
    fig.savefig(output / 'holdout_errors.png', dpi=170)
    plt.close(fig)


def write_report(summary, runs, verification, quality, output, prefix):
    lines = ['# 第十轮：冻结后异日验证', '',
        '验证日2026-10-17（周六），先验来自15日（周四）。时段、输入和评价规则在读取该日交通数值前冻结。', '',
        f'12次运行中，{verification["passed_joint_runs"]}/12通过预设联合检查；真实高峰能力仍不能据此认定。', '',
        '## 主要结果', '',
        '下表为三个新种子的均值。速度/占有率/低速同时核对；原高峰钟点不自动等于周六真实高峰。', '',
        '| 时段 | 输入 | 流量WMAPE | 速度MAE | 占有率MAE | 低速复现/观测 | 错报 | 联合通过 |',
        '|---|---|---|---|---|---|---|---|']
    for r in summary.itertuples():
        lines.append(f'| {"11:15—12:00" if r.case == "ordinary" else "08:00—09:00"} | '
                     f'{"15日冻结先验" if r.policy == "prior" else "历史滚动更新"} | '
                     f'{r.flow_wmape_pct_mean:.2f}% | {r.speed_mae_kmh_mean:.2f} km/h | '
                     f'{r.occupancy_mae_percentage_points_mean:.2f}个百分点 | '
                     f'{r.reproduced_slow_station_windows_mean:g}/{r.observed_slow_station_windows_mean:g} | '
                     f'{r.false_slow_station_windows_mean:g} | {r.passed_runs}/{r.runs} |')
    lines += ['', '![异日三指标误差](holdout_errors.png)', '',
        '检查线为项目自定：流量≤15%、速度≤5km/h、占有率≤5个百分点；有低速时召回≥80%，错报≤10%；'
        '可比核心站窗≥50%、无模拟速度缺失，且0瞬移/0碰撞/守恒0。它们不是赛题官方标准。', '',
        '本轮普通时段有1个合格低速站窗，两个输入方案三个种子均漏报；08—09时段没有合格低速站窗，'
        '不能作为真实拥堵验证。滚动更新没有整体改善流量误差；只能证明数据到计划的链路可检查，不能证明需求或路况已识别。', '',
        '## 数据与时序', '',
        f'官方压缩包包含{quality["archive_members"]}份XML；保存{quality["station_revisions"]:,}条站点修订、'
        f'{quality["unique_station_periods"]:,}条最终站点时期。全天15分钟表{quality["station_windows"]}窗，'
        f'其中{quality["eligible_windows"]}窗满足固定覆盖规则。', '',
        '观测只在完整车道同时有效时合计；15分钟按实际有效时长扩展。缺测不补零。'
        '最终修订用于评价；每次滚动决策先取当时已发布的最新修订，再判断有效性。无合格数据保持上次计划。', '',
        f'已重新计算全部{verification["decisions_checked"]}次计划、'
        f'{verification["used_constraints_checked"]}条使用约束和全部车辆清单；核对网络、类型、路径、时刻和指标。', '',
        'prior的车辆计划不读取17日观测，属于异日开环验证。rolling每300秒只读取当时归档已发布的旧观测，'
        '属于历史时序跟踪；计划先离线生成再批量仿真，不冒充新一轮实时运行。两种输入不同，不计算治理收益。', '',
        '## 全部运行及核算', '',
        '| 时段/输入 | 种子 | 计划车辆 | 评价末待发/在网 | 清空末未完成 | 瞬移/碰撞/守恒差 |',
        '|---|---|---|---|---|---|']
    for r in runs.itertuples():
        lines.append(f'| {r.case}/{r.policy} | {r.seed} | {r.planned} | {r.pending_at_horizon}/{r.running_at_horizon} | '
                     f'{r.unfinished_after_clearance} | {r.teleports}/{r.collisions}/{r.conservation_residual} |')
    lines += ['', '原始急刹等警告保存在各运行warnings.log，不因瞬移/碰撞为零而删除。', '',
        '## 结论范围', '',
        '独立日期补上了此前同日先验的信息泄漏缺口，但未补齐真实OD、转向、初始队列、实际配时和下游供给。'
        '15日周四到17日周六的日型差异是本轮的一部分，单日三种子不是三个独立现实样本。', '',
        '若08—09没有足够低速站窗，只能检验该日该时段，不能当作真实拥堵复现通过。'
        '本轮结束后不按这些成绩反复改参数；真实高峰联合验证仍保留缺口。', '',
        '## 复现与证据', '',
        '```powershell', f'python 应用/run.py holdout-suite --prefix {prefix} --jobs 3',
        f'python 应用/run.py report-holdout --prefix {prefix}', '```', '',
        '[全部逐次指标](all_runs.csv)｜[汇总](summary.csv)｜[全部站窗](all_station_windows.csv.gz)｜'
        '[核验](verification.json)｜[输入与源码哈希](audit_manifest.json)', '',
        '[冻结方案](../../../docs/第十轮独立验证方案.md)｜[三问缺口](../../../docs/B题三问开发对照.md)', '']
    (output / '第十轮开发诊断.md').write_text('\n'.join(lines), encoding='utf-8')
