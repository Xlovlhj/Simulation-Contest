"""Audit real-clock session evidence without claiming traffic accuracy."""
# AI-assisted: OpenAI Codex; retain actual usage and human review.
from datetime import datetime
import gzip
import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, INPUTS, REFINED
from .runner import digest
from .assimilation import counts_at, core_mapping, expected_lanes, stamp, update_rates
from .wallclock import LIVE_PROTOCOL, OBS_COLUMNS, snapshot_rows, make_batch
from .live_accounting import finalize_accounting
import sumolib


def audit_session(run_id):
    directory=APP/'results'/run_id
    meta=json.loads((directory/'run.json').read_text(encoding='utf-8'))
    assert meta['status']=='completed' and meta['wallclock_protocol']==LIVE_PROTOCOL
    for name,sha in meta['source_sha256'].items():
        assert digest(directory/'frozen_sources'/name)==sha
        assert digest(APP/'b_twin'/name)==sha
    for name,sha in meta['prior_input_sha256'].items():
        assert digest(APP.parent/name)==sha
    for file,key in [('network.net.xml','network_sha256'),('demand.rou.xml','demand_sha256'),
                     ('vehicles.csv','vehicle_manifest_sha256'),('observation_stream.csv','observation_stream_sha256'),
                     ('realized_demand.rou.xml','realized_demand_sha256')]:
        assert digest(directory/file)==meta[key]
    collections=[json.loads(l) for l in (directory/'collection.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[]
    for c in collections:
        if c['success']:
            payload=gzip.decompress((directory/c['file']).read_bytes())
            import hashlib
            assert hashlib.sha256(payload).hexdigest()==c['sha256_uncompressed']
            received=datetime.fromisoformat(c['received_at'])
            assert stamp(c['requested_at'])<=stamp(received)<=stamp(c['processed_at'])
            rows.extend(snapshot_rows(payload,expected_lanes(),received))
        else:
            assert c['source'] in {'official_http','injected_transport_outage'}
    stored=pd.read_csv(directory/'observation_stream.csv')
    expected=pd.DataFrame(rows,columns=OBS_COLUMNS)
    if len(rows):
        pd.testing.assert_frame_equal(stored,expected,check_dtype=False,check_exact=False)
    else:
        assert stored.empty
    requests=[stamp(c['requested_at']) for c in collections]
    gaps=[(b-a).total_seconds() for a,b in zip(requests,requests[1:])]
    assert not gaps or min(gaps)>=LIVE_PROTOCOL['poll_seconds']-.25
    # Serialized worker completions precede the next actual HTTP call.
    for a,b in zip(collections,collections[1:]):
        assert stamp(a.get('received_at',a.get('failed_at')))<=stamp(b['requested_at'])
    decisions=[json.loads(l) for l in (directory/'update_decisions.jsonl').read_text(encoding='utf-8').splitlines()]
    agents=pd.read_csv(directory/'vehicles.csv',dtype={'origin_edge':str,'original_destination_edge':str})
    segments=pd.read_csv(directory/'demand_segments.csv')
    routes=pd.read_csv(INPUTS/'ordinary_inferred_routes.csv')
    fleet=pd.read_csv(INPUTS/'vehicle_class_priors.csv');fleet=fleet[fleet.case.eq('ordinary')]
    original=sumolib.net.readNet(str(REFINED/'corridor.net.xml'))
    previous=routes.estimated_vph.to_numpy(dtype=float)
    evidence_rows=holds=used_total=0
    decision_rows=[]
    for d in decisions:
        assert stamp(d['decision_at'])<=stamp(d['decided_at_wallclock'])
        assert digest(directory/f"constraints_{d['slot']:03d}.csv")==d['used_rows_sha256']
        known=stored.iloc[:d['known_receipt_rows']].copy()
        counts=counts_at(known,d['decision_at'],core_mapping(),LIVE_PROTOCOL)
        rates,selected,info=update_rates(routes,counts,core_mapping(),previous,LIVE_PROTOCOL)
        saved=pd.read_csv(directory/f"constraints_{d['slot']:03d}.csv",dtype={'sumo_edge':str})
        assert saved.detector_id.tolist()==selected.detector_id.tolist()
        assert saved.constraint_used.tolist()==selected.constraint_used.tolist()
        for col in ['coverage','flow_vph_estimated','age_seconds','static_predicted_flow_vph']:
            np.testing.assert_allclose(pd.to_numeric(saved[col]),pd.to_numeric(selected[col]),equal_nan=True,rtol=1e-7)
        assert d['constraints']==info['constraints'] and d['action']==info['evidence']
        used=selected[selected.constraint_used]
        for r in used.itertuples():
            assert stamp(r.latest_available_at)<=stamp(d['decision_at'])
            assert stamp(r.last_observation_end)<=stamp(d['decision_at'])
            assert r.coverage>=.8 and r.age_seconds<=600 and not r.requires_review
        if not info['constraints']:
            np.testing.assert_array_equal(rates,previous);holds+=1
        else:
            evidence_rows+=1
        used_total+=info['constraints']
        expected_agents,frame=make_batch(routes,rates,fleet,original,d['simulation_seconds'],
            d['effective_end_seconds'],stamp(d['decision_at']),d['slot'])
        batch=agents[agents.planning_slot.eq(d['slot'])]
        assert len(batch)==d['future_vehicles_added']
        assert batch.vehicle_id.tolist()==expected_agents.vehicle_id.tolist()
        np.testing.assert_allclose(batch.depart,expected_agents.depart)
        pd.testing.assert_frame_equal(batch.reset_index(drop=True),expected_agents.reset_index(drop=True),
            check_dtype=False,check_exact=False,rtol=1e-9,atol=1e-9)
        assert batch.depart.ge(d['effective_begin_seconds']).all()
        assert batch.depart.lt(d['effective_end_seconds']).all()
        group=segments[segments.source_window.eq(d['decision_at'])]
        np.testing.assert_allclose(group.estimated_vph,rates)
        previous=rates
        decision_rows.append({'run_id':run_id,**d})
    assert not agents.vehicle_id.duplicated().any()
    replay=ET.parse(directory/'realized_demand.rou.xml').getroot()
    assert {v.get('id') for v in replay.findall('vehicle')}==set(agents.vehicle_id)
    states=[json.loads(l) for l in (directory/'states_60s.jsonl').read_text(encoding='utf-8').splitlines()]
    for s in states:
        np.testing.assert_allclose(s['simulation_lag_seconds'],s['wallclock_elapsed_seconds']-s['simulation_seconds'],atol=1e-7)
        assert s['simulation_lag_seconds']>=-1e-6
        physical=stamp(meta['physical_origin'])+pd.Timedelta(seconds=s['simulation_seconds'])
        assert physical==stamp(s['physical_time'])<=stamp(s['sampled_at_wallclock'])
    assert states[-1]['simulation_seconds']==meta['simulation_end_seconds']
    metrics=finalize_accounting(directory)
    trip=ET.parse(directory/'tripinfo.xml').getroot().findall('tripinfo')
    assert len({t.get('id') for t in trip})==len(trip)==metrics['tripinfo_rows']
    assert not metrics['not_in_tripinfo_ids']
    assert metrics['conservation_residual']==metrics['teleports']==metrics['collision_vehicle_events']==0
    statistics=ET.parse(directory/'statistics.xml').getroot()
    assert int(statistics.find('teleports').get('total'))==0
    assert int(statistics.find('safety').get('collisions'))==0
    ages=[(stamp(c['received_at'])-stamp(c['newest_observation_end'])).total_seconds() for c in collections if c['success']]
    result={'run_id':run_id,'duration_s':meta['simulation_end_seconds'],'paced_elapsed_s':meta['paced_elapsed_seconds'],
        'frames':len(states),'collections':len(collections),'successful_http':sum(c['success'] for c in collections),
        'actual_http_failures':sum(not c['success'] and c['source']=='official_http' for c in collections),
        'injected_failures':sum(c['source']=='injected_transport_outage' for c in collections),
        'unique_success_payloads':len({c['sha256_uncompressed'] for c in collections if c['success']}),
        'source_delay_min_s':min(ages) if ages else None,'source_delay_max_s':max(ages) if ages else None,
        'decisions':len(decisions),'decisions_with_constraints':evidence_rows,'held_decisions':holds,'used_station_constraints':used_total,
        'maximum_sampled_lag_s':max(s['simulation_lag_seconds'] for s in states),
        'no_simulation_ahead_of_wallclock':True,'singleflight_and_poll_interval_verified':True,
        'all_source_receipts_and_raw_payloads_verified':True,'all_decisions_recomputed':True,
        'future_batches_recomputed_and_immutable':True,'frozen_sources_verified':len(meta['source_sha256']),**metrics}
    return result,decision_rows,collections


def report_wallclock(prefix='stage08_main_v2'):
    if not prefix.replace('_','').isalnum():raise ValueError('Invalid prefix')
    output=APP/'results'/(prefix+'_report');output.mkdir(exist_ok=True)
    rows=[];decisions=[];collections=[]
    for suffix in ['live','dropout']:
        result,d,c=audit_session(prefix+'_'+suffix)
        rows.append(result);decisions+=d
        collections+=[{'run_id':result['run_id'],**item} for item in c]
    table=pd.DataFrame(rows)
    table.to_csv(output/'sessions.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(decisions).to_csv(output/'decisions.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(collections).to_csv(output/'collections.csv',index=False,encoding='utf-8-sig')
    verification={'actual_runs':2,'mode':'real_wallclock_operational_demonstration',
        'all_raw_receipts_sources_and_future_decisions_verified':True,
        'all_sessions_zero_teleports_collisions_and_conservation_residual':True,
        'no_simulation_ahead_of_wallclock':True,'singleflight_and_minimum_poll_period_verified':True,
        'decisions_audited':sum(r['decisions'] for r in rows),'station_constraints_used':sum(r['used_station_constraints'] for r in rows),
        'all_planned_vehicles_retained':True,'duration_seconds':[r['duration_s'] for r in rows],
        'current_traffic_accuracy_validated':False,'nighttime_prior_validated':False,'real_peak_baseline_validated':False,
        'governance_or_emergency_benefit_assessed':False,'long_term_stability_validated':False,'actual_ambulance_gate_verified':False}
    (output/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    for suffix in ['live','dropout']:
        directory=APP/'results'/(prefix+'_'+suffix)
        states=[json.loads(l) for l in (directory/'states_60s.jsonl').read_text(encoding='utf-8').splitlines()]
        axes[0].plot([s['simulation_seconds'] for s in states],[s['simulation_lag_seconds'] for s in states],'.-',label=suffix)
    axes[0].set(xlabel='Simulation seconds (paced 1:1)',ylabel='Sampled clock lag (seconds)')
    axes[0].legend();axes[0].grid(alpha=.2)
    for row in collections:
        axes[1].scatter((stamp(row['requested_at'])-stamp(json.loads((APP/'results'/row['run_id']/'run.json').read_text(encoding='utf-8'))['physical_origin'])).total_seconds(),
            1 if row['success'] else 0,color='#237d70' if row['success'] else '#bd584d',marker='o' if row['source']=='official_http' else 'x')
    axes[1].set(xlabel='Elapsed request time (seconds)',ylabel='Acquisition outcome',yticks=[0,1],yticklabels=['Failure / injected','Actual HTTP success'])
    axes[1].grid(alpha=.2);fig.suptitle('Real clock and HTTP | Operational evidence, not traffic accuracy')
    fig.savefig(output/'wallclock_timing.png',dpi=160);plt.close(fig)
    live,drop=rows
    lines=['# 第八轮：按真实时间采集并运行SUMO','',
        '新增独立在线调度：一个真实秒对应一个仿真秒，官方HTTP采集与SUMO分开运行。该轮验证连续运行和时序，不证明当前路况精度。','',
        '## 做了什么','',
        '- 每60秒最多发起一次请求，同一时刻最多一个；超时或重复响应不阻塞仿真，也不增加有效覆盖。',
        '- 每300秒按当时已经取得的数据生成下一批车辆，原车继续行驶；覆盖不足或过期时保持上次需求，不补成零。',
        '- 保留80%覆盖、600秒年龄和映射排除规则；不为触发更新降低门槛。',
        '- 物理时刻＝本次启动时刻＋仿真秒；用单调时钟等待，仿真不能抢跑到未来。','',
        '## 实现核查','',
        '首版使用本机GetTickCount64计时，分辨率15.625毫秒；UTC核对发现数毫秒提前，记录保留，正常首版主动停止。正式v2改用QueryPerformanceCounter（本机分辨率0.1微秒），交通参数没有调整；旧版正常/断流与技术试跑不纳入下表。','',
        '## 实际运行','',
        '| 运行 | 真实配速 秒 | HTTP成功/自然失败/注入失败 | 计划次数/有合格约束 | 计划车辆 | 截止在网/待发 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['run_id']} | {r['paced_elapsed_s']:.2f} | {r['successful_http']}/{r['actual_http_failures']}/{r['injected_failures']} | {r['decisions']}/{r['decisions_with_constraints']} | {r['planned']} | {r['running_at_cutoff']}/{r['pending_at_cutoff']} |")
    lines+=['',f"正常组预先设定960秒，取得{live['unique_success_payloads']}个不同响应；故障组预设运行360秒、60秒后主动阻断传输。注入失败仅用于工程检验，不冒充真实网络故障。两组不是同车辆的治理收益对照。",'',
        f"末半秒可能有已加载但尚未尝试发车的车辆，正常/故障组各{live['loaded_not_yet_attempted_at_cutoff']}/{drop['loaded_not_yet_attempted_at_cutoff']}辆，已另列计入守恒和未完成总量；首版摘要和复核程序单独归档，仿真未重跑。",'',
        f"本轮共复算{verification['decisions_audited']}次需求决策，使用{verification['station_constraints_used']}条合格站约束。正常组{live['held_decisions']}次保持、{live['decisions_with_constraints']}次有观测约束；断流组{drop['held_decisions']}次保持。保持计划仍在发合成车辆，不代表真实需求没有变化。",'',
        f"采样帧最大配速滞后：正常{live['maximum_sampled_lag_s']:.3f}秒、故障{drop['maximum_sampled_lag_s']:.3f}秒；只检查保存帧，不能当全步最坏延迟。正常源延迟范围{live['source_delay_min_s']:.1f}—{live['source_delay_max_s']:.1f}秒。",'',
        '![实际配速与采集](wallclock_timing.png)','',
        '## 结论与限制','',
        '真实时间采集、数据可用时刻筛选、未来车辆加入和连续SUMO运行已经贯通，采集失败时仍能运行。全部计划车辆、未完成行程和原始响应保留；本轮无瞬移、碰撞或守恒差。运行到固定截止后结束，未做额外清空，不能用成功到达者均值代替全体成本。','',
        '启动时从空网开始，使用15日普通时段均值作初始需求；当前夜间需求、初始排队、真实OD与配时仍未知。历史先验不等于当前实测，控制台在动也不证明当前城市交通已被准确还原。短时工程演示不能替代长时稳定性、高峰联合验证或未知输入稳健性。','',
        '## 查看与复现','',
        f'[正常运行回放](../{prefix}_live/dashboard.html)｜[断流回放](../{prefix}_dropout/dashboard.html)','',
        'HTML为这次在线运行的离线记录，打开后不再请求接口；重新在线运行要用命令和新的运行ID。','',
        '```powershell',f'python 应用/run.py wallclock-run --run-id {prefix}_new_live --duration 960',
        f'python 应用/run.py wallclock-run --run-id {prefix}_new_dropout --duration 360 --drop-after 60',
        f'python 应用/run.py report-wallclock --prefix {prefix}','```','',
        '逐次记录见collection.jsonl、observation_stream.csv、constraints_*.csv、update_decisions.jsonl与states_60s.jsonl。realized_demand.rou.xml和replay.sumocfg固定回放实际已生成的车辆；回放不重新发HTTP请求。','']
    (output/'第八轮开发诊断.md').write_text('\n'.join(lines),encoding='utf-8')
    return {'report':str(output/'第八轮开发诊断.md'),**verification,'sessions':rows}
