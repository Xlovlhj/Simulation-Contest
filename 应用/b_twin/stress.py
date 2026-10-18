"""Predeclared input stress scenarios; retain old simulation implementations."""
# AI-assisted: OpenAI Codex; record actual usage and human review for submission.
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, INPUTS, REFINED
from .runner import digest, prepare_run, run_traci, write_xml
from .demand import fixed_vehicles
from .evaluation import evaluate
from .emergency_guard import GUARD, GuardedController, prepare_guarded
from .emergency_access import build_emergency_access
from .boundary_supply import service_cycle
import sumolib

SEEDS = [20261029, 20261030, 20261031]
SCENARIOS = {
    'reference': {'demand_factor': 1., 'through_shift': 0., 'supply_vph': 900},
    'demand080': {'demand_factor': .8, 'through_shift': 0., 'supply_vph': 900},
    'demand120': {'demand_factor': 1.2, 'through_shift': 0., 'supply_vph': 900},
    'exit_more': {'demand_factor': 1., 'through_shift': -.15, 'supply_vph': 900},
    'through_more': {'demand_factor': 1., 'through_shift': .15, 'supply_vph': 900},
    'supply0600': {'demand_factor': 1., 'through_shift': 0., 'supply_vph': 600},
    'supply1200': {'demand_factor': 1., 'through_shift': 0., 'supply_vph': 1200},
}
ARMS = {'governance': ['free', 'feedback'], 'emergency': ['baseline', 'guarded_green']}
SOURCES = ['stress.py', 'runner.py', 'evaluation.py', 'demand.py', 'background.py',
    'boundary.py', 'boundary_supply.py', 'governance.py', 'emergency.py',
    'emergency_access.py', 'emergency_guard.py', 'paths.py']


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def shift_late_turns(agents, routes, shift, seed, allowed):
    """Move actual agents at one fork, preserving each origin/window count.

    Only alternatives sharing the exact prefix through 1095134817 are used.
    Target shifts are capped by donors and permissions, and rounded to agents.
    """
    result = agents.copy()
    if not shift:
        return result, []
    pivot, side = '1095134817', '45740606'
    options = {}
    for rid, edges in routes.items():
        if pivot not in edges:
            continue
        i = edges.index(pivot)
        kind = 'exit' if len(edges) > i + 1 and edges[i + 1] == side else 'through'
        options[rid] = (tuple(edges[:i + 1]), kind)
    eligible = result[result.route_id.isin(options)]
    audits = []
    for (origin, window), group in eligible.groupby(['origin_edge', 'source_window'], sort=True):
        prefixes = {options[r][0] for r in group.route_id}
        if len(prefixes) != 1:
            raise ValueError('Ambiguous late-turn prefix at one origin/window')
        prefix = next(iter(prefixes))
        before = sum(options[r][1] == 'through' for r in group.route_id)
        want = round(abs(shift) * len(group))
        source_kind, target_kind = ('exit', 'through') if shift > 0 else ('through', 'exit')
        donors = [idx for idx in group.index if options[result.at[idx, 'route_id']][1] == source_kind]
        donors.sort(key=lambda idx: hashlib.sha256(
            f"stress_turn_v1/{seed}/{result.at[idx, 'vehicle_id']}".encode()).digest())
        targets = sorted(r for r, info in options.items() if info == (prefix, target_kind))
        changed = 0
        for idx in donors:
            if changed >= want:
                break
            alternatives = [r for r in targets if allowed(routes[r], result.at[idx, 'sumo_vclass'])]
            if not alternatives:
                continue
            rid = alternatives[0]
            result.at[idx, 'route_id'] = rid
            result.at[idx, 'original_destination_edge'] = routes[rid][-1]
            changed += 1
        after = before + (changed if shift > 0 else -changed)
        audits.append({'origin_edge': origin, 'source_window': window, 'eligible_agents': len(group),
            'through_before': before, 'through_after': after, 'requested_transfers': want,
            'actual_transfers': changed, 'target_shift': shift,
            'actual_shift': (after - before) / len(group),
            'rounding_or_donor_permission_cap': changed != abs(shift) * len(group)})
    # Every actual departure, class and identity remains fixed during a turn perturbation.
    unchanged = [c for c in agents.columns if c not in {'route_id', 'original_destination_edge'}]
    pd.testing.assert_frame_equal(agents[unchanged], result[unchanged])
    return result, audits


