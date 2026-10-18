"""Audit observed sparse yielding, fixed-horizon rescue and ordinary costs."""
# AI-assisted: OpenAI Codex; preserve actual tool use and human review.
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP
from .runner import digest
from .emergency_report import audit_emergency
from .governance_report import agent_costs,audit_decisions
from .emergency_guard import GUARD,lane_continues,space_check,green_extension_needed
import sumolib


def old_diagnosis(output):
    rows=[]
    for seed in range(20261017,20261022):
        for policy in ['baseline','yield','combined']:
            directory=APP/'results'/f'stage05_main_s{seed}_{policy}'
            trace=pd.read_csv(directory/'emergency_trace_2s.csv')
            for edge,g in trace.groupby('edge'):
                rows.append({'seed':seed,'policy':policy,'edge':edge,
                    'sampled_seconds_approx':len(g)*2,'sampled_stopped_seconds_approx':int(g.speed_mps.lt(.1).sum())*2})
    pd.DataFrame(rows).to_csv(output/'stage05_task_edge_dwell.csv',index=False,encoding='utf-8-sig')
    directory=APP/'results/stage05_main_s20261019_yield'
    commands=pd.read_csv(directory/'emergency_commands.csv')
    requests=commands[commands.command.eq('safe_lane_change_request')]
    counts=requests.groupby('target').size().sort_values(ascending=False)
    counts.to_csv(output/'stage05_repeated_requests_seed19.csv',encoding='utf-8-sig')
    return {'seed19_old_yield_requests':len(requests),'seed19_old_requested_vehicles':requests.target.nunique(),
        'maximum_requests_to_one_vehicle':int(counts.max()),
        'note':'Dwell is approximate from 2-second samples; repeated requests alone do not prove the cause of delay.'}


