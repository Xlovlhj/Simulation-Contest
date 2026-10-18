"""Bounded waiting-protection experiment; old frozen engines stay unchanged.

AI-assisted: OpenAI Codex. Disclose modeling, code and analysis assistance.
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

import numpy as np
import pandas as pd
from .paths import APP, PROJECT
from .runner import digest, run_traci, write_xml
from .stress import SCENARIOS, SOURCES, prepare_stress, load, save
from .conditional_governance_v2 import ConditionalMeter, transition
from .governance import UPDATE, YELLOW, SATURATION, STARTUP, feedback_rate, phase_state
from .evaluation import WARMUP, evaluate
from . import governance
import traci.constants as tc

SUITE = APP / 'results/stage16_waiting_suite'
DEVELOPMENT = [20261121, 20261122]
VALIDATION = [20261201, 20261202, 20261203]
CANDIDATES = {'wait120': 120., 'wait240': 240.}


def freeze():
    files = list(dict.fromkeys(SOURCES + ['conditional_governance_v2.py', 'waiting_optimization.py']))
    spec = {'candidates': CANDIDATES, 'development_seeds': DEVELOPMENT, 'validation_seeds': VALIDATION,
            'scenarios': SCENARIOS, 'waiting_memory_s': 60000,
            'source_sha256': {n: digest(APP / 'b_twin' / n) for n in files},
            'plan_sha256': digest(PROJECT / 'docs/第十六轮有限优化方案.md'),
            'original_manifest_sha256': digest(APP / 'results/stage15_conditional_suite/manifest.json')}
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


class WaitingMeter(ConditionalMeter):
    def __init__(self, connection, output, policy, plan):
        super().__init__(connection, output, policy, plan)
        meta = load(output / 'run.json')
        self.variant = meta['waiting_variant']
        self.cap = CANDIDATES.get(self.variant)
        agents = pd.read_csv(output / 'vehicles.csv')
        self.planned = {r.vehicle_id: float(r.depart) for r in agents.itertuples()}
        self.groups = {s['id']: set(agents.loc[agents.route_id.isin(s['routes']), 'vehicle_id']) for s in plan['facilities']}
        self.departed = {}
        self.restrict_since = {s['id']: None for s in plan['facilities']}
        self.release_until = {s['id']: 0. for s in plan['facilities']}
        self.wait_lock = {s['id']: False for s in plan['facilities']}
        self.extra = (output / 'waiting_guard.jsonl').open('w', encoding='utf-8')

    def step(self, now):
        for vid in self.c.simulation.getDepartedIDList():
            self.departed[vid] = now
        for s in self.plan['facilities']:
            values = self.samples[s['id']]
            values.append(100. * self.c.edge.getSubscriptionResults(s['sensor_edge'])[tc.LAST_STEP_OCCUPANCY])
            del values[:-int(UPDATE / .5)]
        if now + 1e-7 >= self.next_update:
            for s in self.plan['facilities']:
                key = s['id']
                occ = float(np.mean(self.samples[key]))
                queue = sum(self.c.edge.getSubscriptionResults(e)[tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for e in s['approach_edges'])
                fraction = queue / s['storage_slots_assumed']
                ids = self.groups[key]
                pending = sorted(v for v in ids if self.planned[v] <= now and v not in self.departed)
                live = sorted({v for edge in s['approach_edges'] for v in self.c.edge.getLastStepVehicleIDs(edge)} & ids)
                waits = {v: now - self.planned[v] for v in pending}
                for v in live:
                    waits[v] = self.c.vehicle.getAccumulatedWaitingTime(v) + self.departed[v] - self.planned[v]
                maximum = max(waits.values(), default=0.)
                state, reason = transition(self.states[key], occ, fraction)
                if self.variant == 'free':
                    state = dict(state, active=False)
                    reason = 'reference_all_green'
                elif self.cap is not None:
                    if maximum >= self.cap:
                        self.wait_lock[key] = True
                        self.release_until[key] = max(self.release_until[key], now + UPDATE)
                    elif self.wait_lock[key] and maximum <= self.cap / 2:
                        self.wait_lock[key] = False
                    since = self.restrict_since[key]
                    if since is not None and now - since >= self.cap:
                        self.release_until[key] = max(self.release_until[key], now + UPDATE)
                    if self.wait_lock[key] or now < self.release_until[key]:
                        state = dict(state, active=False)
                        reason = 'waiting_release_all_green' if self.wait_lock[key] else 'duration_release_all_green'
                self.states[key] = state
                if state['active']:
                    self.rates[key], _ = feedback_rate(self.rates[key], occ, fraction, s['maximum_vph'])
                    green = min(UPDATE - YELLOW - .5, UPDATE * self.rates[key] / (s['lanes'] * SATURATION) + STARTUP)
                    if self.restrict_since[key] is None:
                        self.restrict_since[key] = now
                else:
                    self.rates[key], green = s['maximum_vph'], UPDATE
                    self.restrict_since[key] = None
                self.greens[key] = green
                self.writer.writerow({'simulation_seconds': now, 'meter': key, 'policy': self.variant,
                    'sensor_occupancy_pct': occ, 'approach_halting': queue, 'queue_fraction_assumed': fraction,
                    'rate_vph_nominal': self.rates[key], 'green_seconds': green, 'reason': reason})
                self.extra.write(json.dumps({'simulation_seconds': now, 'meter': key, 'pending_ids': pending,
                    'live_approach_ids': live, 'waiting_seconds_by_id': waits, 'maximum_wait_s': maximum,
                    'wait_lock': self.wait_lock[key], 'release_until': self.release_until[key],
                    'restrict_since': self.restrict_since[key], 'reason': reason}) + '\n')
                self.samples[key].clear()
            self.extra.flush()
            self.handle.flush()
            self.next_update += UPDATE
        for s in self.plan['facilities']:
            key = s['id']
            state = 'G' if now < WARMUP or not self.states[key]['active'] else phase_state(now - WARMUP, self.greens[key])
            self.set_state(s, state)

    def close(self):
        self.extra.close()
        super().close()


def one(scenario, seed, variant):
    name = f'stage16_wait_{scenario}_s{seed}_{variant}'
    output = APP / 'results' / name
    if output.exists():
        assert load(output / 'run.json')['status'] == 'completed', 'Failed output retained'
        return {'run_id': name, 'status': 'already_completed'}
    output, meta = prepare_stress('governance', 'free' if variant == 'free' else 'feedback', scenario, seed, name)
    meta.update(waiting_variant=variant, waiting_memory_s=60000)
    cfg = ET.parse(output / 'run.sumocfg').getroot()
    processing = cfg.find('processing')
    ET.SubElement(processing, 'waiting-time-memory', value='60000')
    write_xml(cfg, output / 'run.sumocfg')
    save(output / 'run.json', meta)
    governance.MeterController = WaitingMeter
    started = time.perf_counter()
    try:
        run_traci(output, meta)
        result = evaluate(output, 'peak', meta)
        meta.update(status='completed', elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save(output / 'run.json', meta)
    return {'run_id': name, 'teleports': result['teleports'], 'collisions': result['collisions']}


def costs(run, horizon=None):
    meta = load(run / 'run.json')
    agents = pd.read_csv(run / 'vehicles.csv')
    trips = pd.DataFrame([e.attrib for e in ET.parse(run / 'tripinfo.xml').getroot().findall('tripinfo')])
    assert set(trips.id) == set(agents.vehicle_id) and len(trips) == len(agents)
    trips = trips.rename(columns={'depart': 'actual_depart'})
    data = agents.merge(trips[['id', 'arrival', 'actual_depart', 'waitingTime']], left_on='vehicle_id', right_on='id', validate='one_to_one')
    for col in ['arrival', 'actual_depart', 'waitingTime']:
        data[col] = pd.to_numeric(data[col])
    horizon = horizon or WARMUP + meta['evaluation_seconds']
    end = meta['simulation_end_seconds']
    data['cost'] = np.maximum(0, np.minimum(np.where(data.arrival.ge(0), data.arrival, np.inf), horizon) - np.maximum(WARMUP, data.depart))
    data['wait_lower'] = np.maximum(0, np.where(data.actual_depart.ge(0), data.actual_depart, end) - data.depart) + np.maximum(0, data.waitingTime)
    result = {'ordinary_hours': float(data.loc[~data.population.eq('emergency'), 'cost'].sum() / 3600),
        'background_hours': float(data.loc[data.population.eq('background'), 'cost'].sum() / 3600),
        'core_hours': float(data.loc[data.population.eq('core'), 'cost'].sum() / 3600),
        'unfinished': int(data.loc[~data.population.eq('emergency'), 'arrival'].lt(0).sum()),
        'pending_end': int(data.loc[~data.population.eq('emergency'), 'actual_depart'].lt(0).sum())}
    for pop in ['background', 'core']:
        g = data.loc[data.population.eq(pop), 'wait_lower']
        result[pop + '_p95_wait_lower_s'] = float(g.quantile(.95))
        result[pop + '_max_wait_lower_s'] = float(g.max())
    return result


def paired_report(items, phase):
    rows = []
    for scenario, seed, candidate in items:
        run = APP / 'results' / f'stage16_wait_{scenario}_s{seed}_{candidate}'
        if phase == 'development':
            original = APP / 'results' / f'stage15_conditional_{scenario}_s{seed}_conditional'
            free = APP / 'results' / f'stage15_conditional_{scenario}_s{seed}_free'
        else:
            original = APP / 'results' / f'stage16_wait_{scenario}_s{seed}_original'
            free = APP / 'results' / f'stage16_wait_{scenario}_s{seed}_free'
        for name in ['vehicles.csv', 'network.net.xml', 'demand.rou.xml']:
            assert len({digest(p / name) for p in [run, original, free]}) == 1, name
        a, b, f = costs(original), costs(run), costs(free)
        m = load(run / 'metrics.json')
        rows.append({'scenario': scenario, 'seed': seed, 'candidate': candidate,
            'total_change_from_original_pct': 100 * (b['ordinary_hours'] / a['ordinary_hours'] - 1),
            'background_change_from_original_pct': 100 * (b['background_hours'] / a['background_hours'] - 1),
            'total_change_from_free_pct': 100 * (b['ordinary_hours'] / f['ordinary_hours'] - 1),
            'background_change_from_free_pct': 100 * (b['background_hours'] / f['background_hours'] - 1),
            'unfinished_change_from_original': b['unfinished'] - a['unfinished'],
            **{'candidate_' + k: v for k, v in b.items()},
            'original_background_p95_wait_lower_s': a['background_p95_wait_lower_s'],
            'original_background_max_wait_lower_s': a['background_max_wait_lower_s'],
            'teleports': m['teleports'], 'collisions': m['collisions'], 'conservation_residual': m['accounting']['conservation_residual']})
    frame = pd.DataFrame(rows)
    frame.to_csv(SUITE / f'{phase}_paired.csv', index=False, encoding='utf-8-sig')
    decisions = {}
    for candidate, g in frame.groupby('candidate'):
        means = g.groupby('scenario')[['total_change_from_original_pct', 'background_change_from_original_pct', 'unfinished_change_from_original']].mean()
        passed = (means.total_change_from_original_pct.max() <= 1 and g.total_change_from_original_pct.max() <= 2
            and g.background_change_from_original_pct.mean() <= -10 and means.background_change_from_original_pct.max() <= 0
            and means.unfinished_change_from_original.max() <= 5 and g.teleports.eq(0).all()
            and g.collisions.eq(0).all() and g.conservation_residual.eq(0).all())
        decisions[candidate] = {'passed': bool(passed), 'pair_count': len(g),
            'selection_score_background_mean_pct': float(g.background_change_from_original_pct.mean()),
            'worst_pair_total_change_pct': float(g.total_change_from_original_pct.max()),
            'worst_condition_mean_total_change_pct': float(means.total_change_from_original_pct.max()),
            'worst_condition_mean_unfinished_change': float(means.unfinished_change_from_original.max()),
            'scope': 'Unweighted finite design selection score, not real-world expected benefit'}
    save(SUITE / f'{phase}_decision.json', decisions)
    return decisions


def batch(items):
    def launch(item):
        scenario, seed, variant = item
        cmd = [sys.executable, '-m', 'b_twin.waiting_optimization', '--one', scenario, str(seed), variant]
        r = subprocess.run(cmd, cwd=APP, capture_output=True, timeout=600, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        (SUITE / f'{scenario}_s{seed}_{variant}.log').write_bytes(r.stdout + r.stderr)
        r.check_returncode()
        return {'scenario': scenario, 'seed': seed, 'variant': variant, 'status': 'completed'}
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(launch, item) for item in items]):
            results.append(future.result())
            save(SUITE / 'execution_progress.json', results)
            print(json.dumps(results[-1]), flush=True)


def main():
    freeze()
    development = [(s, seed, p) for s in SCENARIOS for seed in DEVELOPMENT for p in CANDIDATES]
    batch(development)
    decision = paired_report(development, 'development')
    candidates = [p for p, d in decision.items() if d['passed']]
    selected = min(candidates, key=lambda p: decision[p]['selection_score_background_mean_pct']) if candidates else None
    save(SUITE / 'selection.json', {'selected_before_validation': selected, 'development': decision})
    if selected:
        validation = [(s, seed, p) for s in SCENARIOS for seed in VALIDATION for p in ['free', 'original', selected]]
        batch(validation)
        result = paired_report([(s, seed, selected) for s in SCENARIOS for seed in VALIDATION], 'validation')
        adopted = selected if result[selected]['passed'] else None
    else:
        result, adopted = None, None
    save(SUITE / 'final_decision.json', {'adopted': adopted, 'original_retained': adopted is None,
        'development_runs': len(development), 'validation_runs': 63 if selected else 0, 'validation': result})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--one', nargs=3)
    args = p.parse_args()
    result = one(args.one[0], int(args.one[1]), args.one[2]) if args.one else main()
    print(json.dumps(result, ensure_ascii=False), flush=True)
