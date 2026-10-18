"""复现入口。AI辅助：Codex/OpenAI；具体版本及发布日期未核实，见AI工具使用详情。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parent
if hasattr(sys.stdout,'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify():
    manifest_path=ROOT/'PACKAGE_MANIFEST.json'
    if not manifest_path.exists():
        raise RuntimeError('请在解压后的共享包或支撑包中运行verify；开发目录没有包内清单。')
    manifest=read(manifest_path)
    for item in manifest['files']:
        path=ROOT/item['path']
        if not path.is_file() or digest(path)!=item['sha256']:
            raise RuntimeError('文件缺失或变化：'+item['path'])
    print(f"包内{len(manifest['files'])}个文件校验通过。",flush=True)


def prepare(official_data=None):
    source_manifest=read(ROOT/'records/source_manifest.json')
    official=Path(official_data).resolve() if official_data else None
    missing=[]
    for item in source_manifest['files']:
        target=ROOT/item['project_path']
        if not target.exists() and official:
            matches=[p for p in official.rglob(target.name) if p.is_file() and digest(p)==item['sha256']]
            if not matches:
                missing.append(item['project_path']);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(matches[0],target)
        if not target.exists():
            missing.append(item['project_path']);continue
        if digest(target)!=item['sha256']:
            raise RuntimeError('官方输入哈希不符：'+item['project_path'])
    if missing:
        raise RuntimeError('缺少官方输入：\n'+'\n'.join(missing)+'\n精简包请加 --official-data 指向完整官方题包；共享包已含这些文件。')
    archive=ROOT/'payload/trajectories.tar.xz'
    if archive.exists():
        trajectory_manifest=read(ROOT/'payload/trajectories.json')
        expected={r['path']:r['sha256'] for r in trajectory_manifest['files']}
        with tarfile.open(archive,'r:xz') as tar:
            for member in tar.getmembers():
                name=PurePosixPath(member.name)
                if not member.isfile() or member.name not in expected or name.is_absolute() or '..' in name.parts:
                    raise RuntimeError('轨迹压缩文件包含非预期路径。')
                target=ROOT/Path(*name.parts)
                if target.exists():
                    if digest(target)!=expected[member.name]:
                        raise RuntimeError('已有轨迹变化，拒绝覆盖：'+member.name)
                    continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source,target.open('wb') as destination:
                    shutil.copyfileobj(source,destination)
                if digest(target)!=expected[member.name]:
                    raise RuntimeError('轨迹恢复哈希不符：'+member.name)
        for name,value in expected.items():
            if digest(ROOT/name)!=value:
                raise RuntimeError('轨迹恢复不完整：'+name)
        print(f"已恢复{len(expected)}条轨迹；模式：{trajectory_manifest['mode']}。",flush=True)
    print('9份官方输入核对通过。',flush=True)


def check_results(work):
    expected=read(ROOT/'EXPECTED_RESULTS.json')
    actual={}
    for item in expected['metrics']:
        obj=read(work/item['source'])
        for key in item['keys']:
            obj=obj[key]
        value=float(obj)
        allowed=max(item.get('absolute_tolerance',1e-8),abs(item['value'])*item.get('relative_tolerance',1e-8))
        if not math.isfinite(value) or abs(value-item['value'])>allowed:
            raise RuntimeError(f"结果不符：{item['name']}，实际{value}，基准{item['value']}")
        actual[item['name']]=value
    return actual


def isolated_run(command):
    target_parent=ROOT/'reproduced'
    target_parent.mkdir(exist_ok=True)
    target=Path(tempfile.mkdtemp(prefix=command+'_',dir=target_parent))
    shutil.copytree(ROOT,target,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('reproduced','__pycache__','交付材料','.git','*.pyc'))
    if command=='all':
        cmd=[sys.executable,'-X','utf8','-u','run.py','all']
    elif command=='refit':
        cmd=[sys.executable,'-X','utf8','-u','run.py','refit']
    elif command=='long':
        cmd=[sys.executable,'-X','utf8','-u','-c',"import sys;sys.path.insert(0,'src');from bev.durability import evaluate_durability;from bev.stage4 import plot_durability;r=evaluate_durability(recompute=True);plot_durability(r);print('Long journeys recomputed:',len(r['journeys']))"]
    elif command=='improve':
        previous=target/'records/experiments/remediation_v1'
        shutil.move(str(previous),str(previous.with_name('remediation_v1_previous')))
        cmd=[sys.executable,'-X','utf8','-u','-c',"import sys,runpy;sys.path.insert(0,'src');sys.argv=['checks/remediation_checks.py'];runpy.run_path('checks/remediation_checks.py',run_name='__main__')"]
        checks=subprocess.run(cmd,cwd=target,check=False)
        if checks.returncode:raise RuntimeError('改进实验解析检查未通过')
        cmd=[sys.executable,'-X','utf8','-u','run.py','improve']
    else:
        raise ValueError(command)
    started=datetime.now().isoformat(timespec='seconds')
    result=subprocess.run(cmd,cwd=target,check=False)
    if result.returncode:
        raise RuntimeError(f'复现失败，退出码{result.returncode}。输出保留在{target}')
    metrics=check_results(target) if command=='all' else {}
    if command=='improve':
        expected=read(ROOT/'records/remediation_summary.json')
        actual=read(target/'records/remediation_summary.json')
        keys=[('sensor_exact_model','passed'),('two_hour_parking','passed'),('step_checks','passed'),('sensor_mismatch_stress','passed')]
        for group,key in keys:
            if actual[group][key]!=expected[group][key]:raise RuntimeError('改进实验计数变化：'+group)
            metrics[group+'_'+key]=actual[group][key]
        for key in ['battery_end_difference_c','raw_saving_pct']:
            a=actual['hot_six_cycle_after_two_hour_parking'][key];e=expected['hot_six_cycle_after_two_hour_parking'][key]
            if abs(a-e)>1e-6:raise RuntimeError('改进实验结果变化：'+key)
            metrics[key]=a
    report={'command':command,'started_local':started,'finished_local':datetime.now().isoformat(timespec='seconds'),
            'exit_code':result.returncode,'work_directory':target.relative_to(ROOT).as_posix(),
            'metrics_checked':metrics,'full_refit_repeated':command=='refit','long_journeys_recomputed':command=='long',
            'nonlinear_300_refits_recomputed':False,'corrective_stage8_recomputed':command=='improve',
            'note':'all reuses matching frozen identification, selected control, long-journey and nonlinear-refit results; improve recomputes the new stage8 training and thermal experiments.'}
    (ROOT/'REPRODUCTION_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description='C题：文件核验、准备官方输入、隔离复现与论文重建')
    parser.add_argument('command',nargs='?',default='verify',choices=['verify','prepare','all','refit','long','improve','paper'])
    parser.add_argument('--official-data',help='完整官方题包目录；共享包无需填写')
    args=parser.parse_args()
    if args.command=='verify':
        verify();return
    prepare(args.official_data)
    if args.command in ['all','refit','long','improve']:
        isolated_run(args.command)
    elif args.command=='paper':
        sys.path.insert(0,str(ROOT/'src'))
        from bev.delivery import build_documents
        print(json.dumps(build_documents(),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
