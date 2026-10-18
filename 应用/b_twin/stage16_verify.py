"""Independent accounting and decision audit for the bounded optimization.

AI-assisted: OpenAI Codex; retains failed-performance and original versions.
"""
from datetime import datetime
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from .paths import APP, PROJECT
from .runner import digest
from .stress import load, save
from .evaluation import primary_metrics
from .waiting_optimization import freeze as check_waiting, CANDIDATES, DEVELOPMENT, SCENARIOS
from .baseline_optimization import freeze as check_baseline
from .emergency_scope_optimization import freeze as check_emergency
from .governance import feedback_rate, UPDATE, YELLOW, SATURATION, STARTUP
from .conditional_governance_v2 import transition, audit as audit_original_control
from .emergency_report import audit_emergency
from .emergency_guard_report import audit_guard


def audit_waiting(run, meta, agents, trips):
    log=pd.read_csv(run/'control_30s.csv')
    observations=[json.loads(line) for line in (run/'waiting_guard.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(log)==len(observations)
    cap=CANDIDATES[meta['waiting_variant']]
    by_vehicle=agents.set_index('vehicle_id')
    trip_by_id={t.get('id'):t.attrib for t in trips}
    count=0
    for spec in meta['meter_plan']['facilities']:
        samples=[r for r in observations if r['meter']==spec['id']]
        rows=log[log.meter.eq(spec['id'])]
        assert np.allclose(rows.simulation_seconds,np.arange(300,meta['simulation_end_seconds']+.01,30))
        group=set(agents.loc[agents.route_id.isin(spec['routes']),'vehicle_id'])
        state={'active':False,'blocked':False,'high':0,'low':0}
        locked=False
        until=0.
        since=None
        rate=spec['initial_vph']
        for r,o in zip(rows.itertuples(),samples):
            now=float(r.simulation_seconds)
            assert now==o['simulation_seconds']
            expected_pending={v for v in group if by_vehicle.loc[v,'depart']<=now
                and (float(trip_by_id[v]['depart'])<0 or float(trip_by_id[v]['depart'])>=now-.001)}
            assert set(o['pending_ids'])==expected_pending
            assert set(o['live_approach_ids'])<=group
            assert set(o['waiting_seconds_by_id'])==set(o['pending_ids'])|set(o['live_approach_ids'])
            for v,w in o['waiting_seconds_by_id'].items():
                assert w>=-.02
                if v in expected_pending:
                    assert np.isclose(w,now-by_vehicle.loc[v,'depart'])
                else:
                    t=trip_by_id[v]
                    assert 0<=float(t['depart'])<=now+.01
                    assert float(t['arrival'])<0 or float(t['arrival'])>=now-.01
                    # TraCI announces insertion after its step: receipt = XML depart + .5 s.
                    # The controller's waiting proxy uses that causal receipt time.
                    upper=float(t['waitingTime'])+float(t['depart'])+.5-by_vehicle.loc[v,'depart']
                    assert w<=upper+.04,(v,w,upper)
            maximum=max(o['waiting_seconds_by_id'].values(),default=0)
            assert np.isclose(o['maximum_wait_s'],maximum)
            fraction=r.approach_halting/spec['storage_slots_assumed']
            assert np.isclose(fraction,r.queue_fraction_assumed)
            state,reason=transition(state,r.sensor_occupancy_pct,fraction)
            if maximum>=cap:
                locked=True
                until=max(until,now+30)
            elif locked and maximum<=cap/2:
                locked=False
            if since is not None and now-since>=cap:
                until=max(until,now+30)
            if locked or now<until:
                state=dict(state,active=False)
                reason='waiting_release_all_green' if locked else 'duration_release_all_green'
            if state['active']:
                rate,_=feedback_rate(rate,r.sensor_occupancy_pct,fraction,spec['maximum_vph'])
                green=min(UPDATE-YELLOW-.5,UPDATE*rate/(spec['lanes']*SATURATION)+STARTUP)
                since=now if since is None else since
            else:
                rate,green=spec['maximum_vph'],UPDATE
                since=None
            assert reason==r.reason==o['reason']
            assert o['wait_lock']==locked and np.isclose(o['release_until'],until) and o['restrict_since']==since
            assert np.isclose(r.rate_vph_nominal,rate) and np.isclose(r.green_seconds,green)
            count+=1
    return count


def main():
    check_waiting()
    check_baseline()
    check_emergency()
    for name in ['stage16_waiting_suite','stage16_calibration_suite','stage16_emergency_scope_suite']:
        suite=APP/'results'/name
        spec=load(suite/'manifest.json')['specification']
        for filename,sha in spec['source_sha256'].items():
            assert digest(suite/'frozen_sources'/filename)==sha
            assert digest(APP/'b_twin'/filename)==sha
        assert digest(PROJECT/'docs/第十六轮有限优化方案.md')==spec['plan_sha256']
        for relative,sha in spec.get('input_sha256',{}).items():
            assert digest(PROJECT/relative)==sha
    rows=[]
    decisions=vehicles=emergency_controls=0
    for run in sorted((APP/'results').glob('stage16_*')):
        if not (run/'run.json').exists():
            continue
        meta=load(run/'run.json')
        assert meta['status']=='completed',run.name
        for n,key in [('network.net.xml','network_sha256'),('vehicles.csv','vehicle_manifest_sha256'),('demand.rou.xml','demand_sha256')]:
            assert digest(run/n)==meta[key],(run.name,n)
        agents=pd.read_csv(run/'vehicles.csv')
        trips=ET.parse(run/'tripinfo.xml').getroot().findall('tripinfo')
        assert len(trips)==len(agents) and agents.vehicle_id.is_unique
        assert set(t.get('id') for t in trips)==set(agents.vehicle_id)
        planned=ET.parse(run/'demand.rou.xml').getroot().findall('vehicle')
        assert set(v.get('id') for v in planned)==set(agents.vehicle_id)
        by_id=agents.set_index('vehicle_id')
        for v in planned:
            assert np.isclose(float(v.get('depart')),by_id.loc[v.get('id'),'depart'])
        vehicles+=len(agents)
        m=load(run/'metrics.json')
        c=pd.read_csv(run/'comparison_15min.csv')
        recompute=primary_metrics(c)
        for key in ['flow_wmape_pct','speed_mae_kmh','occupancy_mae_percentage_points']:
            assert np.isclose(m['primary_metrics'][key],recompute[key],equal_nan=True)
        stats=ET.parse(run/'statistics.xml').getroot()
        assert int(stats.find('teleports').get('total'))==m['teleports']==0
        assert int(stats.find('safety').get('collisions'))==m['collisions']==0
        arrived=sum(float(t.get('arrival'))>=0 for t in trips)
        assert arrived+m['trip_accounting']['unfinished_after_clearance']==len(agents)
        assert m['accounting']['conservation_residual']==0
        if meta.get('waiting_variant') in CANDIDATES:
            decisions+=audit_waiting(run,meta,agents,trips)
        if meta.get('scope_policy'):
            if meta['meter_policy']!='free':
                emergency_controls+=audit_original_control(run,meta)['decisions_checked']
            else:
                log=pd.read_csv(run/'control_30s.csv')
                assert log.reason.eq('reference_all_green').all() and log.green_seconds.eq(30).all()
            audit_emergency(run,meta)
            audit_guard(run,meta)
        rows.append({'run_id':run.name,'planned':len(agents),'teleports':0,'collisions':0,'conservation_residual':0,
            'network_sha256':digest(run/'network.net.xml'),'demand_sha256':digest(run/'demand.rou.xml'),
            'vehicles_sha256':digest(run/'vehicles.csv'),'tripinfo_sha256':digest(run/'tripinfo.xml'),
            'metrics_sha256':digest(run/'metrics.json')})
    output=APP/'results/stage16_overview'
    output.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(output/'all_run_audits.csv',index=False,encoding='utf-8-sig')
    old=load(PROJECT/'交付准备/第十五轮核验.json')
    result={'status':'BOUNDED_OPTIMIZATION_AUDITED_NOT_OFFICIAL_SUBMISSION_READY','checked_at':datetime.now().astimezone().isoformat(),
        'actual_runs':old['actual_runs']+len(rows),'previous_runs':old['actual_runs'],'new_optimization_runs':len(rows),
        'vehicle_records_checked':vehicles,'waiting_decisions_checked':decisions,
        'emergency_background_control_decisions_checked':emergency_controls,'all_input_hashes_and_ids_checked':True,
        'all_zero_teleports':True,'all_zero_collisions':True,'all_conservation_zero':True,
        'waiting_selection':load(APP/'results/stage16_waiting_suite/final_decision.json'),
        'calibration_selection':load(APP/'results/stage16_calibration_suite/selection.json'),
        'emergency_decision':load(APP/'results/stage16_emergency_scope_suite/decision.json'),
        'previous_verification_sha256':digest(PROJECT/'交付准备/第十五轮核验.json'),
        'scope':'Finite predeclared candidate search, not a global optimum proof. Cross-run synthetic agents are not real independent vehicle samples.',
        'human_review_completed':False,'existing_delivery_files_updated':False}
    save(PROJECT/'交付准备/第十六轮核验.json',result)
    return result


if __name__=='__main__':
    print(json.dumps(main(),ensure_ascii=False,indent=2),flush=True)
