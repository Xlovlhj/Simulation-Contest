"""Audit every stress input and pair; do not select conditions by outcomes."""
# AI-assisted: OpenAI Codex; retain actual usage and human review for submission.
from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, INPUTS, REFINED
from .runner import digest
from .stress import load, save, shift_late_turns
from .demand import fixed_vehicles
from .governance_report import agent_costs, audit_decisions
from .emergency_report import audit_emergency
from .emergency_guard_report import audit_guard
from .boundary_supply import service_cycle
from .evaluation import WARMUP
import sumolib

LABELS = {'reference': '原设定', 'demand080': '需求−20%', 'demand120': '需求＋20%',
          'exit_more': '多分流', 'through_more': '多直行',
          'supply0600': '供给600', 'supply1200': '供给1200'}


def read_agents(path):
    return pd.read_csv(path, dtype={'origin_edge': str, 'original_destination_edge': str})


def audit_input(directory, meta):
    before = directory / 'pre_stress_inputs'
    for name, sha in load(before / 'hashes.json').items():
        assert digest(before / name) == sha
    protocol = meta['stress_protocol']
    for name, sha in protocol['pre_stress_hashes'].items():
        assert digest(before / name) == sha
    setting = protocol['setting']
    factor = setting['demand_factor']
    net = sumolib.net.readNet(str(directory / 'network.net.xml'))
    root = ET.parse(directory / 'demand.rou.xml').getroot()
    old_root = ET.parse(before / 'demand.rou.xml').getroot()
    by_route = {r.get('id'): r.get('edges').split() for r in root.findall('route')}
    assert by_route == {r.get('id'): r.get('edges').split() for r in old_root.findall('route')}
    core = read_agents(before / 'core_vehicles.csv')
    background = read_agents(before / 'background_vehicles.csv')
    if factor != 1:
        fleet = pd.read_csv(INPUTS / 'vehicle_class_priors.csv')
        fleet = fleet[fleet.case.eq('peak')]
        original_routes = pd.read_csv(INPUTS / 'peak_inferred_routes.csv')
        segments = pd.read_csv(before / 'demand_segments.csv')
        segments['estimated_vph'] *= factor
        original = sumolib.net.readNet(str(REFINED / 'corridor.net.xml'))
        core, _ = fixed_vehicles(original_routes, segments, fleet, original, meta['seed'], 'peak')
        segments = pd.read_csv(before / 'background_segments.csv')
        segments['estimated_vph'] *= factor
        routes = pd.DataFrame([{'route_id': rid, 'direction': 'south', 'edges': ' '.join(by_route[rid])}
                               for rid in segments.route_id.unique()])
        background, _ = fixed_vehicles(routes, segments, fleet, net, meta['seed'], 'peak')
    def allowed(edges, cls):
        return all(net.getEdge(e).allows(cls) for e in edges)
    core, turns = shift_late_turns(core, by_route, setting['through_shift'], meta['seed'], allowed)
    originals = pd.read_csv(INPUTS / 'peak_inferred_routes.csv')
    core['original_destination_edge'] = core.route_id.map({r.route_id: r.edges.split()[-1] for r in originals.itertuples()})
    pd.testing.assert_frame_equal(core, read_agents(directory / 'core_vehicles.csv'),
        check_dtype=False, rtol=1e-12, atol=1e-12)
    pd.testing.assert_frame_equal(background, read_agents(directory / 'background_vehicles.csv'),
        check_dtype=False, rtol=1e-12, atol=1e-12)
    expected = pd.concat([core.assign(population='core'), background.assign(population='background')], ignore_index=True)
    if protocol['family'] == 'emergency':
        initial = read_agents(before / 'vehicles.csv')
        expected = pd.concat([expected, initial[initial.population.eq('emergency')]], ignore_index=True)
    expected = expected.sort_values(['depart', 'vehicle_id']).reset_index(drop=True)
    actual = read_agents(directory / 'vehicles.csv')
    pd.testing.assert_frame_equal(expected, actual, check_dtype=False, rtol=1e-12, atol=1e-12)
    nodes = root.findall('vehicle')
    assert len(nodes) == len(actual) and len({v.get('id') for v in nodes}) == len(actual)
    types = {t.get('id'): t for t in root.findall('vType')}
    for node, row in zip(nodes, actual.itertuples()):
        assert node.get('id') == row.vehicle_id and node.get('route') == row.route_id
        assert float(node.get('depart')) == row.depart
        assert node.get('departLane') == row.depart_lane and node.get('departSpeed') == str(row.depart_speed)
        vtype = types[node.get('type')]
        assert vtype.get('vClass') == row.sumo_vclass
        assert float(vtype.get('length')) == row.length_m_assumed
        assert float(vtype.get('speedFactor')) == row.speed_factor_assumed
        assert float(vtype.get('sigma')) == row.sigma_assumed
        assert allowed(by_route[row.route_id], row.sumo_vclass)
    # The network may differ only in the durations of the numerical exit signal.
    prior_network = ET.parse(before / 'network.net.xml').getroot()
    final_network = ET.parse(directory / 'network.net.xml').getroot()
    node_id = meta['boundary_supply']['node_id']
    original_logic = next(t for t in prior_network.findall('tlLogic') if t.get('id') == node_id)
    changed_logic = next(t for t in final_network.findall('tlLogic') if t.get('id') == node_id)
    plan = service_cycle(setting['supply_vph'], meta['boundary_supply']['lanes'])
    for old, new, key in zip(original_logic, changed_logic, ['green_seconds', 'yellow_seconds', 'red_seconds']):
        assert old.get('state') == new.get('state')
        assert np.isclose(float(new.get('duration')), plan[key])
        new.set('duration', old.get('duration'))
    canonical = lambda tree: ET.canonicalize(xml_data=ET.tostring(tree, encoding='unicode'), strip_text=True)
    assert canonical(prior_network) == canonical(final_network)
    return turns


