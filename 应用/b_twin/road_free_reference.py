"""Post-hoc complete three-seed all-green controls, without changing task inputs.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import pandas as pd
from .paths import APP, PROJECT
from .runner import digest, run_traci
from .road_emergency_v2 import SEEDS
from .emergency_guard import prepare_guarded, GuardedController
from .conditional_governance_v2 import ConditionalMeter
from . import governance
from .evaluation import evaluate
from .governance_report import agent_costs
from .emergency_report import audit_emergency
from .emergency_guard_report import audit_guard

SUITE = APP / 'results/stage15_road_free_reference_suite'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def freeze():
    parent = load(APP / 'results/stage15_road_emergency_v2_suite/manifest.json')['specification']
    sources = dict(parent['source_sha256'])
    sources['road_free_reference.py'] = digest(APP / 'b_twin/road_free_reference.py')
    spec = {'seeds': SEEDS, 'parent_specification': parent, 'source_sha256': sources,
        'plan_sha256': digest(PROJECT / 'docs/第十五轮应急全绿参照方案.md'),
        'scope': 'Post-hoc full preselected seed set; not new blind validation; all-green vs A vs A+extra coordination'}
    if SUITE.exists():
        assert load(SUITE / 'manifest.json')['specification'] == spec
    else:
        (SUITE / 'frozen_sources').mkdir(parents=True)
        for n in sources:
            shutil.copyfile(APP / 'b_twin' / n, SUITE / 'frozen_sources' / n)
        save(SUITE / 'manifest.json', {'frozen_at': datetime.now().astimezone().isoformat(), 'specification': spec})
    for n, h in sources.items():
        assert digest(APP / 'b_twin' / n) == h and digest(SUITE / 'frozen_sources' / n) == h
    return spec


def one(seed):
    name = f'stage15_road_s{seed}_baseline_free_reference'
    output = APP / 'results' / name
    if output.exists():
        assert load(output / 'run.json')['status'] == 'completed'
        return name
    spec = importlib.util.spec_from_file_location('b_twin._road_free_reference', APP / 'b_twin/road_emergency_v2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare_guarded = lambda seed, policy, run_id: prepare_guarded(seed, policy, run_id + '_free_reference')
    output, meta = module.prepare(seed, 'baseline')
    meta.update(meter_policy='free', background_meter_rule=None,
        extension_scope='Post-hoc all-green reference; complete preselected three seeds')
    paired = APP / 'results' / f'stage15_road_s{seed}_baseline'
    for n in ['network.net.xml', 'vehicles.csv', 'demand.rou.xml', 'emergency_scenario.json']:
        assert digest(output / n) == digest(paired / n), n
    governance.MeterController = ConditionalMeter
    try:
        run_traci(output, meta, GuardedController)
        evaluate(output, 'peak', meta)
        meta['status'] = 'completed'
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save(output / 'run.json', meta)
    return name


def report():
    freeze()
    rows = []
    for seed in SEEDS:
        runs = {'free': f'stage15_road_s{seed}_baseline_free_reference',
            'A_only': f'stage15_road_s{seed}_baseline', 'A_plus_B': f'stage15_road_s{seed}_guarded_green'}
        base = None
        for policy, name in runs.items():
            run = APP / 'results' / name
            meta = load(run / 'run.json')
            assert meta['status'] == 'completed'
            metrics = evaluate(run, 'peak', meta)
            task = audit_emergency(run, meta)
            task.update(audit_guard(run, meta))
            cost = agent_costs(run, run, horizon_seconds=5700)
            row = {'seed': seed, 'policy': policy, 'run_id': name, **task,
                'ordinary_vehicle_hours': cost['core_evaluation_vehicle_hours'] + cost['background_evaluation_vehicle_hours'],
                'background_vehicle_hours': cost['background_evaluation_vehicle_hours'],
                'unfinished': metrics['trip_accounting']['unfinished_after_clearance'],
                'teleports': metrics['teleports'], 'collisions': metrics['collisions'],
                'conservation_residual': metrics['accounting']['conservation_residual']}
            if base is None:
                base = row
            row.update(task_change_from_free_pct=100 * (row['rescue_elapsed_lower_bound_s'] / base['rescue_elapsed_lower_bound_s'] - 1),
                ordinary_change_from_free_pct=100 * (row['ordinary_vehicle_hours'] / base['ordinary_vehicle_hours'] - 1),
                background_change_from_free_pct=100 * (row['background_vehicle_hours'] / base['background_vehicle_hours'] - 1))
            rows.append(row)
    frame = pd.DataFrame(rows)
    frame.to_csv(SUITE / 'all_three_arm_results.csv', index=False, encoding='utf-8-sig')
    result = {'new_runs': 3, 'total_three_arm_runs': 9, 'all_three_original_seeds_retained': True,
        'all_zero_teleports': bool(frame.teleports.eq(0).all()), 'all_zero_collisions': bool(frame.collisions.eq(0).all()),
        'all_conservation_zero': bool(frame.conservation_residual.eq(0).all()),
        'scope': 'Post-hoc diagnostic paired comparison, NOT independent future or field evidence'}
    save(SUITE / 'verification.json', result)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--one', type=int)
    args = p.parse_args()
    if args.one:
        print(one(args.one))
    else:
        freeze()
        def launch(seed):
            r = subprocess.run([sys.executable, '-m', 'b_twin.road_free_reference', '--one', str(seed)],
                cwd=APP, capture_output=True, timeout=420, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            (SUITE / f's{seed}.log').write_bytes(r.stdout + r.stderr)
            r.check_returncode()
            return seed
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(launch, seed) for seed in SEEDS]
            for f in as_completed(futures):
                print(json.dumps({'completed_seed': f.result()}), flush=True)
        print(json.dumps(report(), ensure_ascii=False), flush=True)
