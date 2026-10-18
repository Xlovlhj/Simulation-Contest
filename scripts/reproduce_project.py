#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18.
"""Recompute all final numerical results in a new workspace-local directory."""
from __future__ import annotations
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def copy_public(source,target):
    for path in source.rglob('*'):
        if not path.is_file() or path.is_symlink() or '__pycache__' in path.parts or path.name.endswith('.local.json'):continue
        destination=target/path.relative_to(source)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,destination)


def same_numbers(a,b,path='root'):
    if isinstance(a,dict):
        if set(a)!=set(b):raise ValueError('Key mismatch: '+path)
        for k in a:same_numbers(a[k],b[k],path+'.'+k)
    elif isinstance(a,list):
        if len(a)!=len(b):raise ValueError('Length mismatch: '+path)
        for i,(x,y) in enumerate(zip(a,b)):same_numbers(x,y,path+f'[{i}]')
    elif isinstance(a,(int,float)) and not isinstance(a,bool):
        if not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9):raise ValueError('Numerical mismatch: '+path)
    elif a!=b:raise ValueError('Value mismatch: '+path)


def main():
    environment=ROOT/'.venv'
    if not (environment/'bin/sumo').exists():raise ValueError('Install requirements into workspace .venv before reproduction; see README.md')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    workspace=ROOT/'.cache'/('reproduction_'+stamp)
    workspace.mkdir(parents=True,exist_ok=False)
    for name in ('scripts','config','src','data/processed/baidu'):
        copy_public(ROOT/name,workspace/name)
    for prefix in ('collection_*','survey_*'):
        for folder in (ROOT/'data/raw/baidu').glob(prefix):
            copy_public(folder,workspace/folder.relative_to(ROOT))
    # Hard links reuse installed bytes inside the same allowed workspace; no
    # package installation, system mutation or network data acquisition occurs.
    shutil.copytree(environment,workspace/'.venv',copy_function=os.link,symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    temporary=workspace/'.cache/runtime';temporary.mkdir(parents=True)
    env=dict(os.environ,TMPDIR=str(temporary),PYTHONDONTWRITEBYTECODE='1')
    steps=[('冻结正式数据','finalize_data.py',['--freeze']),
           ('从零重跑22组仿真','run_final_experiments.py',['--run']),
           ('重新拟合训练层并评价保留窗口','validate_twin.py',['--run'])]
    records=[]
    for label,script,args in steps:
        print(label,flush=True)
        command=[str(workspace/'.venv/bin/python'),'-B',str(workspace/'scripts'/script),*args]
        result=subprocess.run(command,cwd=workspace,env=env,stdin=subprocess.DEVNULL,check=True)
        records.append({'step':label,'exit_code':result.returncode})
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    original=read(ROOT/'outputs/final_experiments/summary.json')
    reproduced=read(workspace/'outputs/final_experiments/summary.json')
    ignored={'folder','run_manifest_sha256'}
    normalize=lambda d:[{k:v for k,v in row.items() if k not in ignored} for row in d['runs']]
    same_numbers(normalize(original),normalize(reproduced),'simulation_runs')
    same_numbers(original['comparisons'],reproduced['comparisons'],'paired_comparisons')
    va=read(ROOT/'outputs/validation/summary.json');vb=read(workspace/'outputs/validation/summary.json')
    same_numbers(va['metrics'],vb['metrics'],'eta_metrics')
    same_numbers(va['models'],vb['models'],'eta_models')
    if va['data_snapshot_id']!=vb['data_snapshot_id']:raise ValueError('Frozen data snapshot differs')
    report={'status':'passed','created_at':datetime.now(timezone.utc).isoformat(),
        'workspace':str(workspace.relative_to(ROOT)),'simulation_runs':len(reproduced['runs']),
        'simulation_numeric_results_identical':True,'paired_comparisons_identical':True,
        'eta_models_and_metrics_identical':True,'data_snapshot_identical':True,
        'absolute_tolerance':1e-9,'relative_tolerance':1e-12,'steps':records,
        'note':'Fresh isolated computation; outputs are not reused from the original run. Runtime timestamps and manifest hashes may differ.'}
    output=ROOT/'outputs/quality';output.mkdir(parents=True,exist_ok=True)
    (output/'reproduction_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
