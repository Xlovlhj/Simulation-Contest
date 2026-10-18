"""Corrected SUMO occupancy units; fresh, predeclared metering comparisons.

AI-assisted: OpenAI Codex; disclose actual use and human review.
All parameters are design assumptions, not field-calibrated controls.
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

import numpy as np
import pandas as pd

from .paths import APP, PROJECT, INPUTS, REFINED
from .runner import digest, run_traci
from . import governance
from .governance import MeterController, UPDATE, YELLOW, SATURATION, STARTUP, phase_state, feedback_rate
from .evaluation import evaluate, WARMUP
from .stress import SCENARIOS, SOURCES, prepare_stress, load, save
from .governance_report import agent_costs
import traci.constants as tc

RULE = {'version': 'conditional_meter_v2_units_corrected', 'on_occupancy_pct': 18., 'off_occupancy_pct': 12.,
    'consecutive_intervals': 2, 'queue_release_fraction': .6, 'queue_reset_fraction': .4,
    'scope': 'SUMO 1.27.1 ratio multiplied by 100; designed thresholds; causal simulation state; no guaranteed benefit'}
SEEDS = [20261121, 20261122, 20261123]
SUITE = APP / 'results/stage15_conditional_suite'


def transition(state, occupancy, queue_fraction):
    s = dict(state)
    s['high'] = s['high'] + 1 if occupancy >= RULE['on_occupancy_pct'] else 0
    s['low'] = s['low'] + 1 if occupancy <= RULE['off_occupancy_pct'] else 0
    if queue_fraction >= RULE['queue_release_fraction']:
        s.update(blocked=True, active=False)
    elif s['blocked'] and queue_fraction < RULE['queue_reset_fraction']:
        s['blocked'] = False
    if not s['blocked']:
        if s['high'] >= RULE['consecutive_intervals']:
            s['active'] = True
        if s['low'] >= RULE['consecutive_intervals']:
            s['active'] = False
    reason = 'queue_release_all_green' if s['blocked'] else 'conditional_feedback' if s['active'] else 'inactive_all_green'
    return s, reason


class ConditionalMeter(MeterController):
    def __init__(self, connection, output, policy, plan):
        super().__init__(connection, output, policy, plan)
        self.states = {s['id']: {'active': False, 'blocked': False, 'high': 0, 'low': 0} for s in plan['facilities']}

    def step(self, now):
        for s in self.plan['facilities']:
            values = self.samples[s['id']]
            values.append(100. * self.c.edge.getSubscriptionResults(s['sensor_edge'])[tc.LAST_STEP_OCCUPANCY])
            del values[:-int(UPDATE / .5)]
        if now + 1e-7 >= self.next_update:
            for s in self.plan['facilities']:
                values = self.samples[s['id']]
                occ = sum(values) / len(values)
                queue = sum(self.c.edge.getSubscriptionResults(e)[tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for e in s['approach_edges'])
                fraction = queue / s['storage_slots_assumed']
                if self.policy == 'free':
                    state = dict(self.states[s['id']], active=False)
                    reason = 'reference_all_green'
                else:
                    state, reason = transition(self.states[s['id']], occ, fraction)
                self.states[s['id']] = state
                if state['active']:
                    self.rates[s['id']], _ = feedback_rate(self.rates[s['id']], occ, fraction, s['maximum_vph'])
                    green = min(UPDATE - YELLOW - .5, UPDATE * self.rates[s['id']] / (s['lanes'] * SATURATION) + STARTUP)
                else:
                    self.rates[s['id']], green = s['maximum_vph'], UPDATE
                self.greens[s['id']] = green
                self.writer.writerow({'simulation_seconds': now, 'meter': s['id'], 'policy': 'conditional',
                    'sensor_occupancy_pct': occ, 'approach_halting': queue, 'queue_fraction_assumed': fraction,
                    'rate_vph_nominal': self.rates[s['id']], 'green_seconds': green, 'reason': reason})
                self.samples[s['id']].clear()
            self.handle.flush()
            self.next_update += UPDATE
        for s in self.plan['facilities']:
            state = 'G' if now < WARMUP or not self.states[s['id']]['active'] else phase_state(now - WARMUP, self.greens[s['id']])
            self.set_state(s, state)


def audit(run, meta):
    log = pd.read_csv(run / 'control_30s.csv')
    for spec in meta['meter_plan']['facilities']:
        rows = log[log.meter.eq(spec['id'])]
        expected = np.arange(WARMUP, meta['simulation_end_seconds'] + .01, UPDATE)
        assert np.allclose(rows.simulation_seconds, expected) and len(rows) == len(expected)
        state = {'active': False, 'blocked': False, 'high': 0, 'low': 0}
        rate = spec['initial_vph']
        for r in rows.itertuples():
            fraction = r.approach_halting / spec['storage_slots_assumed']
            assert np.isclose(r.queue_fraction_assumed, fraction)
            state, reason = transition(state, r.sensor_occupancy_pct, fraction)
            if state['active']:
                rate, _ = feedback_rate(rate, r.sensor_occupancy_pct, fraction, spec['maximum_vph'])
                green = min(UPDATE - YELLOW - .5, UPDATE * rate / (spec['lanes'] * SATURATION) + STARTUP)
            else:
                rate, green = spec['maximum_vph'], UPDATE
            assert reason == r.reason and r.policy == 'conditional'
            assert np.isclose(rate, r.rate_vph_nominal) and np.isclose(green, r.green_seconds)
    return {'decisions_checked': len(log), 'active_decisions': int(log.reason.eq('conditional_feedback').sum()),
        'queue_release_decisions': int(log.reason.eq('queue_release_all_green').sum())}


def one(scenario, seed, policy):
    name = f'stage15_conditional_{scenario}_s{seed}_{policy}'
    output = APP / 'results' / name
    if output.exists():
        assert load(output / 'run.json')['status'] == 'completed', 'Incomplete run retained'
        return {'run_id': name, 'status': 'already_completed'}
    output, meta = prepare_stress('governance', 'free' if policy == 'free' else 'feedback', scenario, seed, name)
    meta['candidate_rule'] = RULE if policy == 'conditional' else None
    save(output / 'candidate_rule.json', meta['candidate_rule'])
    governance.MeterController = ConditionalMeter  # Process-local factory; source files remain unchanged.
    started = time.perf_counter()
    try:
        run_traci(output, meta)
        result = evaluate(output, 'peak', meta)
        if policy == 'conditional':
            save(output / 'conditional_audit.json', audit(output, meta))
        meta.update(status='completed', candidate_policy=policy, elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save(output / 'run.json', meta)
    return {'run_id': name, 'teleports': result['teleports'], 'collisions': result['collisions']}


def freeze():
    sources = list(dict.fromkeys(SOURCES + ['conditional_governance_v2.py', 'governance_report.py', 'storage.py']))
    inputs = [INPUTS / n for n in ['peak_inferred_routes.csv', 'vehicle_class_priors.csv', 'peak_diagnostic.rou.xml', 'peak_detectors.add.xml']]
    inputs += [REFINED / n for n in ['traffic_15min_estimates.csv', 'detector_mapping.csv', 'corridor.net.xml']]
    inputs += [APP / 'models/boundary_v3' / n for n in ['corridor.net.xml', 'definition.json']]
    inputs += [PROJECT / 'docs/第十五轮占有率单位修复方案.md', APP / 'runtime/stage14_unit_probe/unit_probe.json']
    spec = {'rule': RULE, 'seeds': SEEDS, 'scenarios': SCENARIOS, 'policies': ['free', 'conditional'],
        'source_sha256': {n: digest(APP / 'b_twin' / n) for n in sources},
        'input_sha256': {p.relative_to(PROJECT).as_posix(): digest(p) for p in inputs},
        'scope': 'Finite designed conditions, 3 fresh seeds per condition; no scenario probabilities; no retuning'}
    if SUITE.exists():
        assert load(SUITE / 'manifest.json')['specification'] == spec
    else:
        (SUITE / 'frozen_sources').mkdir(parents=True)
        for n in sources:
            shutil.copyfile(APP / 'b_twin' / n, SUITE / 'frozen_sources' / n)
        save(SUITE / 'manifest.json', {'frozen_at': datetime.now().astimezone().isoformat(), 'specification': spec})
    for n, h in spec['source_sha256'].items():
        assert digest(SUITE / 'frozen_sources' / n) == h
    return spec


def report():
    freeze()
    rows = []
    decision_count = 0
    for scenario in SCENARIOS:
        for seed in SEEDS:
            paths = {p: APP / 'results' / f'stage15_conditional_{scenario}_s{seed}_{p}' for p in ['free', 'conditional']}
            for name in ['network.net.xml', 'demand.rou.xml', 'vehicles.csv', 'core_vehicles.csv', 'background_vehicles.csv']:
                assert digest(paths['free'] / name) == digest(paths['conditional'] / name), name
            metrics, costs = {}, {}
            for p, run in paths.items():
                meta = load(run / 'run.json')
                assert meta['status'] == 'completed'
                metrics[p] = evaluate(run, 'peak', meta)
                costs[p] = agent_costs(run, run)
            d = audit(paths['conditional'], load(paths['conditional'] / 'run.json'))
            decision_count += d['decisions_checked']
            a, b = costs['free'], costs['conditional']
            rows.append({'scenario': scenario, 'seed': seed,
                'ordinary_change_pct': 100 * (b['evaluation_experienced_vehicle_hours'] / a['evaluation_experienced_vehicle_hours'] - 1),
                'background_change_pct': 100 * (b['background_evaluation_vehicle_hours'] / a['background_evaluation_vehicle_hours'] - 1),
                'reference_vehicle_hours': a['evaluation_experienced_vehicle_hours'],
                'candidate_vehicle_hours': b['evaluation_experienced_vehicle_hours'],
                'unfinished_change': metrics['conditional']['trip_accounting']['unfinished_after_clearance'] - metrics['free']['trip_accounting']['unfinished_after_clearance'],
                'reference_unfinished': metrics['free']['trip_accounting']['unfinished_after_clearance'],
                'candidate_unfinished': metrics['conditional']['trip_accounting']['unfinished_after_clearance'],
                'teleports': metrics['free']['teleports'] + metrics['conditional']['teleports'],
                'collisions': metrics['free']['collisions'] + metrics['conditional']['collisions'],
                'conservation_residual_abs': abs(metrics['free']['accounting']['conservation_residual']) + abs(metrics['conditional']['accounting']['conservation_residual']), **d})
    frame = pd.DataFrame(rows)
    frame.to_csv(SUITE / 'paired_changes.csv', index=False, encoding='utf-8-sig')
    summary = frame.groupby('scenario').agg(pairs=('seed', 'size'), average_change_pct=('ordinary_change_pct', 'mean'),
        worst_change_pct=('ordinary_change_pct', 'max'), best_change_pct=('ordinary_change_pct', 'min'),
        worsened_pairs=('ordinary_change_pct', lambda s: int((s > 1e-9).sum())),
        active_decisions=('active_decisions', 'sum'), queue_release_decisions=('queue_release_decisions', 'sum'))
    summary.to_csv(SUITE / 'summary.csv', encoding='utf-8-sig')
    result = {'actual_runs': 42, 'paired_comparisons': len(frame), 'decisions_checked': decision_count,
        'identical_exogenous_inputs_verified': True, 'worsened_pairs': int((frame.ordinary_change_pct > 1e-9).sum()),
        'improved_pairs': int((frame.ordinary_change_pct < -1e-9).sum()), 'worst_change_pct': float(frame.ordinary_change_pct.max()),
        'background_worst_change_pct': float(frame.background_change_pct.max()),
        'all_zero_teleports': bool(frame.teleports.eq(0).all()), 'all_zero_collisions': bool(frame.collisions.eq(0).all()),
        'all_conservation_zero': bool(frame.conservation_residual_abs.eq(0).all()),
        'scope': 'Per-condition results only; no weighted global mean or claim of real-world stable benefit'}
    save(SUITE / 'verification.json', result)
    return result


def run_suite():
    freeze()
    planned = [(s, seed, p) for s in SCENARIOS for seed in SEEDS for p in ['free', 'conditional']]
    def launch(item):
        s, seed, p = item
        r = subprocess.run([sys.executable, '-m', 'b_twin.conditional_governance_v2', '--one', s, str(seed), p],
            cwd=APP, capture_output=True, timeout=420, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        (SUITE / f'{s}_s{seed}_{p}.log').write_bytes(r.stdout + r.stderr)
        r.check_returncode()
        return {'scenario': s, 'seed': seed, 'policy': p, 'status': 'completed'}
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        work = {pool.submit(launch, x): x for x in planned}
        for f in as_completed(work):
            try:
                r = f.result()
            except Exception as exc:
                r = {'item': work[f], 'status': 'failed', 'error': str(exc)}
            results.append(r)
            save(SUITE / 'execution.json', results)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    assert all(r['status'] == 'completed' for r in results), 'Failures retained in execution.json'
    return report()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--one', nargs=3)
    p.add_argument('--freeze-only', action='store_true')
    args = p.parse_args()
    result = one(args.one[0], int(args.one[1]), args.one[2]) if args.one else freeze() if args.freeze_only else run_suite()
    print(json.dumps(result, ensure_ascii=False), flush=True)
