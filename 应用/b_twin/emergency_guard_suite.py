"""Freeze the new emergency rule before viewing fresh paired-seed results."""
# AI-assisted: OpenAI Codex; actual use and human review retained for submission.
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
import os
import subprocess
import sys

from .paths import APP,INPUTS,REFINED
from .runner import digest
from .emergency_guard import GUARD
from .emergency_access import build_emergency_access


def run_guarded_suite(prefix='stage07_main',jobs=3):
    if not prefix.replace('_','').isalnum() or not 1<=jobs<=3:
        raise ValueError('Invalid suite prefix or jobs')
    model=build_emergency_access()
    runs=[{'run_id':f'{prefix}_s{seed}_{policy}','seed':seed,'policy':policy}
        for seed in range(20261023,20261028) for policy in ['baseline','guarded','guarded_green']]
    names=['emergency_guard.py','emergency_guard_suite.py','emergency_guard_report.py',
        'emergency.py','emergency_access.py','runner.py','evaluation.py','governance.py',
        'governance_report.py','demand.py','background.py','boundary_supply.py']
    inputs=[INPUTS/'peak_inferred_routes.csv',INPUTS/'vehicle_class_priors.csv',
        INPUTS/'peak_diagnostic.rou.xml',REFINED/'traffic_15min_estimates.csv',REFINED/'detector_mapping.csv']
    spec={'runs':runs,'guard_protocol':GUARD,
        'source_sha256':{name:digest(APP/'b_twin'/name) for name in names},
        'input_sha256':{p.relative_to(APP.parent).as_posix():digest(p) for p in inputs},
        'network_sha256_before_supply_and_meters':model['network_sha256'],
        'background_vph':900,'supply_vph':900,'entry_ridge':.5,'clearance_seconds':1800,
        'rationale':'New seeds after diagnosing old 17--21; no retuning or selection within this suite',
        'scope':'Designed event with full shared agents; normal passenger permissions; unverified hospital gate'}
    directory=APP/'results'/(prefix+'_suite')
    if directory.exists():
        old=json.loads((directory/'suite.json').read_text(encoding='utf-8'))['specification']
        unchanged_plan={k:v for k,v in old.items() if k!='source_sha256'}=={k:v for k,v in spec.items() if k!='source_sha256'}
        simulation_names=set(names)-{'emergency_guard_report.py','emergency_guard_suite.py'}
        unchanged_simulation=all(old['source_sha256'].get(n)==spec['source_sha256'][n] for n in simulation_names)
        if not unchanged_plan or not unchanged_simulation:
            raise ValueError('Frozen guarded suite changed; use a new prefix')
        for name,sha in old['source_sha256'].items():
            if digest(directory/'frozen_sources'/name)!=sha:
                raise ValueError('Frozen source archive changed')
    else:
        (directory/'frozen_sources').mkdir(parents=True)
        for name in names:
            (directory/'frozen_sources'/name).write_bytes((APP/'b_twin'/name).read_bytes())
        (directory/'suite.json').write_text(json.dumps({'created_at':datetime.now().astimezone().isoformat(),
            'specification':spec},ensure_ascii=False,indent=2),encoding='utf-8')
    # Reporting/driver repairs may be archived independently; neither may
    # change the frozen run table, parameters, inputs or simulation sources.
    for name in ['emergency_guard_report.py','emergency_guard_suite.py']:
        current=APP/'b_twin'/name
        digest_now=digest(current)
        revision=directory/'review_tools'/digest_now
        revision.mkdir(parents=True,exist_ok=True)
        archived=revision/name
        if archived.exists() and digest(archived)!=digest_now:
            raise ValueError('Review tool archive changed')
        if not archived.exists():
            archived.write_bytes(current.read_bytes())
    def one(item):
        output=APP/'results'/item['run_id']
        if output.exists():
            if json.loads((output/'run.json').read_text(encoding='utf-8')).get('status')=='completed':
                return {**item,'status':'already_completed'}
            raise ValueError('Incomplete run retained; use a new prefix')
        args=[sys.executable,str(APP/'run.py'),'guarded-emergency-run','--policy',item['policy'],
            '--seed',str(item['seed']),'--run-id',item['run_id']]
        result=subprocess.run(args,capture_output=True,timeout=360,
            env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        (directory/(item['run_id']+'.log')).write_bytes(result.stdout+result.stderr)
        result.check_returncode()
        return {**item,'status':'completed'}
    results=[]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending={pool.submit(one,r):r for r in runs}
        for future in as_completed(pending):
            try:
                item=future.result()
            except Exception as exc:
                item={**pending[future],'status':'failed','error':str(exc)}
            results.append(item)
            (directory/'execution.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(item,ensure_ascii=False),flush=True)
    if any(r['status']=='failed' for r in results):
        raise RuntimeError('Guarded suite has failures; all evidence retained')
    return {'suite':str(directory),'completed_runs':len(results)}
