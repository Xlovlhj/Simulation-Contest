"""Finite speed-input diagnostic; historical experiments remain unchanged.

AI-assisted: OpenAI Codex / OpenAI. Actual model, release date and human
review must be completed from real records before competition submission.
This uses already inspected day17 and is NOT a new independent validation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import gzip
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pandas as pd

from .paths import APP, DATA, REFINED, sumo_exe
from .runner import digest, write_xml
from .evaluation import aggregate_e1, primary_metrics, vehicle_accounting, trip_accounting
from .holdout import joint_check


OUT = APP / 'results/stage12_speed_diagnostic'
EDGES = ['66348983', '66348976']


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def official_audit():
    """Join official speed records through their explicit ROAD_ROUTE_ID.

    A nearby road, including one directly below a bridge, cannot establish
    the speed limit on that bridge. No missing official value is imputed.
    """
    mapping = pd.read_csv(REFINED / 'detector_mapping.csv', dtype={'sumo_edge': str})
    official = pd.read_csv(DATA / 'processed/detector_official_mapping.csv', dtype={'ROUTE_ID': str})
    features = json.loads(gzip.decompress((DATA / 'processed/official_speed_limit.geojson.gz').read_bytes()))['features']
    by_id = {}
    for f in features:
        p = f['properties']
        by_id.setdefault(str(p['ROAD_ROUTE_ID']), []).append(p)
    rows = []
    for r in mapping.merge(official[['detector_id', 'ROUTE_ID', 'ELEVATION']], on='detector_id', validate='one_to_one').itertuples():
        values = by_id.get(r.ROUTE_ID, [])
        rows.append({'detector_id': r.detector_id, 'sumo_edge': r.sumo_edge,
            'official_route_id': r.ROUTE_ID, 'official_elevation': r.ELEVATION,
            'official_speed_records': ';'.join(sorted({v['SPEED_LIMIT'] for v in values})),
            'official_record_ids': ';'.join(str(v['SPEED_LIMIT_ID']) for v in values),
            'mapping_requires_review': bool(r.requires_review),
            'status': 'route_id_join_only_mapping_still_provisional' if values else 'NO_explicit_record_NOT_a_confirmed_speed_limit'})
    pd.DataFrame(rows).to_csv(OUT / 'official_speed_audit.csv', index=False, encoding='utf-8-sig')
    return rows


def one(source_name, suffix=''):
    source = APP / 'results' / source_name
    target = OUT / (source_name.replace('stage10_main', 'stage12_speed70') + suffix)
    target.mkdir(exist_ok=False)
    meta = json.loads((source / 'run.json').read_text(encoding='utf-8'))
    for name in ['network.net.xml', 'network_definition.json', 'detectors.add.xml', 'demand.rou.xml', 'vehicles.csv', 'run.sumocfg']:
        shutil.copyfile(source / name, target / name)
    tree = ET.parse(target / 'network.net.xml')
    changed = []
    for edge in tree.getroot().findall('edge'):
        if edge.get('id') in EDGES:
            for lane in edge.findall('lane'):
                old = float(lane.get('speed'))
                if abs(old * 3.6 - 50) > .1:
                    raise ValueError('Unexpected baseline speed; protocol invalid')
                lane.set('speed', str(70 / 3.6))
                changed.append({'lane': lane.get('id'), 'old_kmh': old * 3.6, 'new_kmh': 70.})
    if not changed or {x['lane'].rsplit('_', 1)[0] for x in changed} != set(EDGES):
        raise ValueError('Missing diagnostic edges')
    write_xml(tree.getroot(), target / 'network.net.xml')
    meta.update(run_id=target.name, source_run=source_name, status='running',
        diagnostic_role='known_day17_input_sensitivity_NOT_independent_validation',
        changed_lanes=changed, assumed_speed_kmh=70,
        speed_provenance='DESIGN_ALTERNATIVE_NOT_OFFICIAL_OR_ESTIMATED_TRUE_LIMIT',
        original_network_sha256=digest(source / 'network.net.xml'), network_sha256=digest(target / 'network.net.xml'))
    save(target / 'run.json', meta)
    try:
        response = subprocess.run([sumo_exe(), '-c', str(target / 'run.sumocfg'),
            '--log', str(target / 'sumo.log'), '--error-log', str(target / 'warnings.log')], capture_output=True, timeout=240)
        (target / 'process.log').write_bytes(response.stdout + response.stderr)
        response.check_returncode()
        start = pd.Timestamp(meta['physical_start']).tz_localize(None).isoformat()
        _, simulated = aggregate_e1(target / 'e1.xml', start, meta['evaluation_seconds'])
        # Retain the exact historical observation windows AND primary set.
        old = pd.read_csv(source / 'comparison_15min.csv')
        old_sim = [c for c in old if c.startswith('simulated_') or c in ['flow_absolute_error', 'speed_absolute_error_kmh']]
        compared = simulated.merge(old.drop(columns=old_sim), on=['detector_id', 'window'], validate='one_to_one', how='left')
        if len(compared) != len(old) or compared.used_in_primary_metrics.isna().any():
            raise ValueError('Evaluation support changed')
        compared['flow_absolute_error'] = abs(compared.simulated_flow_vph - compared.flow_vph_estimated)
        compared['speed_absolute_error_kmh'] = abs(compared.simulated_speed_kmh - compared.speed_kmh_observed)
        compared.to_csv(target / 'comparison_15min.csv', index=False, encoding='utf-8-sig')
        primary = primary_metrics(compared)
        primary['basis'] = 'Same known-day17 observations and primary station set as stage10; diagnostic only'
        base = json.loads((source / 'metrics.json').read_text(encoding='utf-8'))
        joint = joint_check(primary, base['joint_check']['possible_primary_station_windows'])
        stats = ET.parse(target / 'statistics.xml').getroot()
        accounting = vehicle_accounting(target / 'summary.xml', 300 + meta['evaluation_seconds'], .5, meta['planned_vehicles'])
        trips = trip_accounting(target, 300 + meta['evaluation_seconds'], meta['simulation_end_seconds'])
        result = {'primary_metrics': primary, 'joint_check': joint, 'accounting': accounting,
            'trip_accounting': trips, 'teleports': int(stats.find('teleports').get('total')),
            'collisions': int(stats.find('safety').get('collisions')), 'independent_validation': False}
        result['passed_joint_check'] = bool(joint['passed_error_checks'] and result['teleports'] == 0 and result['collisions'] == 0
            and accounting['conservation_residual'] == 0)
        save(target / 'metrics.json', result)
        meta['status'] = 'completed'
        return {'run_id': target.name, 'source_run': source.name, 'case': meta['case'], 'seed': meta['seed'],
            **{key + '_before': base['primary_metrics'][key] for key in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points']},
            **{key + '_after': primary[key] for key in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points']},
            'observed_slow': primary['observed_slow_station_windows'], 'reproduced_slow': primary['reproduced_slow_station_windows'],
            'passed_joint_check': result['passed_joint_check'], 'teleports': result['teleports'], 'collisions': result['collisions'],
            'conservation_residual': accounting['conservation_residual']}
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save(target / 'run.json', meta)


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    sources = [f'stage10_main_{case}_s{seed}_prior' for case in ['ordinary', 'peak'] for seed in [20261101, 20261102, 20261103]]
    audit = official_audit()
    save(OUT / 'protocol.json', {'frozen_at': datetime.now().astimezone().isoformat(), 'sources': sources,
        'changed_edges': EDGES, 'alternative_kmh': 70, 'baseline_kmh': 50,
        'scope': 'Six paired runs, known-day17 prior demand; no retuning, no model adoption, no unseen date read.',
        'reason': 'Official bridge speed records are absent; 70 is only a bounded diagnostic alternative to default 50.',
        'source_sha256': {n: {f: digest(APP / 'results' / n / f) for f in ['network.net.xml', 'vehicles.csv', 'demand.rou.xml', 'comparison_15min.csv']} for n in sources},
        'script_sha256': digest(__import__('pathlib').Path(__file__)),
        'official_sources_sha256': {n: digest(DATA / 'processed' / n) for n in ['official_speed_limit.geojson.gz', 'detector_official_mapping.csv']},
        'bridge_audit': [r for r in audit if r['sumo_edge'] in EDGES]})
    results, failures = [], []
    with ThreadPoolExecutor(3) as pool:
        tasks = {pool.submit(one, n): n for n in sources}
        for task in as_completed(tasks):
            try:
                row = task.result()
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({'run': tasks[task], 'error': str(exc)})
            save(OUT / 'execution.json', {'completed': results, 'failures': failures})
    pd.DataFrame(results).sort_values(['case', 'seed']).to_csv(OUT / 'paired_results.csv', index=False, encoding='utf-8-sig')
    if failures:
        raise RuntimeError('Diagnostic failures retained')


if __name__ == '__main__':
    main()