def change_supply(output, metadata, target):
    old = metadata['boundary_supply']
    plan = service_cycle(target, old['lanes'])
    root = ET.parse(output / 'network.net.xml').getroot()
    logic = next(t for t in root.findall('tlLogic')
                 if t.get('id') == old['node_id'] and t.get('programID') == 'boundary_supply_assumed')
    phases = logic.findall('phase')
    assert len(phases) == 3
    for phase, field, state in zip(phases, ['green_seconds', 'yellow_seconds', 'red_seconds'], ['G', 'y', 'r']):
        assert set(phase.get('state')) == {state}
        phase.set('duration', str(round(plan[field], 6)))
    write_xml(root, output / 'network.net.xml')
    updated = {**old, **plan}
    save(output / 'boundary_supply.json', updated)
    metadata.update(boundary_supply=updated, residual_supply_vph_scenario=target,
                    network_sha256=digest(output / 'network.net.xml'))


def prepare_stress(family, policy, scenario, seed, run_id):
    if family not in ARMS or policy not in ARMS[family] or scenario not in SCENARIOS:
        raise ValueError('Unknown stress family/policy/scenario')
    setting = SCENARIOS[scenario]
    if family == 'governance':
        output, meta = prepare_run('peak', 'through', seed, run_id, 'time15', 1800, .5, 900, 900, policy)
    else:
        output, meta = prepare_guarded(seed, policy, run_id)
    before = output / 'pre_stress_inputs'
    before.mkdir()
    files = ['vehicles.csv', 'core_vehicles.csv', 'background_vehicles.csv',
        'demand_segments.csv', 'background_segments.csv', 'demand.rou.xml', 'demand_manifest.json',
        'network.net.xml', 'boundary_supply.json', 'run.json']
    for filename in files:
        shutil.copyfile(output / filename, before / filename)
    save(before / 'hashes.json', {name: digest(before / name) for name in files})
    factor = setting['demand_factor']
    root = ET.parse(output / 'demand.rou.xml').getroot()
    by_route = {r.get('id'): r.get('edges').split() for r in root.findall('route')}
    net = sumolib.net.readNet(str(output / 'network.net.xml'))
    def allowed(edges, cls):
        return all(net.getEdge(e).allows(cls) for e in edges)
    core = pd.read_csv(before / 'core_vehicles.csv')
    background = pd.read_csv(before / 'background_vehicles.csv')
    if factor != 1:
        fleet = pd.read_csv(INPUTS / 'vehicle_class_priors.csv')
        fleet = fleet[fleet.case.eq('peak')]
        core_segments = pd.read_csv(before / 'demand_segments.csv')
        core_segments['estimated_vph'] *= factor
        original_routes = pd.read_csv(INPUTS / 'peak_inferred_routes.csv')
        original_net = sumolib.net.readNet(str(REFINED / 'corridor.net.xml'))
        core, permissions = fixed_vehicles(original_routes, core_segments, fleet, original_net, seed, 'peak')
        core_segments.to_csv(output / 'scaled_core_rate_segments.csv', index=False, encoding='utf-8-sig')
        segments = pd.read_csv(before / 'background_segments.csv')
        segments['estimated_vph'] *= factor
        bg_ids = segments.route_id.unique()
        bg_routes = pd.DataFrame([{'route_id': rid, 'direction': 'south',
            'edges': ' '.join(by_route[rid])} for rid in bg_ids])
        background, bg_permissions = fixed_vehicles(bg_routes, segments, fleet, net, seed, 'peak')
        segments.to_csv(output / 'scaled_background_rate_segments.csv', index=False, encoding='utf-8-sig')
    core, turn_audits = shift_late_turns(core, by_route, setting['through_shift'], seed, allowed)
    original_routes = pd.read_csv(INPUTS / 'peak_inferred_routes.csv')
    original_ends = {r.route_id: r.edges.split()[-1] for r in original_routes.itertuples()}
    core['original_destination_edge'] = core.route_id.map(original_ends)
    pd.DataFrame(turn_audits, columns=['origin_edge', 'source_window', 'eligible_agents',
        'through_before', 'through_after', 'requested_transfers', 'actual_transfers',
        'target_shift', 'actual_shift', 'rounding_or_donor_permission_cap']).to_csv(
            output / 'turn_changes.csv', index=False, encoding='utf-8-sig')
    core.to_csv(output / 'core_vehicles.csv', index=False, encoding='utf-8-sig')
    background.to_csv(output / 'background_vehicles.csv', index=False, encoding='utf-8-sig')
    agents = pd.concat([core.assign(population='core'), background.assign(population='background')], ignore_index=True)
    if family == 'emergency':
        initial = pd.read_csv(before / 'vehicles.csv')
        agents = pd.concat([agents, initial[initial.population.eq('emergency')]], ignore_index=True)
    agents = agents.sort_values(['depart', 'vehicle_id']).reset_index(drop=True)
    assert agents.vehicle_id.is_unique
    agents.to_csv(output / 'vehicles.csv', index=False, encoding='utf-8-sig')
    templates = {t.get('id'): t.attrib for t in ET.parse(INPUTS / 'peak_diagnostic.rou.xml').getroot().findall('vType')}
    declared = {t.get('id') for t in root.findall('vType')}
    for row in agents[~agents.population.eq('emergency')].itertuples():
        tid = f'fixed_{row.base_type}_{int(round(row.speed_factor_assumed * 1000))}'
        if tid not in declared:
            root.insert(0, ET.Element('vType', **dict(templates[row.base_type], id=tid,
                speedFactor=str(row.speed_factor_assumed), speedDev='0')))
            declared.add(tid)
    for v in root.findall('vehicle'):
        root.remove(v)
    for row in agents.itertuples():
        edges = by_route[row.route_id]
        if not allowed(edges, row.sumo_vclass):
            raise ValueError('Perturbed route forbids vehicle class')
        attrs = dict(id=row.vehicle_id, depart=str(row.depart), route=row.route_id,
            departLane=row.depart_lane, departSpeed=str(row.depart_speed))
        if row.population == 'emergency':
            attrs.update(type='designed_ambulance_proxy', departPos='0')
        else:
            attrs['type'] = f'fixed_{row.base_type}_{int(round(row.speed_factor_assumed * 1000))}'
        ET.SubElement(root, 'vehicle', **attrs)
    write_xml(root, output / 'demand.rou.xml')
    # Retained demand_segments are explicitly the pre-stress inverse fit, not observations of this scenario.
    manifest = load(output / 'demand_manifest.json')
    manifest.update(version='stress_fixed_agents_v1', input_stress=setting,
        planned_vehicles=len(agents), planned_evaluation_vehicles=int(agents.phase.eq('evaluation').sum()),
        planned_warmup_vehicles=int(agents.phase.eq('warmup').sum()),
        vehicle_manifest_sha256=digest(output / 'vehicles.csv'),
        core_vehicle_manifest_sha256=digest(output / 'core_vehicles.csv'))
    manifest['background'].update(total_background_vph=900 * factor,
        planned_background_agents=len(background), core_vehicle_manifest_sha256=digest(output / 'core_vehicles.csv'),
        background_vehicle_manifest_sha256=digest(output / 'background_vehicles.csv'))
    manifest['limitations'].append('Stress inputs are designed; original inverse rate files are retained in pre_stress_inputs. Actual assignments are vehicles.csv.')
    save(output / 'demand_manifest.json', manifest)
    save(output / 'background.json', manifest['background'])
    change_supply(output, meta, setting['supply_vph'])
    stress = {'version': 'one_factor_stress_v1', 'scenario': scenario, 'family': family,
        'setting': setting, 'seed': seed, 'turn_transfers': sum(r['actual_transfers'] for r in turn_audits),
        'fork': '1095134817 -> 45740606 or through continuation',
        'scope': 'Designed input stress, not uncertainty probabilities or validated actual peak traffic',
        'demand_scope': 'Both core and three equal-share feeders; emergency task unchanged',
        'turn_scope': 'Actual south agents at one late fork; same origin, time, type, ID and total count',
        'pre_stress_hashes': {n: sha for n, sha in load(before / 'hashes.json').items() if n != 'run.json'}}
    save(output / 'stress_protocol.json', stress)
    meta.update(stress_protocol=stress, stress_protocol_sha256=digest(output / 'stress_protocol.json'),
        source_sha256={n: digest(APP / 'b_twin' / n) for n in SOURCES},
        background_vph_scenario=900 * factor, planned_vehicles=len(agents),
        vehicle_manifest_sha256=digest(output / 'vehicles.csv'),
        core_vehicle_manifest_sha256=digest(output / 'core_vehicles.csv'),
        demand_sha256=digest(output / 'demand.rou.xml'), status='prepared')
    (output / 'frozen_sources').mkdir()
    for name in SOURCES:
        shutil.copyfile(APP / 'b_twin' / name, output / 'frozen_sources' / name)
    save(output / 'run.json', meta)
    return output, meta


