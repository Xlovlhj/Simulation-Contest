"""One finite calibration pass on day15; no unseen-day tuning.

AI-assisted: OpenAI Codex. All parameter choices remain modeling assumptions.
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
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
from .paths import APP, PROJECT, INPUTS, REFINED, sumo_exe
from .runner import prepare_run, write_xml, digest
from .evaluation import evaluate
from .stress import load, save, SOURCES

SUITE = APP / 'results/stage16_calibration_suite'
SEED = 20261210
# entry regularization, background veh/h, exit supply veh/h, driver tau
CANDIDATES = {
 'original': (.5, 0, 0, None), 'tau15': (.5, 0, 0, 1.5),
 's600': (.5, 0, 600, None), 's900': (.5, 0, 900, None), 's1200': (.5, 0, 1200, None),
 'bg900': (.5, 900, 0, None), 'bgs600': (.5, 900, 600, None),
 'bgs900': (.5, 900, 900, None), 'bgs1200': (.5, 900, 1200, None),
 'ridge0': (0., 0, 0, None), 'ridge0s900': (0., 900, 900, None), 'bgs900tau15': (.5, 900, 900, 1.5)}


def freeze():
    sources = list(dict.fromkeys(SOURCES + ['baseline_optimization.py']))
    paths = [INPUTS / n for n in ['peak_inferred_routes.csv', 'ordinary_inferred_routes.csv', 'vehicle_class_priors.csv',
          'peak_diagnostic.rou.xml', 'ordinary_diagnostic.rou.xml']]
    paths += [REFINED / n for n in ['traffic_15min_estimates.csv', 'detector_mapping.csv', 'corridor_definition.json']]
    spec = {'seed': SEED, 'candidates': {k:list(v) for k,v in CANDIDATES.items()},
       'training_date': '2026-10-15', 'plan_sha256': digest(PROJECT / 'docs/第十六轮有限优化方案.md'),
       'source_sha256': {n:digest(APP/'b_twin'/n) for n in sources},
       'input_sha256': {p.relative_to(PROJECT).as_posix():digest(p) for p in paths},
       'holdout_contract': 'If training passes, freeze one candidate; day14 prior rates are train-day per-route means, background fixed design rate, tau and supply unchanged, ordinary/peak x 3 seeds x original/candidate. No test-day refit.'}
    if SUITE.exists():
        assert load(SUITE/'manifest.json')['specification'] == spec
    else:
        (SUITE/'frozen_sources').mkdir(parents=True)
        for n in sources:
            shutil.copyfile(APP/'b_twin'/n, SUITE/'frozen_sources'/n)
        save(SUITE/'manifest.json', {'frozen_at':datetime.now().astimezone().isoformat(),'specification':spec})
    return spec


def one(name):
    out = APP/'results'/f'stage16_calibration_{name}_s{SEED}'
    if out.exists():
        assert load(out/'run.json')['status']=='completed', 'Failed calibration retained'
        return {'candidate':name,'status':'already_completed'}
    ridge,bg,supply,tau = CANDIDATES[name]
    out,meta=prepare_run('peak','through',SEED,out.name,'time15',1800,ridge,bg,supply)
    cfg=ET.parse(out/'run.sumocfg').getroot()
    processing=cfg.find('processing')
    if processing is None:
        processing=ET.SubElement(cfg,'processing')
    jump=processing.find('time-to-teleport')
    if jump is None:
        ET.SubElement(processing,'time-to-teleport',value='-1')
    else:
        jump.set('value','-1')
    write_xml(cfg,out/'run.sumocfg')
    if tau:
        routes=ET.parse(out/'demand.rou.xml').getroot()
        for v in routes.findall('vType'):
            v.set('tau',str(tau))
        write_xml(routes,out/'demand.rou.xml')
    meta.update(calibration_candidate=name,driver_tau_assumed=tau,teleport_timeout_seconds=-1,
        demand_sha256=digest(out/'demand.rou.xml'))
    save(out/'run.json',meta)
    started=time.perf_counter()
    try:
        r=subprocess.run([sumo_exe(),'-c',str(out/'run.sumocfg'),'--log',str(out/'sumo.log'),
             '--error-log',str(out/'warnings.log')],capture_output=True,timeout=420)
        (out/'process.log').write_bytes(r.stdout+r.stderr)
        r.check_returncode()
        evaluate(out,'peak',meta)
        meta.update(status='completed')
    except Exception as exc:
        meta.update(status='failed',error=str(exc))
        raise
    finally:
        meta['elapsed_seconds']=time.perf_counter()-started
        save(out/'run.json',meta)
    return {'candidate':name,'status':'completed'}


def report():
    rows=[]
    for name in CANDIDATES:
        out=APP/'results'/f'stage16_calibration_{name}_s{SEED}'
        m=load(out/'metrics.json')
        p=m['primary_metrics']
        missed=p['missed_slow_station_windows']/max(1,p['observed_slow_station_windows'])
        false=p['false_slow_station_windows']/max(1,p['station_windows'])
        valid=(m['teleports']==0 and m['collisions']==0 and m['accounting']['conservation_residual']==0
            and p['speed_missing_simulated_windows']==0)
        score=p['flow_wmape_pct']/15+p['speed_mae_kmh']/5+p['occupancy_mae_percentage_points']/5+missed+false
        rows.append({'candidate':name,'score':score,'valid':valid,**p,'teleports':m['teleports'],
            'collisions':m['collisions'],'conservation_residual':m['accounting']['conservation_residual'],
            'unfinished':m['trip_accounting']['unfinished_after_clearance']})
    frame=pd.DataFrame(rows)
    original=frame[frame.candidate.eq('original')].iloc[0]
    eligible=(frame.valid & frame.score.le(original.score*.9)
        & frame.flow_wmape_pct.le(original.flow_wmape_pct*1.1)
        & frame.occupancy_mae_percentage_points.le(original.occupancy_mae_percentage_points*1.1)
        & frame.reproduced_slow_station_windows.ge(original.reproduced_slow_station_windows)
        & ~frame.candidate.eq('original'))
    frame['passes_development']=eligible
    frame.to_csv(SUITE/'training_results.csv',index=False,encoding='utf-8-sig')
    chosen=frame[eligible].sort_values(['score','candidate'])
    selected=str(chosen.iloc[0].candidate) if len(chosen) else None
    result={'selected_before_holdout':selected,'original_score':float(original.score),
        'training_runs':12,'holdout_opened':False,'original_retained':selected is None,
        'scope':'One seed training search, not independent validation or physical parameter identification'}
    save(SUITE/'selection.json',result)
    return result


def main():
    freeze()
    done=[]
    def launch(name):
        r=subprocess.run([sys.executable,'-m','b_twin.baseline_optimization','--one',name],cwd=APP,
            capture_output=True,timeout=600,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        (SUITE/(name+'.log')).write_bytes(r.stdout+r.stderr)
        r.check_returncode()
        return {'candidate':name,'status':'completed'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(launch,n) for n in CANDIDATES]):
            done.append(f.result())
            save(SUITE/'execution.json',done)
            print(json.dumps(done[-1]),flush=True)
    return report()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--one')
    a=p.parse_args()
    print(json.dumps(one(a.one) if a.one else main(),ensure_ascii=False),flush=True)
