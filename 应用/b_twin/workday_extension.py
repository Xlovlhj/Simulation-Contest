"""Additional unseen workdays using the unchanged stage10 numerical engine.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
Freeze both dates before numeric inspection. This is retrospective validation.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .paths import APP, PROJECT, DATA
from .runner import digest
from .holdout import save_json
import pandas as pd
import numpy as np

DATES = ['2026-10-12', '2026-10-13']
SEEDS = [20261105, 20261106, 20261107]
PLAN = PROJECT / 'docs/第十四轮补充实验方案.md'
SOURCE_NAMES = ['workday_extension.py', 'holdout.py', 'assimilation.py', 'demand.py',
                'evaluation.py', 'runner.py', 'paths.py']


def directory(date):
    return APP / 'results' / ('stage14_day' + date.replace('-', '') + '_suite')


def engine(date):
    name = 'b_twin._workday_' + date.replace('-', '')
    spec = importlib.util.spec_from_file_location(name, APP / 'b_twin/holdout.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL = copy.deepcopy(module.PROTOCOL)
    module.PROTOCOL.update(version='stage14_unseen_workday_v1', date=date, seeds=SEEDS,
        cases={'ordinary': [date + 'T11:15:00+08:00', 2700], 'peak': [date + 'T08:00:00+08:00', 3600]},
        prior='day15_frozen_mean_NO_evaluation_day_training')
    module.assert_frozen = check
    old_plan, old_eval = module.make_plan, module.evaluate_holdout

    def make_plan(*args):
        agents, segments, decisions, constraints = old_plan(*args)
        segments['evidence'] = segments.evidence.str.replace('NO_day17_records_read', 'NO_evaluation_day_records_read', regex=False)
        for d in decisions:
            d['action'] = d['action'].replace('NO_day17_records_read', 'NO_evaluation_day_records_read')
        return agents, segments, decisions, constraints

    def evaluate(run, output, metadata):
        result = old_eval(run, output, metadata)
        result['primary_metrics']['basis'] = 'Unchanged original core stations; unseen workday ' + date
        result['scope'] = 'Retrospective date holdout; NOT future prediction; rolling uses past published observations'
        save_json(run / 'metrics.json', result)
        return result

    module.make_plan, module.evaluate_holdout = make_plan, evaluate
    return module


def freeze_all():
    original = APP / 'results/stage10_main_suite/frozen_inputs'
    for date in DATES:
        output = directory(date)
        if output.exists():
            check(output)
            continue
        frozen = output / 'frozen_inputs'
        shutil.copytree(original, frozen)
        code = output / 'frozen_sources'
        code.mkdir()
        for name in SOURCE_NAMES:
            shutil.copyfile(APP / 'b_twin' / name, code / name)
        shutil.copyfile(PLAN, output / PLAN.name)
        archive = DATA / 'raw/extra_workdays_20261012_13' / (date.replace('-', '') + '.zip')
        save_json(output / 'manifest.json', {
            'frozen_at': datetime.now().astimezone().isoformat(), 'protocol': engine(date).PROTOCOL,
            'source_archive': archive.relative_to(PROJECT).as_posix(), 'archive_sha256': digest(archive),
            'input_sha256': {p.name: digest(p) for p in frozen.iterdir() if p.is_file()},
            'source_sha256': {n: digest(code / n) for n in SOURCE_NAMES}, 'plan_sha256': digest(PLAN),
            'scope': 'Unseen workday values; fixed original model; test dates precede training date, NOT future forecasting'})
    return {'frozen_dates': DATES, 'values_read': False}


def check(output):
    m = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    for n, h in m['source_sha256'].items():
        assert digest(APP / 'b_twin' / n) == h, n
        assert digest(output / 'frozen_sources' / n) == h, n
    for n, h in m['input_sha256'].items():
        assert digest(output / 'frozen_inputs' / n) == h, n
    assert digest(PROJECT / m['source_archive']) == m['archive_sha256']
    assert digest(output / PLAN.name) == m['plan_sha256']
    assert m['protocol'] == engine_protocol(m['protocol']['date'])
    return m


def engine_protocol(date):
    return engine(date).PROTOCOL


def report(date, e, output):
    rows, windows = [], []
    recomputed_agents = decisions_checked = constraints_checked = 0
    from .evaluation import primary_metrics
    for case in e.PROTOCOL['cases']:
        for policy in e.PROTOCOL['policies']:
            for seed in SEEDS:
                run = APP / 'results' / f'stage14_day{date.replace("-", "")}_{case}_s{seed}_{policy}'
                meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
                assert meta['status'] == 'completed'
                for n in ['network.net.xml', 'network_definition.json', 'detectors.add.xml']:
                    assert digest(run / n) == digest(output / 'frozen_inputs' / n)
                agents, segments, decisions, constraints = e.make_plan(output, case, policy, seed)
                actual = pd.read_csv(run / 'vehicles.csv', dtype={'origin_edge': str, 'original_destination_edge': str})
                pd.testing.assert_frame_equal(agents, actual, check_dtype=False, rtol=1e-10, atol=1e-9)
                assert decisions == json.loads((run / 'decisions.json').read_text(encoding='utf-8'))
                recomputed_agents += len(agents)
                decisions_checked += len(decisions)
                if policy == 'rolling':
                    used = constraints[constraints.constraint_used]
                    constraints_checked += len(used)
                    for c in used.itertuples():
                        now = pd.Timestamp(decisions[c.slot]['decision_at'])
                        assert pd.Timestamp(c.latest_available_at) <= now and pd.Timestamp(c.last_observation_end) <= now
                assert digest(run / 'vehicles.csv') == meta['vehicle_manifest_sha256']
                assert digest(run / 'demand.rou.xml') == meta['demand_sha256']
                metrics = e.evaluate_holdout(run, output, meta)
                saved = pd.read_csv(run / 'comparison_15min.csv')
                recomputed = primary_metrics(saved)
                for k in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points']:
                    assert np.isclose(metrics['primary_metrics'][k], recomputed[k])
                row = {'date': date, 'case': case, 'policy': policy, 'seed': seed, 'run_id': run.name,
                    **metrics['primary_metrics'], 'passed_joint_check': metrics['passed_joint_check'],
                    'teleports': metrics['teleports'], 'collisions': metrics['collisions'],
                    **metrics['accounting'], **metrics['trip_accounting']}
                rows.append(row)
                windows.append(saved.assign(run_id=run.name, date=date, case=case, policy=policy))
    frame = pd.DataFrame(rows)
    frame.to_csv(output / 'all_runs.csv', index=False, encoding='utf-8-sig')
    pd.concat(windows, ignore_index=True).to_csv(output / 'all_windows.csv.gz', index=False, compression='gzip')
    fields = ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points',
        'observed_slow_station_windows', 'reproduced_slow_station_windows', 'false_slow_station_windows', 'passed_joint_check']
    summary = frame.groupby(['date', 'case', 'policy'])[fields].mean().reset_index()
    summary.to_csv(output / 'summary.csv', index=False, encoding='utf-8-sig')
    check(output)
    result = {'date': date, 'actual_runs': len(frame), 'passed_joint_runs': int(frame.passed_joint_check.sum()),
        'all_zero_teleports': bool(frame.teleports.eq(0).all()), 'all_zero_collisions': bool(frame.collisions.eq(0).all()),
        'all_conservation_zero': bool(frame.conservation_residual.eq(0).all()), 'all_vehicle_plans_recomputed': True,
        'vehicles_recomputed': recomputed_agents, 'decisions_checked': decisions_checked, 'constraints_checked': constraints_checked,
        'sources_and_inputs_unchanged': True, 'scope': 'Two specified clock windows; NOT whole-day simulation or future prediction'}
    save_json(output / 'verification.json', result)
    return result


def run_all():
    results = []
    for date in DATES:
        output, e = directory(date), engine(date)
        check(output)
        if not (output / 'extraction.json').exists():
            print(json.dumps({'date': date, 'extraction': e.parse_archive(output)}, ensure_ascii=False), flush=True)
        prefix = 'stage14_day' + date.replace('-', '')
        work = [(case, policy, seed) for case in e.PROTOCOL['cases'] for policy in e.PROTOCOL['policies'] for seed in SEEDS]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(e.execute_one, output, *item, prefix): item for item in work}
            for f in as_completed(futures):
                print(json.dumps({'date': date, **f.result()}, ensure_ascii=False), flush=True)
        result = report(date, e, output)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    save_json(APP / 'results/stage14_workday_verification.json', {'dates': results})
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--freeze-only', action='store_true')
    args = p.parse_args()
    print(json.dumps(freeze_all() if args.freeze_only else run_all(), ensure_ascii=False), flush=True)