def audit_guard(directory,meta):
    guard=meta['guard_protocol']
    assert guard==GUARD
    net=sumolib.net.readNet(str(directory/'network.net.xml'))
    logs=pd.read_csv(directory/'emergency_commands.csv')
    decisions=[json.loads(line) for line in (directory/'guard_decisions.jsonl').read_text(encoding='utf-8').splitlines()]
    by_time={r['simulation_seconds']:r for r in decisions}
    requests=logs[logs.command.eq('guarded_lane_change_request')]
    response=pd.read_csv(directory/'yield_responses.csv',dtype={'requested_edge':str,'observed_edge':str})
    assert len(requests)==len(response)
    assert len(requests)==sum(r['action']=='requested_once_on_edge' for r in decisions)
    route_root=ET.parse(directory/'demand.rou.xml').getroot()
    routes={r.get('id'):r.get('edges').split() for r in route_root.findall('route')}
    agents=pd.read_csv(directory/'vehicles.csv').set_index('vehicle_id')
    seen=set();last={}
    for record in requests.itertuples():
        data=json.loads(record.details)
        decision=by_time[record.simulation_seconds]
        assert meta['emergency_policy']!='baseline'
        assert decision['action']=='requested_once_on_edge'
        assert decision['leader']==record.target
        assert data['target_lane_index']==decision['selected_lane']
        assert data['lane_change_mode']==1621
        assert decision['task_speed_mps']<=guard['maximum_task_speed_mps']
        assert decision['leader_speed_mps']<=guard['maximum_leader_speed_mps']
        assert 0<=decision['leader_gap_m']<guard['lookahead_m']
        assert decision['distance_to_junction_m']>=guard['junction_margin_m']
        key=(record.target,data['edge'])
        assert key not in seen
        assert record.simulation_seconds-last.get(record.target,-1e9)>=guard['per_vehicle_cooldown_s']
        seen.add(key);last[record.target]=record.simulation_seconds
        route=routes[agents.loc[record.target,'route_id']]
        at=route.index(data['edge'])
        assert decision['leader_next_edges']==route[at+1:]
        chosen=next(c for c in decision['candidates'] if c['lane_index']==decision['selected_lane'])
        assert all(chosen[k] for k in ['allowed','route_connected','space_ok','sumo_could_change'])
        lane=net.getEdge(data['edge']).getLane(decision['selected_lane'])
        assert lane.allows(decision['leader_class'])
        connected,checked=lane_continues(lane,decision['leader_next_edges'],decision['leader_class'],
            lane.getLength()-decision['leader_position_m'],guard['route_lookahead_m'])
        assert connected and checked==chosen['checked_route_edges']
        spaces=space_check(decision['leader_position_m'],decision['leader_length_m'],
            decision['leader_speed_mps'],chosen['target_lane_vehicles'],guard)
        assert spaces['space_ok']
        for field in ['front_gap_m','rear_gap_m','required_front_gap_m','required_rear_gap_m']:
            assert spaces[field] is chosen[field] or np.isclose(spaces[field],chosen[field])
        observed=response[response.requested_at.eq(record.simulation_seconds)&response.vehicle_id.eq(record.target)]
        assert len(observed)==1
        r=observed.iloc[0]
        assert r.requested_edge==data['edge'] and r.requested_lane==decision['selected_lane']
        assert r.observed_at<=meta['simulation_end_seconds']
        if r.outcome!='observation_window_censored_at_cutoff':
            assert r.observed_at==record.simulation_seconds+guard['response_observation_s']
            if r.outcome=='observed_in_requested_lane':
                assert r.observed_edge==r.requested_edge and r.observed_lane==r.requested_lane
    extensions=logs[logs.command.eq('guarded_existing_green_extension')]
    for record in extensions.itertuples():
        data=json.loads(record.details)
        assert meta['emergency_policy']=='guarded_green'
        assert data['state'] in 'gG'
        assert data['state_before']==data['state_after']
        assert data['state_before'][data['link_index']] in 'gG'
        assert data['extra_seconds']<=meta['emergency_scenario']['signal']['maximum_extension_s_assumed']
        need,eta=green_extension_needed(data['distance_m'],data['task_speed_mps'],data['remaining_before_s'],data['extra_seconds'],guard)
        assert need and np.isclose(eta,data['eta_s'])
    assert not logs.command.isin(['safe_lane_change_request','extend_existing_green']).any()
    if meta['emergency_policy']=='baseline':
        assert len(requests)==len(extensions)==0
    observed=int(response.outcome.eq('observed_in_requested_lane').sum())
    notified=json.loads((directory/'emergency_result.json').read_text(encoding='utf-8'))
    assert notified['yield_requests']==len(requests)
    assert notified['green_extensions']==len(extensions)
    assert notified['observed_requested_lane_responses']==observed
    return {'guard_decisions':len(decisions),'yield_requests':len(requests),'green_extensions':len(extensions),
        'observed_requested_lane_responses':observed,
        'response_left_edge_undetermined':int(response.outcome.eq('left_edge_or_network_UNDETERMINED').sum()),
        'response_censored':int(response.outcome.eq('observation_window_censored_at_cutoff').sum())}


