"""从两个交接ZIP的新解压目录实际运行；AI辅助见records/ai_usage.md。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bev.io import read_json,sha256,write_json


def run_one(label,item,batch,official):
    package=ROOT/item['path']
    assert sha256(package)==item['sha256']
    destination=batch/label
    with zipfile.ZipFile(package) as archive:
        archive.extractall(destination)
    work=destination/'C题复现工程'
    commands=[['verify'],['prepare'],['all']]
    if label=='support':
        commands[1].extend(['--official-data',str(official)])
    with (destination/'runtime.log').open('w',encoding='utf-8') as output:
        for args in commands:
            completed=subprocess.run([sys.executable,'-X','utf8','-u','reproduce.py',*args],cwd=work,
                                     stdout=output,stderr=subprocess.STDOUT,check=False)
            assert completed.returncode==0,(label,args[0],destination.relative_to(ROOT).as_posix())
    report=read_json(work/'REPRODUCTION_REPORT.json')
    checks=read_json(work/report['work_directory']/'results/tables/verification.json')
    assert report['exit_code']==0 and len(report['metrics_checked'])==17
    assert checks['passed'] and checks['tests_run']==34
    manifest=read_json(work/'PACKAGE_MANIFEST.json')
    return {'package_sha256_at_runtime':item['sha256'],'commands':['verify','prepare','all'],
            'exit_code':0,'scientific_tests_passed':34,'numeric_metrics_compared':17,
            'report':(work/'REPRODUCTION_REPORT.json').relative_to(ROOT).as_posix(),
            'scientific_reproduction_files':{r['path']:r['sha256'] for r in manifest['files']
                if r['path'].startswith(('src/','checks/','configs/','data/','payload/','records/experiments/'))
                or r['path'] in ['reproduce.py','run.py','EXPECTED_RESULTS.json','records/requirements-lock.txt']},
            'full_nonlinear_300_refits_recomputed':False}


def main():
    packages=read_json(ROOT/'records/delivery_packages.json')
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    batch=ROOT/'records/package_checks'/('runtime_'+stamp)
    batch.mkdir(parents=True,exist_ok=False)
    official=next(p for p in ROOT.parent.iterdir() if p.is_dir() and p.name.endswith('赛题'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={label:pool.submit(run_one,label,item,batch,official) for label,item in packages.items()}
        results={label:future.result() for label,future in futures.items()}
    result={'date_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
            'passed':True,'environment':'Same installed Python 3.12 environment, two fresh ZIP extractions; not a fresh OS or virtual environment.',
            'results':results,'scope':'Default frozen-evidence reproduction. Team manual review and independent core work have not started.'}
    write_json(ROOT/'records/delivery_runtime_verification.json',result)
    print(json.dumps({k:v if k!='results' else {a:{b:c for b,c in d.items() if b!='scientific_reproduction_files'} for a,d in v.items()} for k,v in result.items()},ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
