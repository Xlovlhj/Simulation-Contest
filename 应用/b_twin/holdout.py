"""Frozen cross-day validation with revision-aware archive replay.

AI-assisted: OpenAI Codex; retain actual use and human review records.
Historical plans are calculated in event time before a SUMO batch replay.
This is not a new wall-clock/TraCI live experiment.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ast
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, DATA, INPUTS, REFINED, PROJECT, sumo_exe
from .assimilation import counts_at, update_rates, stamp
from .demand import fixed_vehicles
from .evaluation import aggregate_e1, primary_metrics, vehicle_accounting, trip_accounting
from .runner import digest, write_xml
import sumolib

TZ = timezone(timedelta(hours=8))
PROTOCOL = {
    'version': 'crossday17_v1', 'date': '2026-10-17',
    'cases': {'ordinary': ['2026-10-17T11:15:00+08:00', 2700],
              'peak': ['2026-10-17T08:00:00+08:00', 3600]},
    'seeds': [20261101, 20261102, 20261103], 'policies': ['prior', 'rolling'],
    'warmup_seconds': 300, 'clearance_seconds': 900,
    'update_seconds': 300, 'lookback_seconds': 900, 'minimum_coverage': .8,
    'stale_seconds': 600, 'maximum_step_fraction': .25,
    'minimum_change_reference_vph': 10., 'entry_ridge': .5,
    'network': 'through', 'background_vph': 0, 'supply_vph': 0,
    'prior': 'frozen_day15_case_mean_no_day17_training',
    'validation_minimum_coverage': .6, 'validation_maximum_gap_seconds': 180,
    'joint_limits': {'flow_wmape_pct': 15., 'speed_mae_kmh': 5.,
                     'occupancy_mae_percentage_points': 5.,
                     'slow_recall_minimum': .8, 'false_slow_fraction_maximum': .1,
                     'minimum_station_window_fraction': .5},
}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def suite_dir(prefix):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', prefix):
        raise ValueError('Invalid prefix')
    return APP / 'results' / (prefix + '_suite')


def freeze(prefix):
    """Freeze rules and model files BEFORE opening unseen numerical records."""
    output = suite_dir(prefix)
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'frozen_inputs'
    inputs.mkdir()
    sources = [REFINED / 'detector_mapping.csv', REFINED / 'corridor_definition.json',
               REFINED / 'corridor.net.xml', INPUTS / 'vehicle_class_priors.csv']
    sources += [INPUTS / (case + suffix) for case in PROTOCOL['cases']
                for suffix in ['_inferred_routes.csv', '_diagnostic.rou.xml']]
    old = APP / 'results/stage06_main_ordinary_prior'
    sources += [old / name for name in ['network.net.xml', 'network_definition.json', 'detectors.add.xml']]
    hashes = {}
    for source in sources:
        target = inputs / source.name
        shutil.copyfile(source, target)
        hashes[source.relative_to(PROJECT).as_posix()] = digest(target)
    lanes = pd.read_csv(DATA / 'processed/traffic_lane_30s.csv.gz', usecols=['detector_id', 'lane_id'])
    expected = {did: sorted(map(str, g.lane_id.unique())) for did, g in lanes.groupby('detector_id')}
    save_json(inputs / 'expected_lanes.json', expected)
    hashes['derived_expected_lanes'] = digest(inputs / 'expected_lanes.json')
    archived = DATA / 'raw/holdout_20261017/20261017.zip'
    if not archived.is_file():
        raise FileNotFoundError('Download official day17 archive first; no numeric parsing has occurred')
    hashes[archived.relative_to(PROJECT).as_posix()] = digest(archived)
    frozen_sources = output / 'frozen_sources'
    frozen_sources.mkdir()
    code = {}
    for name in ['holdout.py', 'holdout_report.py', 'assimilation.py', 'demand.py', 'evaluation.py', 'runner.py', 'paths.py']:
        source = APP / 'b_twin' / name
        shutil.copyfile(source, frozen_sources / name)
        code[name] = digest(source)
    plan = PROJECT / 'docs/第十轮独立验证方案.md'
    shutil.copyfile(plan, output / plan.name)
    save_json(output / 'manifest.json', {
        'frozen_at': datetime.now(TZ).isoformat(), 'protocol': PROTOCOL,
        'input_sha256': hashes, 'source_sha256': code, 'plan_sha256': digest(plan),
        'source_archive': archived.relative_to(PROJECT).as_posix(),
        'scope': 'Unseen day17; fixed dates/windows before numeric inspection. Thursday prior vs Saturday test.',
    })
    return output


def assert_frozen(output):
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['protocol'] != PROTOCOL:
        raise ValueError('Protocol changed; use a new prefix')
    amendment_file = output / 'review_revision.json'
    amendments = json.loads(amendment_file.read_text(encoding='utf-8')) if amendment_file.exists() else {}
    for name, value in manifest['source_sha256'].items():
        expected = amendments.get('current_sha256', {}).get(name, value)
        if digest(APP / 'b_twin' / name) != expected or digest(output / 'frozen_sources' / name) != value:
            raise ValueError('Frozen source changed: ' + name)
        if expected != value:
            if name not in {'holdout.py', 'holdout_report.py'}:
                raise ValueError('Only review code amendments permitted')
            if name == 'holdout.py':
                def simulation_ast(path):
                    tree = ast.parse(path.read_text(encoding='utf-8'))
                    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
                                 and not (isinstance(node, ast.FunctionDef) and node.name == 'assert_frozen')]
                    return ast.dump(tree, include_attributes=False)
                if simulation_ast(APP / 'b_twin' / name) != simulation_ast(output / 'frozen_sources' / name):
                    raise ValueError('Amendment changed simulation logic')
    for name, value in manifest['input_sha256'].items():
        p = output / 'frozen_inputs/expected_lanes.json' if name == 'derived_expected_lanes' else PROJECT / name
        if digest(p) != value:
            raise ValueError('Frozen input changed: ' + name)
        if not name.endswith('.zip') and digest(output / 'frozen_inputs' / p.name) != value:
            raise ValueError('Frozen copy changed: ' + name)
    return manifest


def latest_revisions(stream, decision=None):
    """Latest known record first; a subsequent invalid record revokes old data."""
    known = stream
    if decision is not None:
        now = stamp(decision)
        known = stream[(pd.to_datetime(stream.available_at, utc=True) <= now)
                       & (pd.to_datetime(stream.observation_end, utc=True) <= now)]
    return known.sort_values(['available_at', 'source_member'], kind='stable').drop_duplicates(
        ['detector_id', 'observation_start', 'observation_end'], keep='last')


def causal_counts(stream, decision, mapping):
    # counts_at filters valid records; explicitly resolve ALL revisions first.
    return counts_at(latest_revisions(stream, decision), decision, mapping, PROTOCOL)


def parse_archive(output):
    manifest = assert_frozen(output)
    expected = json.loads((output / 'frozen_inputs/expected_lanes.json').read_text(encoding='utf-8'))
    detector_pattern = re.compile(r'<detector><detector_id>(' + '|'.join(sorted(expected)) +
                                  r')</detector_id>.*?</detector>', re.DOTALL)
    rows, members = [], []
    with zipfile.ZipFile(PROJECT / manifest['source_archive']) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith('.xml'):
                continue
            payload = archive.read(name)
            text = payload.decode('utf-8-sig')
            capture = re.search(r'(\d{8}-\d{4})-raw', name).group(1)
            available = stamp(datetime.strptime(capture, '%Y%m%d-%H%M'))
            date = re.search(r'<date>(.*?)</date>', text).group(1)
            members.append({'member': name, 'available_at': available.isoformat(),
                            'sha256': hashlib.sha256(payload).hexdigest()})
            for period in re.findall(r'<period>(.*?)</period>', text, re.DOTALL):
                begin = stamp(date + 'T' + re.search(r'<period_from>(.*?)</period_from>', period).group(1))
                end = stamp(date + 'T' + re.search(r'<period_to>(.*?)</period_to>', period).group(1))
                if end <= begin:
                    end += pd.Timedelta(days=1)
                for match in detector_pattern.finditer(period):
                    node = ET.fromstring(match.group(0))
                    did = node.findtext('detector_id')
                    lanes = node.findall('./lanes/lane')
                    def number(lane, tag):
                        try:
                            return float(lane.findtext(tag))
                        except (ValueError, TypeError):
                            return float('nan')
                    counts = np.array([number(lane, 'volume') for lane in lanes])
                    speeds = np.array([number(lane, 'speed') for lane in lanes])
                    occ = np.array([number(lane, 'occupancy') for lane in lanes])
                    labels = [lane.findtext('lane_id') for lane in lanes]
                    valid = (sorted(labels) == expected[did] and len(set(labels)) == len(labels)
                             and all(lane.findtext('valid') == 'Y' for lane in lanes)
                             and (end - begin).total_seconds() == 30 and available >= end
                             and np.isfinite([counts, speeds, occ]).all()
                             and (counts >= 0).all() and (speeds >= 0).all()
                             and ((occ >= 0) & (occ <= 100)).all())
                    rows.append({'detector_id': did, 'observation_start': begin.isoformat(),
                        'observation_end': end.isoformat(), 'available_at': available.isoformat(),
                        'quality': 'valid' if valid else 'invalid_or_incomplete',
                        'count': float(counts.sum()) if valid else None,
                        'speed_numerator': float((counts * speeds).sum()) if valid else None,
                        'occupancy_pct': float(occ.mean()) if valid else None,
                        'source_member': name, 'availability_source': 'official_archive_capture_proxy_NOT_local_receipt'})
    stream = pd.DataFrame(rows)
    stream.to_csv(output / 'observation_revisions.csv.gz', index=False, compression='gzip')
    save_json(output / 'archive_members.json', members)
    truth = latest_revisions(stream)
    truth.to_csv(output / 'final_station_30s.csv.gz', index=False, compression='gzip')
    estimates = estimate_truth(truth, expected)
    estimates.to_csv(output / 'validation_15min.csv', index=False, encoding='utf-8-sig')
    report = {'archive_members': len(members), 'station_revisions': len(stream), 'unique_station_periods': len(truth),
              'eligible_windows': int(estimates.eligible.sum()), 'station_windows': len(estimates),
              'stream_sha256': digest(output / 'observation_revisions.csv.gz'),
              'truth_sha256': digest(output / 'validation_15min.csv'),
              'extraction_at': datetime.now(TZ).isoformat()}
    save_json(output / 'extraction.json', report)
    return report


def estimate_truth(truth, expected):
    rows = []
    start = stamp(PROTOCOL['date'])
    for did in sorted(expected):
        station = truth[truth.detector_id.eq(did)]
        for window in pd.date_range(start, periods=96, freq='15min'):
            g = station[(pd.to_datetime(station.observation_start, utc=True) >= window)
                        & (pd.to_datetime(station.observation_end, utc=True) <= window + pd.Timedelta(minutes=15))]
            g = g[g.quality.eq('valid')]
            n = len(g)
            # Valid 30-second intervals must lie on the declared time grid.
            slots = set(pd.to_datetime(g.observation_start, utc=True).map(stamp))
            grid = set(pd.date_range(window, periods=30, freq='30s'))
            if not slots.issubset(grid):
                raise ValueError('Off-grid observation')
            run = gap = 0
            for t in sorted(grid):
                run = 0 if t in slots else run + 1
                gap = max(gap, run)
            count = float(g['count'].sum()) if n else np.nan
            rows.append({'detector_id': did, 'window': window.tz_localize(None).isoformat(),
                'coverage': n / 30, 'complete_intervals': n, 'max_missing_run_seconds': gap * 30,
                'flow_vph_estimated': count * 120 / n if n else np.nan,
                'speed_kmh_observed': float(g.speed_numerator.sum() / count) if count > 0 else np.nan,
                'occupancy_pct_observed': float(g.occupancy_pct.mean()) if n else np.nan,
                'eligible': n / 30 >= .6 and gap * 30 <= 180,
                'provenance': 'final_revisions_aligned_lanes_time_expansion_NOT_full_count'})
    return pd.DataFrame(rows)


def make_plan(output, case, policy, seed):
    frozen = output / 'frozen_inputs'
    routes = pd.read_csv(frozen / (case + '_inferred_routes.csv'))
    fleet = pd.read_csv(frozen / 'vehicle_class_priors.csv')
    fleet = fleet[fleet.case.eq(case)]
    mapping = pd.read_csv(frozen / 'detector_mapping.csv', dtype={'sumo_edge': str})
    core = json.loads((frozen / 'corridor_definition.json').read_text(encoding='utf-8'))['core_edges']
    mapping = mapping[mapping.sumo_edge.isin(core)]
    stream = pd.read_csv(output / 'observation_revisions.csv.gz') if policy == 'rolling' else None
    original = sumolib.net.readNet(str(frozen / 'corridor.net.xml'))
    start, duration = PROTOCOL['cases'][case]
    origin = stamp(start) - pd.Timedelta(seconds=300)
    previous = routes.estimated_vph.to_numpy(dtype=float)
    all_agents, segments, decisions, constraints = [], [], [], []
    for slot, now in enumerate(range(0, 300 + duration, 300)):
        decision = origin + pd.Timedelta(seconds=now)
        if policy == 'rolling':
            counts = causal_counts(stream, decision, mapping)
            rates, selected, info = update_rates(routes, counts, mapping, previous, PROTOCOL)
            selected['slot'] = slot
            constraints.append(selected)
        else:
            rates = routes.estimated_vph.to_numpy(dtype=float)
            info = {'constraints': 0, 'evidence': 'frozen_day15_prior_NO_day17_records_read'}
        frame = pd.DataFrame([{'route_id': route.route_id, 'direction': route.direction,
            'source_window': decision.isoformat(), 'begin': now, 'end': now + 300,
            'phase': 'warmup' if now < 300 else 'evaluation', 'estimated_vph': float(rate),
            'evidence': info['evidence']} for route, rate in zip(routes.itertuples(), rates)])
        agents, _ = fixed_vehicles(routes, frame, fleet, original, seed + slot, case)
        agents['vehicle_id'] = f'update{slot:03d}_' + agents.vehicle_id
        agents['planned_at'] = decision.isoformat()
        if len(agents) and ((agents.depart < now).any() or (agents.depart >= now + 300).any()):
            raise ValueError('Future-only plan contract failed')
        decisions.append({'slot': slot, 'simulation_seconds': now, 'decision_at': decision.isoformat(),
                          'constraints': info['constraints'], 'action': info['evidence'],
                          'future_vehicles_added': len(agents), 'admitted_boundary_vph': float(rates.sum())})
        all_agents.append(agents)
        segments.append(frame)
        previous = rates.copy()
    return (pd.concat(all_agents).sort_values(['depart', 'vehicle_id']).reset_index(drop=True),
            pd.concat(segments, ignore_index=True), decisions,
            pd.concat(constraints, ignore_index=True) if constraints else pd.DataFrame())


def joint_check(metrics, possible):
    limits = PROTOCOL['joint_limits']
    observed = metrics['observed_slow_station_windows']
    n = metrics['station_windows']
    recall = metrics['reproduced_slow_station_windows'] / observed if observed else None
    false_fraction = metrics['false_slow_station_windows'] / n if n else None
    passed = (n >= possible * limits['minimum_station_window_fraction'] and n > 0
              and metrics['speed_missing_simulated_windows'] == 0
              and all(metrics[k] is not None and np.isfinite(metrics[k]) and metrics[k] <= limits[k]
                      for k in ['flow_wmape_pct', 'speed_mae_kmh', 'occupancy_mae_percentage_points'])
              and (recall is None or recall >= limits['slow_recall_minimum'])
              and false_fraction <= limits['false_slow_fraction_maximum'])
    return {'passed_error_checks': bool(passed), 'slow_recall': recall, 'false_slow_fraction': false_fraction,
            'possible_primary_station_windows': possible, 'limits_are_project_defined_not_contest_rules': True}


def evaluate_holdout(run, output, metadata):
    start = stamp(metadata['physical_start']).tz_localize(None).isoformat()
    duration = metadata['evaluation_seconds']
    intervals, simulated = aggregate_e1(run / 'e1.xml', start, duration)
    intervals.to_csv(run / 'e1_intervals.csv', index=False, encoding='utf-8-sig')
    simulated.to_csv(run / 'simulated_15min.csv', index=False, encoding='utf-8-sig')
    observed = pd.read_csv(output / 'validation_15min.csv')
    compared = simulated.merge(observed, on=['detector_id', 'window'], how='left', validate='one_to_one')
    mapping = pd.read_csv(output / 'frozen_inputs/detector_mapping.csv', dtype={'sumo_edge': str})
    compared = compared.merge(mapping[['detector_id', 'requires_review']], on='detector_id', validate='many_to_one')
    core = json.loads((output / 'frozen_inputs/corridor_definition.json').read_text(encoding='utf-8'))['core_edges']
    ids = set(mapping[mapping.sumo_edge.isin(core) & ~mapping.requires_review].detector_id)
    compared['used_in_primary_metrics'] = compared.eligible.fillna(False) & compared.detector_id.isin(ids)
    compared['flow_absolute_error'] = abs(compared.simulated_flow_vph - compared.flow_vph_estimated)
    compared['speed_absolute_error_kmh'] = abs(compared.simulated_speed_kmh - compared.speed_kmh_observed)
    compared.to_csv(run / 'comparison_15min.csv', index=False, encoding='utf-8-sig')
    metrics = primary_metrics(compared)
    metrics['basis'] = 'Frozen original core stations; unseen day17 eligible windows'
    for k, v in list(metrics.items()):
        if isinstance(v, float) and not np.isfinite(v):
            metrics[k] = None
    stats = ET.parse(run / 'statistics.xml').getroot()
    accounting = vehicle_accounting(run / 'summary.xml', 300 + duration, .5, metadata['planned_vehicles'])
    trips = trip_accounting(run, 300 + duration, metadata['simulation_end_seconds'])
    joint = joint_check(metrics, len(ids) * (duration // 900))
    result = {'case': metadata['case'], 'policy': metadata['policy'], 'seed': metadata['seed'],
        'primary_metrics': metrics, 'joint_check': joint, 'accounting': accounting, 'trip_accounting': trips,
        'teleports': int(stats.find('teleports').get('total')), 'collisions': int(stats.find('safety').get('collisions')),
        'role': 'crossday_open_loop' if metadata['policy'] == 'prior' else 'crossday_eventtime_tracking_NOT_forecast'}
    result['passed_joint_check'] = bool(joint['passed_error_checks'] and result['teleports'] == 0
                                      and result['collisions'] == 0 and accounting['conservation_residual'] == 0)
    save_json(run / 'metrics.json', result)
    return result


def execute_one(output, case, policy, seed, prefix):
    run = APP / 'results' / f'{prefix}_{case}_s{seed}_{policy}'
    if run.exists():
        metadata = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        if metadata.get('status') == 'completed':
            return {'run_id': run.name, 'status': 'already_completed'}
        raise RuntimeError('Existing failed/incomplete run preserved: ' + run.name)
    run.mkdir()
    frozen = output / 'frozen_inputs'
    for name in ['network.net.xml', 'network_definition.json', 'detectors.add.xml']:
        shutil.copyfile(frozen / name, run / name)
    metadata = {'run_id': run.name, 'case': case, 'policy': policy, 'seed': seed,
                'physical_start': PROTOCOL['cases'][case][0], 'evaluation_seconds': PROTOCOL['cases'][case][1],
                'simulation_end_seconds': 300 + PROTOCOL['cases'][case][1] + 900,
                'status': 'preparing', 'protocol': PROTOCOL, 'network_sha256': digest(run / 'network.net.xml')}
    save_json(run / 'run.json', metadata)
    started = time.perf_counter()
    try:
        agents, segments, decisions, constraints = make_plan(output, case, policy, seed)
        agents.to_csv(run / 'vehicles.csv', index=False, encoding='utf-8-sig')
        segments.to_csv(run / 'demand_segments.csv', index=False, encoding='utf-8-sig')
        constraints.to_csv(run / 'constraints.csv', index=False, encoding='utf-8-sig')
        save_json(run / 'decisions.json', decisions)
        template = ET.parse(frozen / (case + '_diagnostic.rou.xml')).getroot()
        root = ET.Element('routes')
        types = {node.get('id'): node.attrib for node in template.findall('vType')}
        for (base, factor), g in agents.groupby(['base_type', 'speed_factor_assumed']):
            ET.SubElement(root, 'vType', **dict(types[base], id=f'stream_{base}_{round(factor*1000)}',
                                               speedFactor=str(factor), speedDev='0'))
        routes = pd.read_csv(frozen / (case + '_inferred_routes.csv'))
        definition = json.loads((frozen / 'network_definition.json').read_text(encoding='utf-8'))
        net = sumolib.net.readNet(str(run / 'network.net.xml'))
        for route in routes.itertuples():
            edges = route.edges.split()
            edges += definition['route_continuations'].get(edges[-1], [])
            for a, b in zip(edges, edges[1:]):
                if net.getEdge(b) not in net.getEdge(a).getOutgoing():
                    raise ValueError('Disconnected route')
            classes = agents.loc[agents.route_id.eq(route.route_id), 'sumo_vclass'].unique()
            if not all(net.getEdge(edge).allows(c) for edge in edges for c in classes):
                raise ValueError('Forbidden vehicle class')
            ET.SubElement(root, 'route', id=route.route_id, edges=' '.join(edges))
        for a in agents.itertuples():
            ET.SubElement(root, 'vehicle', id=a.vehicle_id, route=a.route_id, depart=str(a.depart),
                type=f'stream_{a.base_type}_{round(a.speed_factor_assumed*1000)}', departLane=a.depart_lane, departSpeed=a.depart_speed)
        write_xml(root, run / 'demand.rou.xml')
        cfg = ET.Element('configuration')
        for section, settings in {
            'input': {'net-file': 'network.net.xml', 'route-files': 'demand.rou.xml', 'additional-files': 'detectors.add.xml'},
            'time': {'step-length': '.5', 'end': str(metadata['simulation_end_seconds'])},
            'processing': {'time-to-teleport': '-1'},
            'random_number': {'seed': str(seed)},
            'output': {'summary-output': 'summary.xml', 'statistic-output': 'statistics.xml', 'tripinfo-output': 'tripinfo.xml',
                       'tripinfo-output.write-unfinished': 'true', 'tripinfo-output.write-undeparted': 'true'},
            'report': {'no-step-log': 'true', 'duration-log.disable': 'true', 'xml-validation': 'never'},
        }.items():
            node = ET.SubElement(cfg, section)
            for key, value in settings.items():
                ET.SubElement(node, key, value=value)
        write_xml(cfg, run / 'run.sumocfg')
        metadata.update(planned_vehicles=len(agents), vehicle_manifest_sha256=digest(run / 'vehicles.csv'),
                        demand_sha256=digest(run / 'demand.rou.xml'), status='running')
        save_json(run / 'run.json', metadata)
        result = subprocess.run([sumo_exe(), '-c', str(run / 'run.sumocfg'), '--log', str(run / 'sumo.log'),
                                 '--error-log', str(run / 'warnings.log')], capture_output=True, timeout=240)
        (run / 'process.log').write_bytes(result.stdout + result.stderr)
        result.check_returncode()
        evaluate_holdout(run, output, metadata)
        metadata['status'] = 'completed'
    except Exception as exc:
        metadata.update(status='failed', error=str(exc))
        raise
    finally:
        metadata['elapsed_seconds'] = time.perf_counter() - started
        save_json(run / 'run.json', metadata)
    return {'run_id': run.name, 'status': 'completed'}


def run_holdout(prefix='stage10_main', jobs=3):
    output = suite_dir(prefix)
    if not output.exists():
        freeze(prefix)
    assert_frozen(output)
    if not (output / 'extraction.json').exists():
        print(json.dumps({'extraction': parse_archive(output)}, ensure_ascii=False), flush=True)
    else:
        report = json.loads((output / 'extraction.json').read_text(encoding='utf-8'))
        if digest(output / 'observation_revisions.csv.gz') != report['stream_sha256'] or digest(output / 'validation_15min.csv') != report['truth_sha256']:
            raise ValueError('Derived validation records changed')
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=max(1, min(3, jobs))) as executor:
        work = {executor.submit(execute_one, output, case, policy, seed, prefix): (case, policy, seed)
                for case in PROTOCOL['cases'] for policy in PROTOCOL['policies'] for seed in PROTOCOL['seeds']}
        for future in as_completed(work):
            try:
                row = future.result()
                results.append(row)
            except Exception as exc:
                failures.append({'item': work[future], 'error': str(exc)})
            print(json.dumps({'completed': len(results), 'failed': len(failures), 'last': results[-1] if results else None}), flush=True)
    execution = {'results': results, 'failures': failures, 'at': datetime.now(TZ).isoformat()}
    save_json(output / ('execution.json' if not (output / 'execution.json').exists() else 'execution_resume.json'), execution)
    if failures:
        raise RuntimeError('Holdout failures preserved; see execution.json')
    return {'suite': str(output), 'runs': len(results)}