def report_guarded(prefix='stage07_main'):
    suite=APP/'results'/(prefix+'_suite')
    spec=json.loads((suite/'suite.json').read_text(encoding='utf-8'))['specification']
    for name,sha in spec['source_sha256'].items():
        assert digest(suite/'frozen_sources'/name)==sha
    simulation_names=set(spec['source_sha256'])-{'emergency_guard_report.py','emergency_guard_suite.py'}
    for name in simulation_names:
        assert digest(APP/'b_twin'/name)==spec['source_sha256'][name]
    review_changes=[]
    for name in ['emergency_guard_report.py','emergency_guard_suite.py']:
        source=APP/'b_twin'/name
        sha=digest(source)
        archive=suite/'review_tools'/sha/name
        archive.parent.mkdir(parents=True,exist_ok=True)
        if archive.exists():
            assert digest(archive)==sha
        else:
            archive.write_bytes(source.read_bytes())
        if sha!=spec['source_sha256'][name]:
            review_changes.append({'name':name,'frozen_sha256':spec['source_sha256'][name],'current_sha256':sha})
    (suite/'review_tool_revisions.json').write_text(json.dumps({'changes':review_changes,
        'reason':'仅修正CSV道路ID读取、报告解释/来源核验及驱动恢复；仿真源码、参数、种子和完整输入未变。'},
        ensure_ascii=False,indent=2),encoding='utf-8')
    for name,sha in spec['input_sha256'].items():
        assert digest(APP.parent/name)==sha
    output=APP/'results'/(prefix+'_report')
    output.mkdir(exist_ok=True)
    diagnosis=old_diagnosis(output)
    (output/'stage05_diagnosis.json').write_text(json.dumps(diagnosis,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[]
    paired_files=[('network.net.xml','network_sha256'),('demand.rou.xml','demand_sha256'),
        ('vehicles.csv','vehicle_manifest_sha256'),('meter_plan.json','meter_plan_sha256'),
        ('emergency_scenario.json','emergency_scenario_sha256'),('guard_protocol.json','guard_protocol_sha256')]
    for item in spec['runs']:
        directory=APP/'results'/item['run_id']
        meta=json.loads((directory/'run.json').read_text(encoding='utf-8'))
        assert meta['status']=='completed' and meta['seed']==item['seed'] and meta['emergency_policy']==item['policy']
        for name,key in paired_files:
            assert digest(directory/name)==meta[key]
        metrics=json.loads((directory/'metrics.json').read_text(encoding='utf-8'))
        audit=audit_emergency(directory,meta)
        guard=audit_guard(directory,meta)
        control=audit_decisions(directory,meta)
        child=output/item['run_id']
        child.mkdir(exist_ok=True)
        cost=agent_costs(directory,child,meta['simulation_end_seconds'])
        trace=pd.read_csv(directory/'emergency_trace_2s.csv')
        rows.append({**item,**audit,**guard,
            'ordinary_vehicle_hours':cost['core_evaluation_vehicle_hours']+cost['background_evaluation_vehicle_hours'],
            'core_vehicle_hours':cost['core_evaluation_vehicle_hours'],'background_vehicle_hours':cost['background_evaluation_vehicle_hours'],
            'unfinished_all':metrics['trip_accounting']['unfinished_after_clearance'],
            'planned':metrics['trip_accounting']['planned'],'teleports':metrics['teleports'],'collisions':metrics['collisions'],
            'conservation_residual':metrics['accounting']['conservation_residual'],'meter_decisions':control['decisions'],
            'sampled_task_stopped_s_approx':int(trace.speed_mps.lt(.1).sum())*2})
    table=pd.DataFrame(rows)
    pairs=[]
    for seed,group in table.groupby('seed'):
        baseline=group[group.policy.eq('baseline')].iloc[0]
        for r in group.itertuples():
            for name in [n for n,_ in paired_files]+['boundary_supply.json','core_vehicles.csv','background_vehicles.csv']:
                assert (APP/'results'/r.run_id/name).read_bytes()==(APP/'results'/baseline.run_id/name).read_bytes(),name
            if r.policy=='baseline':continue
            pairs.append({'seed':seed,'policy':r.policy,
                'task_fixed_cutoff_change_pct':100*(r.rescue_elapsed_lower_bound_s/baseline.rescue_elapsed_lower_bound_s-1),
                'task_fixed_cutoff_change_s':r.rescue_elapsed_lower_bound_s-baseline.rescue_elapsed_lower_bound_s,
                'ordinary_cost_change_pct':100*(r.ordinary_vehicle_hours/baseline.ordinary_vehicle_hours-1),
                'core_cost_change_pct':100*(r.core_vehicle_hours/baseline.core_vehicle_hours-1),
                'background_cost_change_pct':100*(r.background_vehicle_hours/baseline.background_vehicle_hours-1),
                'unfinished_all_change':r.unfinished_all-baseline.unfinished_all})
    paired=pd.DataFrame(pairs)
    summary=table.groupby('policy',sort=False).agg(runs=('seed','size'),completed=('rescue_completed','sum'),
        within_design_threshold=('rescue_within_design_threshold','sum'),
        fixed_cutoff_mean_s=('rescue_elapsed_lower_bound_s','mean'),fixed_cutoff_max_s=('rescue_elapsed_lower_bound_s','max'),
        ordinary_vehicle_hours=('ordinary_vehicle_hours','mean'),unfinished_all=('unfinished_all','mean'),
        yield_requests=('yield_requests','sum'),observed_lane_responses=('observed_requested_lane_responses','sum'),
        green_extensions=('green_extensions','sum'))
    table.to_csv(output/'all_runs.csv',index=False,encoding='utf-8-sig')
    paired.to_csv(output/'paired_changes.csv',index=False,encoding='utf-8-sig')
    summary.to_csv(output/'summary.csv',encoding='utf-8-sig')
    verified={'actual_runs':len(table),'paired_seeds':int(table.seed.nunique()),
        'fresh_seeds':sorted(table.seed.unique().tolist()),'identical_exogenous_inputs_within_seed':True,
        'all_archived_inputs_and_sources_verified':True,'all_task_event_route_and_safety_modes_verified':True,
        'original_archived_source_count':len(spec['source_sha256']),
        'current_simulation_sources_match_frozen_count':len(simulation_names),
        'review_tool_revisions_archived':review_changes,
        'all_guard_request_space_routes_and_repeat_limits_verified':True,
        'all_green_extensions_keep_same_compatible_state':True,
        'all_requests_have_response_or_censor_records':True,
        'all_meter_decisions_verified':int(table.meter_decisions.sum()),
        'guard_decisions_audited':int(table.guard_decisions.sum()),
        'yield_requests':int(table.yield_requests.sum()),'observed_requested_lane_responses':int(table.observed_requested_lane_responses.sum()),
        'green_extensions':int(table.green_extensions.sum()),
        'all_runs_zero_teleports_collisions_and_conservation_residual':bool(table[['teleports','collisions','conservation_residual']].eq(0).all().all()),
        'all_tasks_completed':bool(table.rescue_completed.all()),'real_peak_baseline_validated':False,
        'task_completions_by_policy':{p:int(summary.loc[p,'completed']) for p in ['baseline','guarded','guarded_green']},
        'within_design_threshold_by_policy':{p:int(summary.loc[p,'within_design_threshold']) for p in ['baseline','guarded','guarded_green']},
        'actual_ambulance_gate_verified':False,'official_live_assimilation':False}
    (output/'verification.json').write_text(json.dumps(verified,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
    policies=['baseline','guarded','guarded_green']
    colors=['#7b8795','#c39043','#237d70']
    for seed,g in table.groupby('seed'):
        g=g.set_index('policy').loc[policies]
        axes[0].plot(range(3),g.rescue_elapsed_lower_bound_s,color='#bac3c9',linewidth=.8)
    for i,policy in enumerate(policies):
        g=table[table.policy.eq(policy)]
        axes[0].scatter([i]*len(g),g.rescue_elapsed_lower_bound_s,color=colors[i],zorder=3)
        bad=g[~g.rescue_completed]
        axes[0].scatter([i]*len(bad),bad.rescue_elapsed_lower_bound_s,marker='x',color='red',s=70)
        if i:
            g=paired[paired.policy.eq(policy)]
            axes[1].scatter([i]*len(g),g.ordinary_cost_change_pct,color=colors[i])
    for ax in axes:
        ax.set_xticks(range(3),['No dedicated aid','Sparse yield','Sparse + green'])
        ax.grid(axis='y',alpha=.25)
    axes[0].set_ylabel('Task elapsed until shared cutoff (seconds)')
    axes[1].set_ylabel('Ordinary exposure paired change (%)')
    axes[1].axhline(0,color='grey',linestyle=':')
    fig.suptitle('Designed emergency | 5 new paired seeds | Fixed route and normal safety')
    fig.savefig(output/'guarded_comparison.png',dpi=165)
    plt.close(fig)
    labels={'baseline':'无专门协同','guarded':'检查空位与路线的稀疏让行','guarded_green':'稀疏让行＋按到达估计延绿'}
    lines=['# 第七轮：减少无效应急请求','',
        '保留第五轮失败结果，修改请求规则后，用五个新种子做15次配对。所有组仍为设计事件、固定公共道路终点、普通通行权限；真实救护门与高峰基准尚未验证。','',
        '## 先诊断，再冻结规则','',
        f"旧种子20261019让行组有{diagnosis['seed19_old_yield_requests']}次请求，涉及{diagnosis['seed19_old_requested_vehicles']}辆车；最多同车{diagnosis['maximum_requests_to_one_vehicle']}次。主要长停在公主道771547540。这里只能确认请求重复和阻滞位置，不能据此断言重复请求造成全部延误。",'',
        '新规则只考虑60米内最近前车，任务与前车速度均不超过5m/s。目标车道必须允许该车型、沿下一段及约120米路线连续可行，前后空位满足3米＋1.5秒速度余量，并通过SUMO当前换道可行检查；距路口25米内不请求。同车同路段只请求一次，同车另有30秒冷却。默认安全模式保留，检查仍不保证下一步一定换道。','',
        '组合组只在当前已绿、按到达估计需要延长且落在最多10秒延长窗口内时动作；不跳红、不改相容状态。估计为距离/max(当前速度,3m/s)，加2秒余量；3m/s是设计下限，低速排队时可能偏乐观，不保证实际到达。4秒后记录目标车是否仍在本路段且处于请求车道；离开路段记无法判断，不把所有请求都当作成功或因果收益。','',
        '规则、参数及源码在查看新种子20261023—20261027结果前冻结；没有按新结果再调参或挑选种子。旧种子试跑只验证实现并保留，不混入正式五组。','',
        '## 五组配对结果','',
        '| 策略 | 完成 | 共同截止经历均值 秒 | 普通交通经历量 均值 辆·小时 | 末期未完成 均值 | 请求/4秒位置确认 |','|---|---:|---:|---:|---:|---:|']
    for policy in policies:
        r=summary.loc[policy]
        lines.append(f"| {labels[policy]} | {r.completed:.0f}/5 | {r.fixed_cutoff_mean_s:.1f} | {r.ordinary_vehicle_hours:.2f} | {r.unfinished_all:.1f} | {r.yield_requests:.0f}/{r.observed_lane_responses:.0f} |")
    lines+=['','08:35调度，09:30共同截止；未完成任务保留3300秒已经历时间，不从均值中删除。普通交通主成本覆盖08:00—09:30，包含全部核心/背景车、入口外等待和未完成。配对百分比是先对每个种子比较，再取均值。','',
        '预设180秒仅为实验阈值，不是当地救护标准；本轮三组各0/5达标。任务到达不等于达到该时间目标。','',
        '| 种子 | 无协同 秒 | 稀疏让行 秒 | 让行＋延绿 秒 | 组合请求/延绿 |',
        '|---|---:|---:|---:|---:|']
    for seed,g in table.groupby('seed'):
        g=g.set_index('policy')
        lines.append(f"| {seed} | {g.loc['baseline','rescue_elapsed_lower_bound_s']:.1f} | {g.loc['guarded','rescue_elapsed_lower_bound_s']:.1f} | {g.loc['guarded_green','rescue_elapsed_lower_bound_s']:.1f} | {int(g.loc['guarded_green','yield_requests'])}/{int(g.loc['guarded_green','green_extensions'])} |")
    lines+=['','三个种子未触发让行或延绿，结果与无协同相同；两个种子触发并改善，未观察到任务变慢。以下平均改善主要来自这两组，不能解释为每次都有相同比例收益。','']
    changes={}
    for policy in policies[1:]:
        g=paired[paired.policy.eq(policy)]
        changes[policy]={'task_change_pct':float(g.task_fixed_cutoff_change_pct.mean()),'ordinary_change_pct':float(g.ordinary_cost_change_pct.mean())}
        lines.append(f"- {labels[policy]}：任务截止经历平均{g.task_fixed_cutoff_change_pct.mean():+.2f}%（范围{g.task_fixed_cutoff_change_pct.min():+.2f}%至{g.task_fixed_cutoff_change_pct.max():+.2f}%），{int(g.task_fixed_cutoff_change_s.lt(0).sum())}/5更快；普通交通成本平均{g.ordinary_cost_change_pct.mean():+.2f}%（{g.ordinary_cost_change_pct.min():+.2f}%至{g.ordinary_cost_change_pct.max():+.2f}%）。")
    lines+=['','![新种子应急与普通交通对比](guarded_comparison.png)','',
        '## 如何解释','']
    for policy in policies[1:]:
        g=paired[paired.policy.eq(policy)]
        supported=(g.task_fixed_cutoff_change_pct.mean()<0 and summary.loc[policy,'completed']>=summary.loc['baseline','completed'])
        lines.append(f"{labels[policy]}"+('在本批设计情景中平均改善；仅有五组，未知输入稳健性仍待检验。' if supported else '在本批结果下不能作为已验证的优选方案；保留未改善和未完成结果。'))
    lines+=['','普通交通总经历量下降不代表每类车辆都受益。稀疏让行的背景车成本配对均值'
        f"{paired[paired.policy.eq('guarded')].background_cost_change_pct.mean():+.2f}%，组合为{paired[paired.policy.eq('guarded_green')].background_cost_change_pct.mean():+.2f}%；"
        '组合在种子20261025使背景车成本增加0.60%、截止未完成增加4辆。组合末期未完成均值199辆，与参照相同；稀疏让行为201辆。','']
    lines+=['',f"本批共{int(table.yield_requests.sum())}次让行请求、{int(table.observed_requested_lane_responses.sum())}次4秒后目标位置确认、{int(table.green_extensions.sum())}次延绿；请求更少本身不等于任务更快。新旧种子不同，不能用第五轮和第七轮均值直接当改进幅度；应使用本轮相同种子的baseline配对。",'',
        '## 核验与范围','',
        f"15次输入、原12份冻结源码归档、当前10份仿真源码、同种子完整车辆/路径/事件/设施、{int(table.meter_decisions.sum())}条入口决策、{int(table.guard_decisions.sum())}条应急检查、逐次空位/路线/重复限制、事件恢复与安全模式均复核。报告和批量驱动另存修订版本，原归档不覆盖；仅报告解析/解释和恢复工具改动，仿真未改参或重跑。所有任务、普通车辆及未完成保留，安全与守恒见verification.json。",'',
        '仍使用同日估计需求、假设背景900/名义供给900、生成配时和未经核实的本地交通。新种子检验随机性，不能替代独立真实数据或需求/供给稳健性。未同化在线观测，研究终点只到公共道路，不含医院内部和患者交接。','',
        '## 回放','',
        f'[默认首种子回放](../{prefix}_s20261023_guarded_green/dashboard.html)：预先指定的种子23，125道路、96帧；该组未触发专门协同，与无协同相同。', '',
        f'[种子24触发回放](../{prefix}_s20261024_guarded_green/dashboard.html)：4次请求、4次位置确认和1次延绿，仅供检查非零指令显示；是已知触发示例，不代表随机样本或总体效果。上表保留全部种子。','',
        '## 复现','', '```powershell',f'python 应用/run.py guarded-emergency-suite --prefix {prefix} --jobs 3',
        f'python 应用/run.py report-guarded-emergency --prefix {prefix}',
        f'python 应用/run.py dashboard --run-id {prefix}_s20261023_guarded_green',
        'python 管理中心/更新页面.py','```','',
        'guard_decisions.jsonl逐次保存候选空位、后续路线与排除理由；yield_responses.csv保存4秒观测或无法判断。原日志、未完成行程、所有CSV、源码及参数归档均保留。','']
    (output/'第七轮开发诊断.md').write_text('\n'.join(lines),encoding='utf-8')
    return {'report':str(output/'第七轮开发诊断.md'),**verified,'paired_mean_changes':changes}
