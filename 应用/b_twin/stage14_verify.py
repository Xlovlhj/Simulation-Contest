"""Final extension audit and handover; historical packages remain unchanged.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
from __future__ import annotations

from datetime import datetime
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd

from .paths import APP, PROJECT
from .runner import digest
from .workday_extension import DATES, directory, check
from .conditional_governance import freeze as check_conditional, SCENARIOS, SEEDS
from .road_emergency import freeze as check_road, SEEDS as ROAD_SEEDS, POLICIES
from .wallclock_report import audit_session
from .aivas_audit import audit as audit_aivas


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    prior = PROJECT / '交付准备/第十二轮核验.json'
    prior_result = load(prior)
    assert digest(PROJECT / '交付准备' / prior_result['package']['package']) == prior_result['package']['sha256']
    for d in DATES:
        check(directory(d))
    check_conditional()
    check_road()
    planned = []
    for date in DATES:
        protocol = load(directory(date) / 'manifest.json')['protocol']
        planned += [f'stage14_day{date.replace("-", "")}_{case}_s{seed}_{policy}'
                    for case in protocol['cases'] for policy in protocol['policies'] for seed in protocol['seeds']]
    planned += [f'stage14_conditional_{s}_s{seed}_{p}' for s in SCENARIOS for seed in SEEDS for p in ['free', 'conditional']]
    planned += [f'stage14_road_s{seed}_{p}' for seed in ROAD_SEEDS for p in POLICIES]
    rows = []
    for name in planned:
        run = APP / 'results' / name
        m, metrics = load(run / 'run.json'), load(run / 'metrics.json')
        assert m['status'] == 'completed'
        for filename, key in [('network.net.xml', 'network_sha256'), ('demand.rou.xml', 'demand_sha256'),
            ('vehicles.csv', 'vehicle_manifest_sha256')]:
            assert digest(run / filename) == m[key], (name, filename)
        agents = pd.read_csv(run / 'vehicles.csv')
        trips = ET.parse(run / 'tripinfo.xml').getroot().findall('tripinfo')
        assert agents.vehicle_id.is_unique and len(trips) == len(agents)
        assert {t.get('id') for t in trips} == set(agents.vehicle_id)
        demand = ET.parse(run / 'demand.rou.xml').getroot().findall('vehicle')
        assert {v.get('id') for v in demand} == set(agents.vehicle_id)
        saved = agents.set_index('vehicle_id')
        for v in demand:
            assert abs(float(v.get('depart')) - saved.loc[v.get('id'), 'depart']) <= 1e-8
        stats = ET.parse(run / 'statistics.xml').getroot()
        assert int(stats.find('teleports').get('total')) == metrics['teleports'] == 0
        assert int(stats.find('safety').get('collisions')) == metrics['collisions'] == 0
        assert metrics['accounting']['conservation_residual'] == 0
        if name.startswith('stage14_conditional'):
            decisions = pd.read_csv(run / 'control_30s.csv')
            assert np.isfinite(decisions[['sensor_occupancy_pct', 'approach_halting', 'queue_fraction_assumed', 'green_seconds', 'rate_vph_nominal']].to_numpy()).all()
        rows.append({'run_id': name, 'planned_vehicles': len(agents), 'all_trip_ids_verified': True,
            'all_departure_plans_verified': True, 'network_sha256': digest(run / 'network.net.xml'),
            'vehicle_sha256': digest(run / 'vehicles.csv'), 'tripinfo_sha256': digest(run / 'tripinfo.xml'),
            'metrics_sha256': digest(run / 'metrics.json'), 'teleports': 0, 'collisions': 0, 'conservation_residual': 0})
    matched_trips = 0
    for s in SCENARIOS:
        for seed in SEEDS:
            paths = [APP / 'results' / f'stage14_conditional_{s}_s{seed}_{p}/tripinfo.xml' for p in ['free', 'conditional']]
            records = [{v.get('id'): (v.get('depart'), v.get('arrival')) for v in ET.parse(p).getroot().findall('tripinfo')} for p in paths]
            assert records[0] == records[1]
            matched_trips += len(records[0])
    state_path = APP / 'runtime/stage13_data_feasibility/continuous_test_state.json'
    state = load(state_path)
    assert state['status'] == 'completed_operational_audit_passed_NOT_traffic_accuracy', 'One-hour session is not yet finalized'
    live_name = next(j['output'].split('/')[-1] for j in state['jobs'] if j['name'].startswith('SUMO'))
    live_audit, _, _ = audit_session(live_name)
    aivas_path = PROJECT / next(j['output'] for j in state['jobs'] if j['name'].startswith('官方'))
    aivas_result = audit_aivas(aivas_path)
    frame = pd.DataFrame(rows)
    output = APP / 'results/stage14_overview'
    output.mkdir(exist_ok=True)
    frame.to_csv(output / 'all_extension_run_audits.csv', index=False, encoding='utf-8-sig')
    result = {'status': 'RESEARCH_SCOPE_FROZEN_WORKING_DRAFT_NOT_FOR_SUBMISSION',
        'checked_at': datetime.now().astimezone().isoformat(),
        'actual_runs': prior_result['actual_runs'] + len(rows) + 1,
        'previously_checked_runs': prior_result['actual_runs'], 'new_batch_runs': len(rows), 'new_wallclock_runs': 1,
        'new_batch_vehicle_records_checked': int(frame.planned_vehicles.sum()),
        'conditional_reference_vehicle_times_identical': matched_trips,
        'all_new_batch_input_hashes_and_vehicle_ids_verified': True,
        'all_new_batch_zero_teleports_collisions_conservation': True,
        'workday_validation': load(APP / 'results/stage14_workday_verification.json'),
        'conditional_governance': load(APP / 'results/stage14_conditional_suite/verification.json'),
        'road_emergency': load(APP / 'results/stage14_road_emergency_suite/verification.json'),
        'geometry': load(APP / 'results/stage14_geometry_audit/verification.json'),
        'identifiability': load(APP / 'results/stage14_identifiability/verification.json'),
        'wallclock': live_audit, 'aivas': aivas_result,
        'current_delivery_versions': {'paper': 'v2', 'support': 'v8', 'video': 'v1', 'contains_extension_results': False},
        'missing_data_resolved': False, 'real_traffic_accuracy_validated': False,
        'stable_governance_benefit_demonstrated': False, 'hospital_gate_verified': False,
        'road_task_does_not_depend_on_hospital_gate': True,
        'can_start_final_paper_revision_with_limited_claims': True,
        'human_review_completed': False,
        'previous_verification_sha256': digest(prior), 'this_verifier_sha256': digest(APP / 'b_twin/stage14_verify.py'),
        'scope': 'Prior 152 audits are referenced, not rerun here. New 75 batch runs and one live session audited; synthetic vehicle records include repeated paired agents.'}
    (PROJECT / '交付准备/第十四轮核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return {k: v for k, v in result.items() if k not in ['geometry', 'identifiability', 'workday_validation']}


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2))