def report_stress(prefix='stage09_main'):
    if not prefix.replace('_', '').isalnum():
        raise ValueError('Invalid prefix')
    suite = APP / 'results' / (prefix + '_suite')
    spec = load(suite / 'suite.json')['specification']
    for name, sha in spec['source_sha256'].items():
        assert digest(suite / 'frozen_sources' / name) == sha
        assert digest(APP / 'b_twin' / name) == sha
    for name, sha in spec['input_sha256'].items():
        assert digest(APP.parent / name) == sha
    output = APP / 'results' / (prefix + '_report')
    output.mkdir(exist_ok=True)
    source = APP / 'b_twin/stress_report.py'
    review = output / 'review_tools' / digest(source)
    review.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, review / source.name)
    rows, turn_rows, audited = [], [], []
    for i, item in enumerate(spec['runs']):
        directory = APP / 'results' / item['run_id']
        meta = load(directory / 'run.json')
        assert meta['status'] == 'completed' and meta['seed'] == item['seed']
        assert meta['stress_protocol']['scenario'] == item['scenario']
        assert meta['stress_protocol']['setting'] == spec['scenarios'][item['scenario']]
        assert meta['source_sha256'] == spec['source_sha256']
        for name, sha in meta['source_sha256'].items():
            assert digest(directory / 'frozen_sources' / name) == sha
        files = [('network.net.xml', 'network_sha256'), ('demand.rou.xml', 'demand_sha256'),
            ('vehicles.csv', 'vehicle_manifest_sha256'), ('core_vehicles.csv', 'core_vehicle_manifest_sha256'),
            ('meter_plan.json', 'meter_plan_sha256'), ('stress_protocol.json', 'stress_protocol_sha256')]
        if item['family'] == 'emergency':
            files += [('emergency_scenario.json', 'emergency_scenario_sha256'), ('guard_protocol.json', 'guard_protocol_sha256')]
            assert meta['emergency_policy'] == item['policy']
        else:
            assert meta['meter_policy'] == item['policy']
        assert meta['entry_ridge'] == .5 and meta['clearance_seconds'] == 1800
        assert meta['teleport_timeout_seconds'] == -1
        for name, key in files:
            assert digest(directory / name) == meta[key]
        turns = audit_input(directory, meta)
        turn_rows.extend([{**item, **t} for t in turns])
        child = output / item['run_id']
        child.mkdir(exist_ok=True)
        horizon = meta['simulation_end_seconds'] if item['family'] == 'emergency' else None
        cost = agent_costs(directory, child, horizon)
        control = audit_decisions(directory, meta)
        metrics = load(directory / 'metrics.json')
        trips = metrics['trip_accounting']
        route_ends = {r.get('id'): r.get('edges').split()[-1]
                      for r in ET.parse(directory / 'demand.rou.xml').getroot().findall('route')}
        agents = read_agents(directory / 'vehicles.csv').set_index('vehicle_id')
        exit_arrivals = [float(t.get('arrival')) for t in ET.parse(directory / 'tripinfo.xml').getroot().findall('tripinfo')
            if route_ends[agents.loc[t.get('id'), 'route_id']] == '45740459'
            and WARMUP <= float(t.get('arrival')) < WARMUP + meta['evaluation_seconds']]
        row = {**item, 'ordinary_vehicle_hours': cost['core_evaluation_vehicle_hours'] + cost['background_evaluation_vehicle_hours'],
            'core_vehicle_hours': cost['core_evaluation_vehicle_hours'],
            'background_vehicle_hours': cost['background_evaluation_vehicle_hours'],
            'planned': trips['planned'], 'unfinished': trips['unfinished_after_clearance'],
            'pending_at_horizon': trips['pending_at_horizon'], 'running_at_horizon': trips['running_at_horizon'],
            'flow_wmape_pct': metrics['primary_metrics']['flow_wmape_pct'],
            'speed_mae_kmh': metrics['primary_metrics']['speed_mae_kmh'],
            'occupancy_mae_percentage_points': metrics['primary_metrics']['occupancy_mae_percentage_points'],
            'teleports': metrics['teleports'], 'collisions': metrics['collisions'],
            'conservation_residual': metrics['accounting']['conservation_residual'],
            'meter_decisions': control['decisions'], 'queue_overrides': control['queue_overrides'],
            'turn_transfers': meta['stress_protocol']['turn_transfers']}
        row.update(actual_exit_flow_vph=len(exit_arrivals) * 3600 / meta['evaluation_seconds'],
            nominal_supply_vph=meta['residual_supply_vph_scenario'],
            missed_slow_station_windows=metrics['primary_metrics']['missed_slow_station_windows'],
            false_slow_station_windows=metrics['primary_metrics']['false_slow_station_windows'])
        if item['family'] == 'emergency':
            row.update(audit_emergency(directory, meta))
            row.update(audit_guard(directory, meta))
        rows.append(row)
        audited.append({'run_id': item['run_id'], 'input_recomputed': True,
            'run_sha256': digest(directory / 'run.json'), 'metrics_sha256': digest(directory / 'metrics.json'),
            'tripinfo_sha256': digest(directory / 'tripinfo.xml')})
        if (i + 1) % 12 == 0:
            print(json.dumps({'audited': i + 1, 'total': len(spec['runs'])}), flush=True)
    table = pd.DataFrame(rows)
    # A scenario must be a genuine perturbation of the same frozen base, rather
    # than a regenerated or selectively changed base demand for each condition.
    base_files = ['vehicles.csv', 'core_vehicles.csv', 'background_vehicles.csv',
        'demand_segments.csv', 'background_segments.csv', 'demand.rou.xml',
        'demand_manifest.json', 'network.net.xml', 'boundary_supply.json']
    for family, policies in spec['arms'].items():
        for seed in spec['seeds']:
            group = table[table.family.eq(family) & table.seed.eq(seed)]
            base = group[group.scenario.eq('reference') & group.policy.eq(policies[0])].iloc[0]
            reference = APP / 'results' / base.run_id
            for run in group.itertuples():
                directory = APP / 'results' / run.run_id
                for name in base_files:
                    assert (directory / 'pre_stress_inputs' / name).read_bytes() == (reference / 'pre_stress_inputs' / name).read_bytes(), (run.run_id, name)
                if spec['scenarios'][run.scenario]['demand_factor'] == 1:
                    actual = read_agents(directory / 'vehicles.csv')
                    original = read_agents(reference / 'vehicles.csv')
                    unchanged = [c for c in actual.columns if c not in {'route_id', 'original_destination_edge'}]
                    pd.testing.assert_frame_equal(actual[unchanged], original[unchanged], check_dtype=False)
                    if spec['scenarios'][run.scenario]['through_shift'] == 0:
                        for name in ['vehicles.csv', 'demand.rou.xml']:
                            assert (directory / name).read_bytes() == (reference / name).read_bytes()
                if spec['scenarios'][run.scenario]['supply_vph'] == 900:
                    assert (directory / 'network.net.xml').read_bytes() == (reference / 'network.net.xml').read_bytes()
    pairs = []
    for (scenario, seed, family), group in table.groupby(['scenario', 'seed', 'family'], sort=False):
        policies = spec['arms'][family]
        base = group[group.policy.eq(policies[0])].iloc[0]
        treatment = group[group.policy.eq(policies[1])].iloc[0]
        reference = APP / 'results' / base.run_id
        candidate = APP / 'results' / treatment.run_id
        shared = ['network.net.xml', 'demand.rou.xml', 'vehicles.csv', 'boundary_supply.json',
            'meter_plan.json', 'stress_protocol.json', 'core_vehicles.csv', 'background_vehicles.csv']
        if family == 'emergency':
            shared += ['emergency_scenario.json', 'guard_protocol.json']
        for name in shared:
            assert (reference / name).read_bytes() == (candidate / name).read_bytes(), (scenario, seed, family, name)
        row = {'scenario': scenario, 'seed': seed, 'family': family,
            'reference_run': base.run_id, 'candidate_run': treatment.run_id,
            'ordinary_change_pct': 100 * (treatment.ordinary_vehicle_hours / base.ordinary_vehicle_hours - 1),
            'background_change_pct': 100 * (treatment.background_vehicle_hours / base.background_vehicle_hours - 1),
            'core_change_pct': 100 * (treatment.core_vehicle_hours / base.core_vehicle_hours - 1),
            'unfinished_change': int(treatment.unfinished - base.unfinished),
            'reference_unfinished': int(base.unfinished), 'candidate_unfinished': int(treatment.unfinished)}
        if family == 'emergency':
            row.update(task_change_pct=100 * (treatment.rescue_elapsed_lower_bound_s / base.rescue_elapsed_lower_bound_s - 1),
                task_change_seconds=treatment.rescue_elapsed_lower_bound_s - base.rescue_elapsed_lower_bound_s,
                reference_task_completed=bool(base.rescue_completed), candidate_task_completed=bool(treatment.rescue_completed),
                reference_task_seconds=base.rescue_elapsed_lower_bound_s, candidate_task_seconds=treatment.rescue_elapsed_lower_bound_s,
                yield_requests=int(treatment.yield_requests), green_extensions=int(treatment.green_extensions))
        pairs.append(row)
    pairs = pd.DataFrame(pairs)
    assert len(table) == 84 and len(pairs) == 42
    assert table[['teleports', 'collisions', 'conservation_residual']].eq(0).all().all()
    table.to_csv(output / 'all_runs.csv', index=False, encoding='utf-8-sig')
    pairs.to_csv(output / 'paired_changes.csv', index=False, encoding='utf-8-sig')
    turn_table = pd.DataFrame(turn_rows)
    turn_table.to_csv(output / 'turn_reassignments.csv', index=False, encoding='utf-8-sig')
    summaries = []
    for (scenario, family), group in pairs.groupby(['scenario', 'family'], sort=False):
        r = {'scenario': scenario, 'family': family, 'pairs': len(group),
            'ordinary_mean_change_pct': group.ordinary_change_pct.mean(),
            'ordinary_best_change_pct': group.ordinary_change_pct.min(),
            'ordinary_worst_change_pct': group.ordinary_change_pct.max(),
            'ordinary_improved': int(group.ordinary_change_pct.lt(-1e-8).sum()),
            'ordinary_worsened': int(group.ordinary_change_pct.gt(1e-8).sum()),
            'background_worst_change_pct': group.background_change_pct.max(),
            'unfinished_worst_increase': int(group.unfinished_change.max())}
        if family == 'emergency':
            r.update(task_mean_change_pct=group.task_change_pct.mean(), task_best_change_pct=group.task_change_pct.min(),
                task_worst_change_pct=group.task_change_pct.max(), task_improved=int(group.task_change_pct.lt(-1e-8).sum()),
                task_worsened=int(group.task_change_pct.gt(1e-8).sum()), reference_completed=int(group.reference_task_completed.sum()),
                candidate_completed=int(group.candidate_task_completed.sum()))
        summaries.append(r)
    summary = pd.DataFrame(summaries)
    summary.to_csv(output / 'scenario_summary.csv', index=False, encoding='utf-8-sig')
    save(output / 'audit_manifest.json', {'runs': audited, 'report_source_sha256': digest(source)})
    save(output / 'verification.json', {'actual_runs': 84, 'fixed_input_pairs': 42, 'seeds_per_condition': 3,
        'scenarios': 7, 'all_source_and_input_hashes_verified': True, 'all_agent_perturbations_recomputed': True,
        'all_paired_exogenous_inputs_identical': True, 'all_controls_and_emergency_commands_audited': True,
        'all_conditions_share_the_same_pre_stress_base_per_seed': True,
        'all_runs_zero_teleports_collisions_and_conservation_residual': True,
        'designed_one_factor_screening_complete': True, 'global_input_robustness_proved': False,
        'real_peak_baseline_validated': False, 'actual_ambulance_gate_verified': False,
        'competition_delivery_complete': False})
    make_figure(output, pairs, spec['scenarios'])
    write_report(output, table, pairs, summary, turn_table, spec)
    return {'report': str(output), 'actual_runs': 84, 'pairs': 42, 'verified': True}


