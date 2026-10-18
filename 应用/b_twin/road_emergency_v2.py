"""Emergency arrival at a public-road section, without a hospital-gate dependency.

AI-assisted: OpenAI Codex; disclose actual use and human review.
This is a designed road-passage task, not a real dispatch or approved plan.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
from .paths import APP, PROJECT
from .runner import digest, run_traci, write_xml
from .stress import SOURCES, load, save
from .emergency_guard import prepare_guarded, GuardedController, GUARD
from .emergency import VEHICLE
from .evaluation import evaluate
from .governance_report import agent_costs
from . import governance
from .conditional_governance_v2 import ConditionalMeter, RULE
from .emergency_report import audit_emergency
from .emergency_guard_report import audit_guard
import sumolib

SEEDS = [20261125, 20261126, 20261127]
POLICIES = ['baseline', 'guarded', 'guarded_green']
TARGET = '1095134811'
SUITE = APP / 'results/stage15_road_emergency_v2_suite'


def definition():
    parent = load(APP / 'models/emergency_v1/definition.json')
    net = sumolib.net.readNet(str(APP / 'models/emergency_v1/corridor.net.xml'))
    route = parent['route_edges'][:parent['route_edges'].index(TARGET) + 1]
    assert all(net.getEdge(b) in net.getEdge(a).getOutgoing() for a, b in zip(route, route[1:]))
    assert all(net.getEdge(e).allows('passenger') for e in route)
    coords = {}
    for label, eid in [('origin', route[0]), ('target', route[-1])]:
        edge = net.getEdge(eid)
        point = edge.getShape()[0 if label == 'origin' else -1]
        lon, lat = net.convertXY2LonLat(*point)
        coords[label] = {'sumo_edge': eid, 'name': edge.getName(), 'longitude': lon, 'latitude': lat,
            'meaning': 'Scheduled insertion on origin edge' if label == 'origin' else 'Arrival at target edge downstream end'}
    return {'version': 'road_target_v3_corrected_meter', 'origin_edge': route[0], 'destination_edge': route[-1], 'route_edges': route,
        'route_external_length_m': sum(net.getEdge(e).getLength() for e in route), 'coordinates': coords,
        'parent_geometry_sha256': parent['network_sha256'],
        'task': 'Designed emergency road-passage task to the downstream designated road section under en-route disruption',
        'scope': 'No hospital, gate, internal driveway, loading, patient handover or on-site treatment is modeled',
        'limitations': ['Origin is a modeled insertion point, not a verified emergency base or legal parking place.',
            'Target is a road-passage section; no stopping or public-road parking permission is assumed.',
            'Coordinates and street matching use official GIS as a geographic cross-check; lane coverage is not surveyed.',
            'Demand, external supply, event and controller parameters remain designed inputs; baseline traffic accuracy failed.',
            'No real dispatch, response deadline or deployment effectiveness is established.']}


def prepare(seed, policy):
    run_id = f'stage15_road_s{seed}_{policy}'
    output, meta = prepare_guarded(seed, policy, run_id)
    d = definition()
    before = output / 'pre_road_target_inputs'
    before.mkdir()
    for n in ['run.json', 'emergency_scenario.json', 'emergency_network_definition.json', 'demand.rou.xml', 'vehicles.csv', 'demand_manifest.json']:
        shutil.copyfile(output / n, before / n)
    scenario = dict(meta['emergency_scenario'])
    scenario.update(destination_edge=TARGET, fixed_route_edges=d['route_edges'], endpoint_scope=d['scope'],
        limitations=d['limitations'] + ['Passenger permissions and all SUMO safety modes retained in all policies.',
            '180-second threshold is a design diagnostic, not an official response standard.'])
    root = ET.parse(output / 'demand.rou.xml').getroot()
    route = next(r for r in root.findall('route') if r.get('id') == 'emergency_fixed_route')
    route.set('edges', ' '.join(d['route_edges']))
    write_xml(root, output / 'demand.rou.xml')
    vehicles = pd.read_csv(output / 'vehicles.csv', dtype={'origin_edge': str, 'original_destination_edge': str})
    vehicles.loc[vehicles.vehicle_id.eq(VEHICLE), 'original_destination_edge'] = TARGET
    vehicles.to_csv(output / 'vehicles.csv', index=False, encoding='utf-8-sig')
    manifest = load(output / 'demand_manifest.json')
    manifest.update(vehicle_manifest_sha256=digest(output / 'vehicles.csv'), emergency_endpoint_scope=d['scope'])
    save(output / 'demand_manifest.json', manifest)
    save(output / 'emergency_scenario.json', scenario)
    save(output / 'emergency_network_definition.json', d)
    meta.update(network_version='emergency_road_target_v2', emergency_scenario=scenario,
        emergency_scenario_sha256=digest(output / 'emergency_scenario.json'),
        emergency_network_definition_sha256=digest(output / 'emergency_network_definition.json'),
        demand_sha256=digest(output / 'demand.rou.xml'), vehicle_manifest_sha256=digest(output / 'vehicles.csv'))
    save(output / 'run.json', meta)
    return output, meta


def one(seed, policy):
    existing = APP / 'results' / f'stage15_road_s{seed}_{policy}'
    if existing.exists():
        assert load(existing / 'run.json')['status'] == 'completed', 'Incomplete output retained'
        return {'run_id': existing.name, 'status': 'already_completed'}
    output, meta = prepare(seed, policy)
    governance.MeterController = ConditionalMeter
    meta['background_meter_rule'] = RULE
    save(output / 'background_meter_rule.json', RULE)
    started = time.perf_counter()
    try:
        run_traci(output, meta, GuardedController)
        result = evaluate(output, 'peak', meta)
        meta.update(status='completed', elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save(output / 'run.json', meta)
    return {'run_id': output.name, 'teleports': result['teleports'], 'collisions': result['collisions']}


def freeze():
    files = list(dict.fromkeys(SOURCES + ['road_emergency_v2.py', 'governance_report.py', 'emergency_report.py', 'emergency_guard_report.py', 'conditional_governance_v2.py']))
    inputs = dict(load(APP / 'results/stage15_conditional_suite/manifest.json')['specification']['input_sha256'])
    inputs['docs/第十五轮道路应急修复方案.md'] = digest(PROJECT / 'docs/第十五轮道路应急修复方案.md')
    spec = {'task': definition(), 'seeds': SEEDS, 'policies': POLICIES, 'guard': GUARD,
        'source_sha256': {n: digest(APP / 'b_twin' / n) for n in files}, 'input_sha256': inputs,
        'parent_geometry_sha256': digest(APP / 'models/emergency_v1/corridor.net.xml'),
        'scope': 'New endpoint and fresh seeds; do not mix old hospital-road task results'}
    for name, value in inputs.items():
        assert digest(PROJECT / name) == value
    if SUITE.exists():
        assert load(SUITE / 'manifest.json')['specification'] == spec
    else:
        (SUITE / 'frozen_sources').mkdir(parents=True)
        for n in files:
            shutil.copyfile(APP / 'b_twin' / n, SUITE / 'frozen_sources' / n)
        save(SUITE / 'manifest.json', {'frozen_at': datetime.now().astimezone().isoformat(), 'specification': spec})
    for n, h in spec['source_sha256'].items():
        assert digest(SUITE / 'frozen_sources' / n) == h
    return spec


def report():
    freeze()
    rows = []
    for seed in SEEDS:
        paths = {p: APP / 'results' / f'stage15_road_s{seed}_{p}' for p in POLICIES}
        for name in ['network.net.xml', 'vehicles.csv', 'demand.rou.xml', 'emergency_scenario.json']:
            assert len({digest(run / name) for run in paths.values()}) == 1, name
        for policy, run in paths.items():
            m = load(run / 'run.json')
            assert m['status'] == 'completed'
            metrics = evaluate(run, 'peak', m)
            costs = agent_costs(run, run, horizon_seconds=5700)
            task = audit_emergency(run, m)
            task.update(audit_guard(run, m))
            rows.append({'seed': seed, 'policy': policy, 'run_id': run.name, **task, **costs,
                'teleports': metrics['teleports'], 'collisions': metrics['collisions'],
                'conservation_residual': metrics['accounting']['conservation_residual'],
                'unfinished': metrics['trip_accounting']['unfinished_after_clearance']})
    frame = pd.DataFrame(rows)
    frame.to_csv(SUITE / 'all_runs.csv', index=False, encoding='utf-8-sig')
    pairs = []
    for seed in SEEDS:
        g = frame[frame.seed.eq(seed)].set_index('policy')
        a = g.loc['baseline']
        for policy in ['guarded', 'guarded_green']:
            b = g.loc[policy]
            pairs.append({'seed': seed, 'policy': policy,
                'reference_task_seconds': a.rescue_elapsed_lower_bound_s,
                'candidate_task_seconds': b.rescue_elapsed_lower_bound_s,
                'task_change_pct': 100 * (b.rescue_elapsed_lower_bound_s / a.rescue_elapsed_lower_bound_s - 1),
                'ordinary_change_pct': 100 * ((b.core_evaluation_vehicle_hours + b.background_evaluation_vehicle_hours) / (a.core_evaluation_vehicle_hours + a.background_evaluation_vehicle_hours) - 1),
                'background_change_pct': 100 * (b.background_evaluation_vehicle_hours / a.background_evaluation_vehicle_hours - 1),
                'reference_completed': bool(a.rescue_completed), 'candidate_completed': bool(b.rescue_completed)})
    paired = pd.DataFrame(pairs)
    paired.to_csv(SUITE / 'paired_changes.csv', index=False, encoding='utf-8-sig')
    result = {'actual_runs': 9, 'paired_comparisons': 6, 'all_inputs_identical_within_seed': True,
        'all_zero_teleports': bool(frame.teleports.eq(0).all()), 'all_zero_collisions': bool(frame.collisions.eq(0).all()),
        'all_conservation_zero': bool(frame.conservation_residual.eq(0).all()),
        'scope': definition()['scope']}
    save(SUITE / 'verification.json', result)
    return result


def run_suite():
    freeze()
    def launch(seed, policy):
        r = subprocess.run([sys.executable, '-m', 'b_twin.road_emergency_v2', '--one', str(seed), policy],
            cwd=APP, capture_output=True, timeout=420, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        (SUITE / f's{seed}_{policy}.log').write_bytes(r.stdout + r.stderr)
        r.check_returncode()
        return {'seed': seed, 'policy': policy, 'status': 'completed'}
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(launch, seed, p): (seed, p) for seed in SEEDS for p in POLICIES}
        for f in as_completed(futures):
            try:
                r = f.result()
            except Exception as exc:
                r = {'item': futures[f], 'status': 'failed', 'error': str(exc)}
            results.append(r)
            save(SUITE / 'execution.json', results)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    assert all(r['status'] == 'completed' for r in results), 'Failed outputs preserved'
    return report()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--one', nargs=2)
    p.add_argument('--freeze-only', action='store_true')
    p.add_argument('--report-only', action='store_true')
    args = p.parse_args()
    r = one(int(args.one[0]), args.one[1]) if args.one else freeze() if args.freeze_only else report() if args.report_only else run_suite()
    print(json.dumps(r, ensure_ascii=False), flush=True)
