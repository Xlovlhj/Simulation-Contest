"""Predeclared light/medium/heavy road event comparisons, all seeds retained.

AI-assisted: OpenAI Codex; scenarios are design assumptions, not field incidents.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import pandas as pd
from .paths import APP, PROJECT
from .stress import SOURCES, load, save
from .runner import digest, run_traci
from . import governance
from .conditional_governance_v2 import ConditionalMeter
from . import road_emergency_v2 as road
from .emergency_guard import GuardedController
from .emergency_report import audit_emergency
from .emergency_guard_report import audit_guard
from .evaluation import evaluate
from .waiting_optimization import costs

SUITE = APP/'results/stage16_emergency_scope_suite'
CONDITIONS = {'light':6., 'medium':3., 'heavy':1.}
SEEDS = [20261221,20261222,20261223]
POLICIES = ['free','A','AB']


def freeze():
    files=list(dict.fromkeys(SOURCES+['emergency_scope_optimization.py','road_emergency_v2.py',
        'conditional_governance_v2.py','waiting_optimization.py','emergency_report.py','emergency_guard_report.py']))
    spec={'conditions_mps':CONDITIONS,'seeds':SEEDS,'policies':POLICIES,
        'plan_sha256':digest(PROJECT/'docs/第十六轮有限优化方案.md'),
        'source_sha256':{n:digest(APP/'b_twin'/n) for n in files},
        'road_geometry_sha256':digest(APP/'models/emergency_v1/corridor.net.xml'),
        'source_suite_sha256':digest(APP/'results/stage15_road_emergency_v2_suite/manifest.json')}
    if SUITE.exists():
        assert load(SUITE/'manifest.json')['specification']==spec
    else:
        (SUITE/'frozen_sources').mkdir(parents=True)
        for n in files:
            shutil.copyfile(APP/'b_twin'/n,SUITE/'frozen_sources'/n)
        save(SUITE/'manifest.json',{'frozen_at':datetime.now().astimezone().isoformat(),'specification':spec})
    return spec


def one(condition,seed,policy):
    name=f'stage16_road_{condition}_s{seed}_{policy}'
    out=APP/'results'/name
    if out.exists():
        assert load(out/'run.json')['status']=='completed','Failed output retained'
        return {'run_id':name,'status':'already_completed'}
    previous=road.prepare_guarded
    road.prepare_guarded=lambda s,p,r: previous(s,p,name)
    try:
        out,meta=road.prepare(seed,'guarded_green' if policy=='AB' else 'baseline')
    finally:
        road.prepare_guarded=previous
    scenario=meta['emergency_scenario']
    scenario['incident'].update(speed_limit_mps_assumed=CONDITIONS[condition],label=f'{condition}: predeclared single-lane work-zone design, not observed incident')
    save(out/'emergency_scenario.json',scenario)
    meta.update(scope_condition=condition,scope_policy=policy,meter_policy='free' if policy=='free' else 'feedback',
        emergency_scenario=scenario,emergency_scenario_sha256=digest(out/'emergency_scenario.json'))
    save(out/'run.json',meta)
    governance.MeterController=ConditionalMeter
    started=time.perf_counter()
    try:
        run_traci(out,meta,GuardedController)
        evaluate(out,'peak',meta)
        meta.update(status='completed',elapsed_seconds=time.perf_counter()-started)
    except Exception as exc:
        meta.update(status='failed',error=str(exc))
        raise
    finally:
        save(out/'run.json',meta)
    return {'run_id':name,'status':'completed'}


def report():
    rows=[]
    for condition in CONDITIONS:
        for seed in SEEDS:
            runs={p:APP/'results'/f'stage16_road_{condition}_s{seed}_{p}' for p in POLICIES}
            for name in ['network.net.xml','vehicles.csv','demand.rou.xml','emergency_scenario.json']:
                assert len({digest(r/name) for r in runs.values()})==1,name
            for p,run in runs.items():
                meta=load(run/'run.json')
                m=load(run/'metrics.json')
                task=audit_emergency(run,meta)
                task.update(audit_guard(run,meta))
                rows.append({'condition':condition,'seed':seed,'policy':p,'run_id':run.name,
                    **costs(run,horizon=5700),**task,'teleports':m['teleports'],'collisions':m['collisions'],
                    'conservation_residual':m['accounting']['conservation_residual']})
    frame=pd.DataFrame(rows)
    frame.to_csv(SUITE/'all_runs.csv',index=False,encoding='utf-8-sig')
    pairs=[]
    for (condition,seed),g in frame.groupby(['condition','seed']):
        g=g.set_index('policy')
        for p in ['A','AB']:
            a=g.loc['free' if p=='A' else 'A']
            b=g.loc[p]
            pairs.append({'condition':condition,'seed':seed,'contrast':'A_vs_free' if p=='A' else 'AB_vs_A',
                'task_change_pct':100*(b.rescue_elapsed_lower_bound_s/a.rescue_elapsed_lower_bound_s-1),
                'ordinary_change_pct':100*(b.ordinary_hours/a.ordinary_hours-1),
                'background_change_pct':100*(b.background_hours/a.background_hours-1),
                'reference_completed':bool(a.rescue_completed),'candidate_completed':bool(b.rescue_completed),
                'reference_task_s':a.rescue_elapsed_lower_bound_s,'candidate_task_s':b.rescue_elapsed_lower_bound_s})
    paired=pd.DataFrame(pairs)
    paired.to_csv(SUITE/'paired_changes.csv',index=False,encoding='utf-8-sig')
    decisions={}
    for condition,g in paired[paired.contrast.eq('AB_vs_A')].groupby('condition'):
        passed=(g.reference_completed.all() and g.candidate_completed.all() and g.task_change_pct.mean()<=-5
            and g.task_change_pct.max()<=2 and g.ordinary_change_pct.mean()<=1 and g.background_change_pct.mean()<=5)
        decisions[condition]={'extra_coordination_accepted':bool(passed),'mean_task_change_pct':float(g.task_change_pct.mean()),
            'worst_task_change_pct':float(g.task_change_pct.max()),'mean_ordinary_change_pct':float(g.ordinary_change_pct.mean())}
    save(SUITE/'decision.json',{'actual_runs':len(frame),'conditions':decisions,
        'all_zero_teleports':bool(frame.teleports.eq(0).all()),'all_zero_collisions':bool(frame.collisions.eq(0).all()),
        'all_conservation_zero':bool(frame.conservation_residual.eq(0).all()),'all_task_arrived':bool(frame.rescue_completed.all()),
        'yield_requests':int(frame.yield_requests.sum()),'green_extensions':int(frame.green_extensions.sum()),
        'scope':'All predeclared severity/seed combinations; finite design comparison, not real rescue validation'})
    return load(SUITE/'decision.json')


def main():
    freeze()
    items=[(c,s,p) for c in CONDITIONS for s in SEEDS for p in POLICIES]
    def launch(item):
        c,s,p=item
        r=subprocess.run([sys.executable,'-m','b_twin.emergency_scope_optimization','--one',c,str(s),p],cwd=APP,
            capture_output=True,timeout=600,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        (SUITE/f'{c}_s{s}_{p}.log').write_bytes(r.stdout+r.stderr)
        r.check_returncode()
        return {'condition':c,'seed':s,'policy':p,'status':'completed'}
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for f in as_completed([pool.submit(launch,i) for i in items]):
            results.append(f.result())
            save(SUITE/'execution.json',results)
            print(json.dumps(results[-1]),flush=True)
    return report()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--one',nargs=3)
    args=p.parse_args()
    print(json.dumps(one(args.one[0],int(args.one[1]),args.one[2]) if args.one else main(),ensure_ascii=False),flush=True)
