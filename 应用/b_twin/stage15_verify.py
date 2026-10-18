"""Audit corrected-unit extensions and retain superseded results as history.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
from datetime import datetime
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from .paths import APP, PROJECT
from .runner import digest
from .stage14_verify import main as verify_previous_extension
from .conditional_governance_v2 import freeze as check_governance, audit as audit_control, SCENARIOS, SEEDS
from .road_emergency_v2 import freeze as check_road, SEEDS as ROAD_SEEDS, POLICIES
from .road_free_reference import freeze as check_free_reference


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    verify_previous_extension()
    previous = load(PROJECT / '交付准备/第十四轮核验.json')
    check_governance()
    check_road()
    check_free_reference()
    names = [f'stage15_conditional_{s}_s{seed}_{p}' for s in SCENARIOS for seed in SEEDS for p in ['free', 'conditional']]
    names += [f'stage15_road_s{seed}_{p}' for seed in ROAD_SEEDS for p in POLICIES]
    names += [f'stage15_road_s{seed}_baseline_free_reference' for seed in ROAD_SEEDS]
    rows, decisions_checked = [], 0
    for name in names:
        output = APP / 'results' / name
        meta, m = load(output / 'run.json'), load(output / 'metrics.json')
        assert meta['status'] == 'completed'
        for filename, key in [('network.net.xml', 'network_sha256'), ('demand.rou.xml', 'demand_sha256'), ('vehicles.csv', 'vehicle_manifest_sha256')]:
            assert digest(output / filename) == meta[key], (name, filename)
        agents = pd.read_csv(output / 'vehicles.csv')
        trips = ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo')
        assert agents.vehicle_id.is_unique and len(trips) == len(agents)
        assert {t.get('id') for t in trips} == set(agents.vehicle_id)
        demand = ET.parse(output / 'demand.rou.xml').getroot().findall('vehicle')
        assert {v.get('id') for v in demand} == set(agents.vehicle_id)
        by_id = agents.set_index('vehicle_id')
        for v in demand:
            assert abs(float(v.get('depart')) - by_id.loc[v.get('id'), 'depart']) < 1e-8
        stats = ET.parse(output / 'statistics.xml').getroot()
        assert int(stats.find('teleports').get('total')) == m['teleports']
        assert int(stats.find('safety').get('collisions')) == m['collisions']
        assert m['accounting']['conservation_residual'] == 0
        log = pd.read_csv(output / 'control_30s.csv')
        assert np.isfinite(log[['sensor_occupancy_pct', 'queue_fraction_assumed', 'green_seconds']].to_numpy()).all()
        assert log.sensor_occupancy_pct.between(0, 100 + 1e-7).all()
        if meta['meter_policy'] != 'free':
            audit = audit_control(output, meta)
            decisions_checked += audit['decisions_checked']
        else:
            assert log.reason.eq('reference_all_green').all() and log.green_seconds.eq(30).all()
        rows.append({'run_id': name, 'vehicle_records': len(agents), 'teleports': m['teleports'],
            'collisions': m['collisions'], 'conservation_residual': 0,
            'network_sha256': digest(output / 'network.net.xml'), 'demand_sha256': digest(output / 'demand.rou.xml'),
            'vehicle_sha256': digest(output / 'vehicles.csv'), 'tripinfo_sha256': digest(output / 'tripinfo.xml'),
            'control_sha256': digest(output / 'control_30s.csv')})
    frame = pd.DataFrame(rows)
    out = APP / 'results/stage15_overview'
    out.mkdir(exist_ok=True)
    frame.to_csv(out / 'all_corrected_run_audits.csv', index=False, encoding='utf-8-sig')
    probe = load(APP / 'results/stage15_unit_check/verification.json')
    assert abs(probe['edge_api_raw'] - probe['geometric_occupied_fraction']) < 1e-8
    assert probe['e1_xml_percent'] == 100 and probe['edge_to_percent_factor'] == 100
    result = {'status': 'RESEARCH_RESULTS_FROZEN_WORKING_DRAFT_NOT_FOR_SUBMISSION',
        'checked_at': datetime.now().astimezone().isoformat(), 'actual_runs': previous['actual_runs'] + len(frame),
        'previously_checked_runs': 152, 'stage14_batch_runs': 75, 'stage15_corrected_batch_runs': len(frame), 'new_wallclock_runs': 1,
        'corrected_vehicle_records_checked': int(frame.vehicle_records.sum()), 'corrected_control_decisions_checked': decisions_checked,
        'all_corrected_input_hashes_and_vehicle_ids_verified': True,
        'all_corrected_zero_teleports': bool(frame.teleports.eq(0).all()), 'all_corrected_zero_collisions': bool(frame.collisions.eq(0).all()),
        'all_corrected_conservation_zero': True, 'occupancy_unit_probe': probe,
        'corrected_governance': load(APP / 'results/stage15_conditional_suite/verification.json'),
        'corrected_road_emergency': load(APP / 'results/stage15_road_emergency_v2_suite/verification.json'),
        'additional_road_free_reference': load(APP / 'results/stage15_road_free_reference_suite/verification.json'),
        'wallclock': previous['wallclock'], 'aivas': previous['aivas'],
        'unseen_workday_joint_passes': 0, 'unseen_workday_new_runs': 24,
        'real_traffic_accuracy_validated': False, 'stable_real_world_governance_benefit_demonstrated': False,
        'hospital_gate_verified': False, 'road_task_does_not_require_hospital_gate': True,
        'can_start_final_paper_revision_with_limited_claims': True, 'human_review_completed': False,
        'existing_delivery_files_contain_corrected_models': False,
        'previous_verification_sha256': digest(PROJECT / '交付准备/第十四轮核验.json'),
        'this_verifier_sha256': digest(APP / 'b_twin/stage15_verify.py'),
        'scope': '129 new batch runs plus one wallclock run audited. Historical wrong-unit outputs remain for diagnosis; no claims of independent real vehicle samples or official submit readiness.'}
    (PROJECT / '交付准备/第十五轮核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2))
