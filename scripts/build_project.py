#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18.
"""Build one offline application from verified final data and experiment outputs."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
PHASES={'S1':'一般交通','S2':'拥堵治理','S3':'应急协同'}
PROFILES={'primary':'主情景','higher_demand':'较高需求','severe_event':'严重慢行','longer_event':'较长事件'}
POLICIES={'fixed':'固定路线','A':'路径引导 A','B':'协同 B'}
WINDOWS={'training':'训练窗口','same_day_exploratory':'同日短窗','cross_day_holdout':'跨日保留窗'}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def safe(data):return json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def local(value):
    path=(ROOT/value).resolve()
    if not path.is_relative_to(ROOT) or path.name.endswith('.local.json'):raise ValueError('Nonpublic/outside path')
    return path


def load_replay(row):
    folder=local(row['folder']);m=read(folder/'run_manifest.json')
    if digest(folder/'run_manifest.json')!=row['run_manifest_sha256']:raise ValueError('Run manifest hash mismatch')
    for name in ('network.net.xml','fcd.xml','summary.xml','metrics.json','event.json','source_queue_trace.json'):
        if digest(folder/name)!=m['artifacts'][name]:raise ValueError('Artifact hash mismatch: '+name)
    source_wait={float(x['time_s']):x['source_waiting'] for x in read(folder/'source_queue_trace.json')}
    summaries={float(s.attrib['time']):s.attrib for s in ET.parse(folder/'summary.xml').getroot().findall('step')}
    frames=[]
    for _,step in ET.iterparse(folder/'fcd.xml',events=('end',)):
        if step.tag!='timestep':continue
        t=float(step.get('time'));s=summaries.get(t,{})
        frames.append({'t':t,'v':[[v.get('id'),round(float(v.get('x')),2),round(float(v.get('y')),2),round(float(v.get('angle')),1),round(float(v.get('speed')),2)] for v in step.findall('vehicle')],
                       'arrived':int(s.get('arrived',0)),'waiting':int(s.get('waiting',0))+int(source_wait.get(t,0))})
        step.clear()
    event=read(folder/'event.json') if row['scenario_family']!='S1' else None
    task_release=m.get('protocol',{}).get('hospital_task',{}).get('release_s',720) if row['scenario_family']=='S3' else None
    note=f"计划 {row['planned']} 辆，到达 {row['arrived']} 辆；普通车辆总系统时间 {row['ordinary_system_time_s']:,.0f} 车·秒。"
    if row['task_release_to_arrival_s'] is not None:note+=f"任务从发布至到达 {row['task_release_to_arrival_s']:g} 秒。"
    label=PROFILES[row['profile']]+' · '+PHASES[row['scenario_family']]+' · '+POLICIES[row['policy']]
    return {'case':row['case'],'label':label,'phase':row['scenario_family'],'policy_key':row['policy'],
            'description':f"6 个固定 OD，各每 {row['departure_interval_s']} 秒一辆；只在同条件下比较策略。" + ('附加医院外围保障任务。' if task_release is not None else ''),
            'profile':row['profile'],'event':event,'frames':frames,'task_release_s':task_release,'result_note':note,
            'time_basis':f"模拟秒与声明实验时钟对应：{m['experiment_anchor']} + t；该时钟是反事实实验锚点，不代表实测交通回放。"}


def validation_rows(summary):
    # The validation script emits one row per phase/model/scope; retain overall
    # metrics here and link per-OD details in the paper and frozen CSV.
    metrics=summary['metrics']
    if isinstance(metrics,dict):
        rows=[r for phase in ('training','same_day_exploratory','cross_day_holdout') for r in metrics[phase].values()]
    else:rows=metrics
    result=[]
    labels={'per_od_training_median':'按路径中位数','shared_affine_eta':'共享等效模型','physical_sumo':'原始 SUMO'}
    for r in rows:
        od=r.get('od_id',r.get('route_pair_id',r.get('scope','overall')))
        if od not in ('overall','ALL','all',None):continue
        model=r.get('method',r.get('model',r.get('model_name','')))
        result.append({'window':WINDOWS.get(r.get('phase'),r.get('phase')),'model':labels.get(model,model),
                       'n':r.get('n',r.get('sample_count')),'mae':r.get('mae_s',r.get('MAE_s'))})
    if not result:raise ValueError('Validation summary schema needs explicit adaptation')
    return result


def observations(data,out):
    import replay_baidu
    snapshots,excluded=replay_baidu.read_snapshots(ROOT/'data/raw/baidu',ROOT/'data/processed/baidu/probe_assessments.json','shanghai_ruijin',ROOT/'data/processed/baidu/survey_routes.json')
    allowed={Path(c['directory']).name for c in data['collections']}
    selected=[s for s in snapshots if s['batch'] in allowed]
    document={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
        'coordinate_system':'bd09ll','source':'Baidu Web API','mode':'frozen_observation_replay',
        'reference_routes':next((s['reference_routes'] for s in selected if s.get('reference_routes')),[]),
        'snapshots':[{k:v for k,v in s.items() if k!='reference_routes'} for s in selected],
        'excluded':[{'batch':s['batch'],'reason':'不属于最终冻结的四个观测会话'} for s in snapshots if s['batch'] not in allowed]}
    page=replay_baidu.PAGE.replace('__DATA__',safe(document))
    page=page.replace('<body>','<body><p style="margin:12px 24px"><a href="index.html">← 返回项目总览</a> · 仅展示冻结会话原始快照，重复样本在分析表中另行去重</p>',1)
    (out/'observations.html').write_text(page,encoding='utf-8')


def main():
    final=read(ROOT/'outputs/final_experiments/summary.json')
    validation=read(ROOT/'outputs/validation/summary.json')
    data=read(ROOT/'data/processed/final/data_manifest.json')
    if final['status']!='completed' or not final['all_physics_valid']:raise ValueError('Final scenarios not complete and physically valid')
    runs=final['runs']
    core=[r for r in runs if r['profile']=='primary']
    # Also expose every sensitivity run through the same actual trajectory UI.
    replays=[load_replay(r) for r in runs]
    network=ET.parse(local(core[0]['folder'])/'network.net.xml').getroot()
    lanes=[{'id':lane.get('id'),'points':[[round(float(c),2) for c in point.split(',')] for point in lane.get('shape').split()]} for lane in network.iter('lane')]
    collections=[]
    for entry in data['collections']:
        counts=entry.get('counts',entry.get('session_counts',{}))
        successes=entry.get('successes',entry.get('success_counts',counts))
        collections.append({'window':entry['started_at'][5:16].replace('T',' ')+' — '+entry['finished_at'][11:16],
                            'traffic':counts.get('traffic',0),'direction':counts.get('direction',0),
                            'errors':entry.get('error_count',sum(counts.values())-sum(successes.values())),
                            'status':'已停止'})
    audit=data['data_audit']
    total_eligible=sum(x['eligible_count'] for x in audit['phases'].values())
    metrics=validation_rows(validation)
    reductions=[x for x in final['comparisons'] if x['policy']=='B' and x['reference_policy']=='A']
    sentence='；'.join(PROFILES[x['profile']]+'：任务变化 '+str(x['task_time_delta_s'])+' 秒、普通总系统时间变化 '+str(x['ordinary_system_time_delta_s'])+' 车·秒' for x in reductions)
    output={'overview_stats':[['有效路线样本',str(total_eligible)],['可运行候选路径','8'],['完成的仿真组数',str(len(runs))],['仿真物理检查','全部通过']],
        'collections':collections,'area_note':'6 组需求 OD · 307 个有向几何分段 · 310 个允许转向 · 8 条候选路径',
        'completion_note':'数据冻结、跨日误差评价、治理 A／B、同条件对照和敏感性实验已执行。',
        'validation_rows':metrics,'model_note':'默认 ETA 状态采用每条路径训练窗口中位数；共享非负线性等效模型作为研究对照。两者都只用训练窗口估计，微观流量与信号参数仍为假设。',
        'validation_note':'跨日误差用于报告泛化能力，不用于反复调参或改选模型。模型与平台一致不等于证明现场单车轨迹准确。',
        'results':[{'family':PROFILES[r['profile']]+' / '+r['scenario_family'],'policy':POLICIES[r['policy']],
                    'planned':r['planned'],'arrived':r['arrived'],'ordinary_system_time_s':r['ordinary_system_time_s'],
                    'task_release_to_arrival_s':r['task_release_to_arrival_s'],'control_wait_s':r['ordinary_source_control_wait_s']} for r in runs],
        'interpretation':'B 相对 A 的配对变化（负值为用时减少）：'+sentence+'。所有结果来自预先声明的同条件仿真假设，不能仅凭较快的任务时间判断整体更优。',
        'lanes':lanes,'replays':replays,
        'provenance':'数据：百度官方 Web API；模型：SUMO 1.28.0。冻结输入和运行产物有 SHA-256 审计。AI 辅助范围在论文与使用详情中披露，队员复核状态单独记录。'}
    out=ROOT/'outputs/app';out.mkdir(parents=True,exist_ok=True)
    page=(ROOT/'src/app_template.html').read_text(encoding='utf-8').replace('__DATA__',safe(output))
    (out/'index.html').write_text(page,encoding='utf-8')
    observations(data,out)
    for name in ('paper.pdf','paper.docx','ai_usage.pdf'):
        p=ROOT/'outputs/paper'/name
        if p.exists():shutil.copyfile(p,out/name)
    inputs={str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'src/app_template.html',ROOT/'scripts/build_project.py',ROOT/'outputs/final_experiments/summary.json',ROOT/'outputs/validation/summary.json',ROOT/'data/processed/final/data_manifest.json']}
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'inputs_sha256':inputs,
        'artifacts':{p.name:digest(p) for p in out.iterdir() if p.is_file() and p.name!='app_manifest.json'},
        'run_count':len(runs),'replay_count':len(replays),'route_sample_count':total_eligible}
    (out/'app_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'built','page':'outputs/app/index.html','runs':len(runs),'frames':sum(len(r['frames']) for r in replays)},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