def make_figure(output, pairs, scenarios):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)
    for ax, family, metric, title in zip(axes, ['governance', 'emergency'],
            ['ordinary_change_pct', 'task_change_pct'], ['Entrance feedback: all-vehicle cost', 'Emergency: fixed-cutoff task exposure']):
        group = pairs[pairs.family.eq(family)]
        for y, scenario in enumerate(scenarios):
            data = group[group.scenario.eq(scenario)][metric]
            ax.plot([data.min(), data.max()], [y, y], color='#89a8a1', linewidth=2)
            ax.scatter(data, np.full(len(data), y), color='#26786d', s=28, alpha=.8)
            ax.scatter([data.mean()], [y], color='#bc7638', marker='D', s=36, zorder=4)
        ax.axvline(0, color='#555', linewidth=.8)
        ax.set_yticks(range(len(scenarios)), list(scenarios))
        ax.invert_yaxis()
        ax.set_title(title)
        ax.set_xlabel('Paired change (%) | positive = worse')
        ax.grid(axis='x', alpha=.2)
    fig.suptitle('Designed one-factor screening | 3 seeds per condition | dots: seeds, diamond: mean')
    fig.savefig(output / 'stress_comparison.png', dpi=150)
    plt.close(fig)


def write_report(output, table, pairs, summary, turns, spec):
    text = ['# 第九轮：未知输入稳健性', '',
        '固定治理规则，改变需求、末端分流与下游供给；7种条件×3个新种子×两类各两策略，共84次实际运行、42组配对。全部结果保留，未按成绩调参。', '',
        '## 逐条件结果', '', '负数为减少，正数为增加；每行分别对同条件、同种子基准计算，再取三组均值。', '',
        '| 条件 | 入口总成本变化 均值/最差 | 应急任务经历量变化 均值/最差 | 应急完成 基准→协同 |',
        '|---|---:|---:|---:|']
    for scenario in spec['scenarios']:
        g = summary[summary.scenario.eq(scenario) & summary.family.eq('governance')].iloc[0]
        e = summary[summary.scenario.eq(scenario) & summary.family.eq('emergency')].iloc[0]
        text.append(f"| {LABELS[scenario]} | {g.ordinary_mean_change_pct:+.2f}% / {g.ordinary_worst_change_pct:+.2f}% | "
                    f"{e.task_mean_change_pct:+.2f}% / {e.task_worst_change_pct:+.2f}% | {int(e.reference_completed)}/3→{int(e.candidate_completed)}/3 |")
    gov = pairs[pairs.family.eq('governance')]
    em = pairs[pairs.family.eq('emergency')]
    both_unfinished = int((em.reference_task_completed.astype(str).str.lower().eq('false')
        & em.candidate_task_completed.astype(str).str.lower().eq('false')).sum())
    text += ['', f"- 入口反馈：21组中{gov.ordinary_change_pct.lt(-1e-8).sum()}组成本降低、{gov.ordinary_change_pct.gt(1e-8).sum()}组增加；最差{gov.ordinary_change_pct.max():+.2f}%。不能据部分平均改善宣布普遍有效。",
        f"- 应急协同：21组中{em.task_change_pct.lt(-1e-8).sum()}组任务经历量降低、{em.task_change_pct.gt(1e-8).sum()}组增加、{em.task_change_pct.abs().le(1e-8).sum()}组相同；最差{em.task_change_pct.max():+.2f}%。未完成者保留共同截止下界。",
        f"- 应急任务完成{int(em.reference_task_completed.sum())}/21→{int(em.candidate_task_completed.sum())}/21。相同的截止经历量中有{both_unfinished}组双方均未完成，只说明删失下界相同，不能推断最终救援时间相同或没有恶化。",
        f"- 应急协同使普通总成本最高增加{em.ordinary_change_pct.max():.2f}%，末期未完成最多增加{int(em.unfinished_change.max())}辆；任务有改善也可能把代价转给普通交通。供给600条件下两策略均0/3完成，当前方案不能解决该严重受限情景。",
        f"- 背景车最大成本变化：入口{gov.background_change_pct.max():+.2f}%，应急{em.background_change_pct.max():+.2f}%；普通未完成最多增加{int(pairs.unfinished_change.max())}辆。全体总成本下降仍可能损害部分车辆。", '',
        '![设计条件下的逐组变化](stress_comparison.png)', '', '## 输入怎样变化', '',
        '需求条件将核心路径率与三路背景同时乘0.8/1.2，按原固定车辆算法重新生成；不把减少输入车辆解释为治理收益。每种条件内部的策略使用完全相同的车辆、路径、供给、设施与种子。', '',
        '分流只改变南向1095134817末端分岔的路径选择，保留实际车辆ID、入口、出发时刻、车型和总量。目标为直行比例−/+15个百分点；按入口、来源窗取整，受可转移人数和权限限制。', '']
    for scenario in ['exit_more', 'through_more']:
        t = turns[turns.scenario.eq(scenario) & turns.family.eq('governance') & turns.policy.eq('free')]
        n = t.eligible_agents.sum()
        delta = (t.through_after.sum() - t.through_before.sum()) / n * 100 if n else 0
        text.append(f"- {LABELS[scenario]}：三个种子合计实际转移{int(t.actual_transfers.sum())}辆，直行比例合计变化{delta:+.2f}个百分点；逐入口/时窗明细见turn_reassignments.csv。")
    text += ['', '供给600/900/1200辆每小时均是数值出口的名义参数。只改该边界相位时长，几何、其他信号和车辆不变；实际出流不等于名义值，all_runs.csv另存按最终路段行程到达时间核算的actual_exit_flow_vph。', '',
        '## 历史观测误差仍需单独解释', '',
        '| 条件 | 流量WMAPE范围 % | 速度MAE范围 km/h | 5个真实低速站窗漏报范围 |',
        '|---|---:|---:|---:|']
    for scenario in spec['scenarios']:
        g = table[table.scenario.eq(scenario) & table.family.eq('governance')]
        text.append(f"| {LABELS[scenario]} | {g.flow_wmape_pct.min():.2f}—{g.flow_wmape_pct.max():.2f} | "
            f"{g.speed_mae_kmh.min():.2f}—{g.speed_mae_kmh.max():.2f} | {int(g.missed_slow_station_windows.min())}—{int(g.missed_slow_station_windows.max())} |")
    text += ['', '上述范围覆盖各条件六次入口运行，沿用15日校准观测与固定核心站集合；压力条件无须仍拟合这一天，但偏离观测的条件不能冒充更可信的真实状态。占有率误差、低速错报、未发车和未完成均保存在逐次结果，未据这些成绩挑选供给或调参。', '',
        '## 如何使用这些结论', '',
        '入口反馈不宜作为无条件优选：本轮在原条件、低/高需求及低供给等条件下有恶化。多分流和供给1200的三组总成本均降低，也只能视为这些设计条件中的有限证据，不能由已看过的结果继续调参并称独立验证。', '',
        '稀疏应急协同可保留为条件性功能演示，不能保证救援时间或普通交通同时改善。应报告完成率、固定截止量、背景车成本和未完成量，并明确严重供给约束及医院公共道路终点的限制。', '',
        '## 核验与适用范围', '',
        f"84次源码/输入哈希、固定车辆重算、42组共享外生输入、{int(table.meter_decisions.sum())}条入口决策及全部应急请求已核验。全部0瞬移、0碰撞、车辆守恒残差0；原始急刹车警告保留，零碰撞不代表真实安全性验证。", '',
        '入口成本08:00—09:00，应急普通成本08:00—09:30，包含入口外等待与未完成；任务从08:35计划调度算至共同09:30截止。两类网络与窗口不同，不能混算成同一改进率。', '',
        '这是三个新种子的单因素筛查，不覆盖所有未知OD、转向、相关变化、初始排队或医院周边未知流。倍率/比例/供给不是数据置信区间；各条件没有已知概率，不对它们随意平均成总体收益。真实高峰联合指标和救护门仍未验证，本轮不能替代真实效果验证。', '',
        '[冻结实验方案](../../../docs/第九轮实验方案.md)｜[逐次原始结果](all_runs.csv)｜[42组配对](paired_changes.csv)｜[逐条件汇总](scenario_summary.csv)', '',
        '```powershell', 'python 应用/run.py stress-suite --prefix stage09_main --jobs 3',
        'python 应用/run.py report-stress --prefix stage09_main', '```', '',
        '同前缀完成项只读归档；失败和旧输出保留，改变仿真规则或输入须使用新前缀。']
    (output / '第九轮开发诊断.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