def execute_stress(family, policy, scenario, seed, run_id):
    output, meta = prepare_stress(family, policy, scenario, seed, run_id)
    started = time.perf_counter()
    try:
        run_traci(output, meta, GuardedController if family == 'emergency' else None)
        metrics = evaluate(output, 'peak', meta)
        meta.update(status='completed', backend='traci', elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        meta.update(status='failed', error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise
    finally:
        save(output / 'run.json', meta)
    return {'run_id': run_id, 'planned': len(pd.read_csv(output / 'vehicles.csv')),
        'teleports': metrics['teleports'], 'collisions': metrics['collisions']}


def run_stress_suite(prefix='stage09_main', jobs=3):
    if not prefix.replace('_', '').isalnum() or not 1 <= jobs <= 3:
        raise ValueError('Invalid prefix/jobs')
    model = build_emergency_access()
    planned = [{'run_id': f'{prefix}_{scenario}_s{seed}_{family}_{policy}', 'scenario': scenario,
                'seed': seed, 'family': family, 'policy': policy}
        for scenario in SCENARIOS for seed in SEEDS for family, policies in ARMS.items() for policy in policies]
    inputs = [INPUTS / 'peak_inferred_routes.csv', INPUTS / 'vehicle_class_priors.csv',
        INPUTS / 'peak_diagnostic.rou.xml', INPUTS / 'peak_detectors.add.xml',
        REFINED / 'traffic_15min_estimates.csv', REFINED / 'detector_mapping.csv',
        REFINED / 'corridor.net.xml', APP / 'models/boundary_v3/corridor.net.xml',
        APP / 'models/boundary_v3/definition.json', APP / 'models/emergency_v1/corridor.net.xml',
        APP / 'models/emergency_v1/definition.json', APP.parent / 'docs/第九轮实验方案.md',
        APP / 'tests/test_stress.py']
    spec = {'version': 'one_factor_stress_v1', 'scenarios': SCENARIOS, 'seeds': SEEDS, 'arms': ARMS,
        'runs': planned, 'source_sha256': {n: digest(APP / 'b_twin' / n) for n in SOURCES},
        'input_sha256': {p.relative_to(APP.parent).as_posix(): digest(p) for p in inputs},
        'guard': GUARD, 'entry_ridge': .5, 'clearance_seconds': 1800,
        'governance_cost_window': '08:00--09:00', 'emergency_cost_window': '08:00--09:30',
        'scope': 'One-factor designed screening only; 3 seeds per condition, no global probability or real-traffic claim',
        'decision': 'Keep candidate rules fixed; include adverse pairs, nontriggers and censored tasks; no outcome-based retuning'}
    directory = APP / 'results' / (prefix + '_suite')
    if directory.exists():
        if load(directory / 'suite.json')['specification'] != spec:
            raise ValueError('Frozen suite changed; use a new prefix')
        for name, sha in spec['source_sha256'].items():
            assert digest(directory / 'frozen_sources' / name) == sha
    else:
        (directory / 'frozen_sources').mkdir(parents=True)
        for name in SOURCES:
            shutil.copyfile(APP / 'b_twin' / name, directory / 'frozen_sources' / name)
        save(directory / 'suite.json', {'created_at': datetime.now().astimezone().isoformat(), 'specification': spec})
    def one(item):
        path = APP / 'results' / item['run_id']
        if path.exists():
            if load(path / 'run.json').get('status') == 'completed':
                return {**item, 'status': 'already_completed'}
            raise ValueError('Incomplete output retained; use a new prefix')
        command = [sys.executable, str(APP / 'run.py'), 'stress-run', '--family', item['family'],
            '--policy', item['policy'], '--scenario', item['scenario'], '--seed', str(item['seed']), '--run-id', item['run_id']]
        result = subprocess.run(command, capture_output=True, timeout=360,
            env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        (directory / (item['run_id'] + '.log')).write_bytes(result.stdout + result.stderr)
        result.check_returncode()
        return {**item, 'status': 'completed'}
    results = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(one, r): r for r in planned}
        for future in as_completed(pending):
            try:
                result = future.result()
            except Exception as exc:
                result = {**pending[future], 'status': 'failed', 'error': str(exc)}
            results.append(result)
            save(directory / 'execution.json', results)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(r['status'] == 'failed' for r in results):
        raise RuntimeError('Stress suite has failures; original outputs preserved')
    return {'suite': str(directory), 'completed_runs': len(results)}
