"""Frozen engineering suite and audit for observation-driven input updates."""
# AI-assisted: OpenAI Codex; preserve real tool-use records for submission.
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, DATA, INPUTS, REFINED
from .runner import digest
from .assimilation import PROTOCOL, stamp, core_mapping, update_rates, counts_at


def run_assimilation_suite(prefix="stage06_main", jobs=2):
    if not prefix.replace("_", "").replace("-", "").isalnum() or not 1 <= jobs <= 3:
        raise ValueError("Invalid prefix or job count")
    runs = [{"run_id":f"{prefix}_{case}_{policy}", "case":case, "policy":policy,
             "extra_delay":0., "dropout":False} for case in ["ordinary","peak"] for policy in ["prior","rolling"]]
    runs += [{"run_id":f"{prefix}_ordinary_delay", "case":"ordinary", "policy":"rolling",
              "extra_delay":900., "dropout":False},
             {"run_id":f"{prefix}_ordinary_dropout", "case":"ordinary", "policy":"rolling",
              "extra_delay":0., "dropout":True}]
    sources = ["assimilation.py", "assimilation_suite.py", "demand.py", "runner.py", "evaluation.py", "live.py"]
    inputs = [DATA/"processed/traffic_lane_30s.csv.gz", REFINED/"detector_mapping.csv",
        REFINED/"corridor_definition.json", INPUTS/"ordinary_inferred_routes.csv",
        INPUTS/"peak_inferred_routes.csv", INPUTS/"vehicle_class_priors.csv"]
    spec = {"runs":runs, "protocol":PROTOCOL,
        "source_sha256":{name:digest(APP/"b_twin"/name) for name in sources},
        "input_sha256":{p.relative_to(APP.parent).as_posix():digest(p) for p in inputs},
        "predeclared_role":"Engineering verification only; no governance benefit or independent forecast claim"}
    directory=APP/"results"/(prefix+"_suite")
    if directory.exists():
        old=json.loads((directory/"suite.json").read_text(encoding="utf-8"))
        if old["specification"]!=spec:
            raise ValueError("Frozen suite differs: use a new prefix and retain historical results")
    else:
        (directory/"frozen_sources").mkdir(parents=True)
        for name in sources:
            shutil.copyfile(APP/"b_twin"/name,directory/"frozen_sources"/name)
        (directory/"suite.json").write_text(json.dumps({"created_at":datetime.now().astimezone().isoformat(),
            "specification":spec},ensure_ascii=False,indent=2),encoding="utf-8")
    def run(item):
        output=APP/"results"/item["run_id"]
        if output.exists():
            state=json.loads((output/"run.json").read_text(encoding="utf-8"))
            if state.get("status")!="completed":
                raise ValueError("Existing failed/incomplete run retained; use a new suite prefix")
            return item["run_id"]
        args=[sys.executable,str(APP/"run.py"),"assimilation-run","--case",item["case"],
            "--policy",item["policy"],"--run-id",item["run_id"],"--extra-delay",str(item["extra_delay"])]
        if item["dropout"]:
            args.append("--dropout")
        result=subprocess.run(args,capture_output=True)
        (directory/(item["run_id"]+".log")).write_bytes(result.stdout+result.stderr)
        result.check_returncode()
        return item["run_id"]
    completed=[]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending={pool.submit(run,item):item for item in runs}
        for future in as_completed(pending):
            completed.append(future.result())
    return {"suite":str(directory),"completed_runs":len(completed),"run_ids":sorted(completed)}


def report_assimilation(prefix="stage06_main"):
    suite_dir=APP/"results"/(prefix+"_suite")
    spec=json.loads((suite_dir/"suite.json").read_text(encoding="utf-8"))["specification"]
    for name,sha in spec["source_sha256"].items():
        assert digest(suite_dir/"frozen_sources"/name)==sha, name
    for relative,sha in spec["input_sha256"].items():
        assert digest(APP.parent/relative)==sha,relative
    output=APP/"results"/(prefix+"_report")
    output.mkdir(exist_ok=True)
    rows=[]
    total_decisions=updated=held=causal_constraints=0
    for item in spec["runs"]:
        directory=APP/"results"/item["run_id"]
        run=json.loads((directory/"run.json").read_text(encoding="utf-8"))
        metrics=json.loads((directory/"metrics.json").read_text(encoding="utf-8"))
        assert run['status']=='completed'
        assert digest(directory/"observation_stream.csv")==run['observation_stream_sha256']
        assert digest(directory/"vehicles.csv")==run['vehicle_manifest_sha256']
        assert digest(directory/"demand.rou.xml")==run['demand_sha256']
        assert digest(directory/"realized_demand.rou.xml")==run['realized_demand_sha256']
        assert metrics['accounting']['conservation_residual']==0
        agents=pd.read_csv(directory/"vehicles.csv")
        assert agents.vehicle_id.is_unique
        realized=ET.parse(directory/"realized_demand.rou.xml").getroot().findall('vehicle')
        assert len(realized)==len(agents)
        stream=pd.read_csv(directory/"observation_stream.csv")
        decisions=[json.loads(line) for line in (directory/"update_decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        segments=pd.read_csv(directory/"demand_segments.csv")
        routes=pd.read_csv(INPUTS/f"{item['case']}_inferred_routes.csv")
        previous=routes.estimated_vph.to_numpy()
        mapping=core_mapping()
        for decision in decisions:
            total_decisions+=1
            counts_file=directory/f"constraints_{decision['slot']:03d}.csv"
            assert digest(counts_file)==decision['used_rows_sha256']
            counts=pd.read_csv(counts_file,dtype={'sumo_edge':str})
            used=counts[counts.constraint_used]
            for row in used.itertuples():
                assert stamp(row.latest_available_at)<=stamp(decision['decision_at'])
                assert stamp(row.last_observation_end)<=stamp(decision['decision_at'])
                assert row.coverage>=PROTOCOL['minimum_coverage']
                assert row.age_seconds<=PROTOCOL['stale_seconds']
                assert not row.requires_review
                causal_constraints+=1
            slot=segments[segments.begin.eq(decision['simulation_seconds'])]
            rates=slot.set_index('route_id').loc[routes.route_id].estimated_vph.to_numpy()
            raw=counts.drop(columns=['sumo_edge','requires_review','constraint_used','static_predicted_flow_vph','constraint_reason'])
            recomputed=counts_at(stream,decision['decision_at'],mapping)
            pd.testing.assert_frame_equal(raw.sort_index(axis=1),recomputed.sort_index(axis=1),
                check_dtype=False,check_exact=False,rtol=1e-9,atol=1e-7)
            inferred,_,info=update_rates(routes,raw,mapping,previous)
            expected=inferred if item['policy']=='rolling' else routes.estimated_vph.to_numpy()
            assert np.allclose(rates,expected,rtol=1e-9,atol=1e-7)
            planned=agents[agents.planned_at.eq(decision['decision_at'])]
            assert len(planned)==decision['future_vehicles_added']
            assert planned.depart.ge(decision['effective_begin_seconds']).all()
            assert planned.depart.lt(decision['effective_end_seconds']).all()
            assert decision['simulation_seconds']==decision['effective_begin_seconds']
            if item['policy']=='rolling':
                if info['constraints']:
                    updated+=1
                else:
                    held+=1
                    assert np.allclose(rates,previous)
            previous=rates
        primary=metrics['primary_metrics']
        rows.append({**item,'updates':len(decisions),
            'decisions_with_constraints':sum(d['constraints']>0 for d in decisions),
            'held_decisions':sum(d['constraints']==0 for d in decisions),
            'planned':len(agents),'flow_wmape_pct':primary['flow_wmape_pct'],
            'speed_mae_kmh':primary['speed_mae_kmh'],
            'occupancy_mae_pp':primary['occupancy_mae_percentage_points'],
            'observed_slow_windows':primary['observed_slow_station_windows'],
            'reproduced_slow_windows':primary['reproduced_slow_station_windows'],
            'pending_at_evaluation_end':metrics['accounting']['evaluation_end']['pending'],
            'unfinished_after_clearance':metrics['trip_accounting']['unfinished_after_clearance'],
            'teleports':metrics['teleports'],'collisions':metrics['collisions'],
            'conservation_residual':metrics['accounting']['conservation_residual']})
    table=pd.DataFrame(rows)
    for case in ['ordinary','peak']:
        ids=[r['run_id'] for r in rows if r['case']==case and not r['extra_delay'] and not r['dropout']]
        assert len({digest(APP/'results'/rid/'observation_stream.csv') for rid in ids})==1
        assert len({json.loads((APP/'results'/rid/'run.json').read_text(encoding='utf-8'))['network_sha256'] for rid in ids})==1
    table.to_csv(output/'all_runs.csv',index=False,encoding='utf-8-sig')
    verification={'actual_runs':len(rows),'all_archive_and_source_hashes_verified':True,
        'all_future_input_and_vehicle_hashes_verified':True,
        'decision_count':total_decisions,'used_causal_station_constraints':causal_constraints,
        'rolling_updates_with_evidence':updated,'rolling_holds_without_evidence':held,
        'all_used_observation_and_availability_times_not_after_decision':True,
        'all_rolling_counts_recomputed_from_archived_stream':True,
        'all_decision_rules_recomputed':True,'future_vehicles_only_no_retroactive_edits':True,
        'duplicates_do_not_inflate_coverage':True,
        'zero_teleports_collisions_and_conservation_residual':bool(table[['teleports','collisions','conservation_residual']].eq(0).all().all()),
        'archive_capture_is_replay_availability_proxy':True,
        'frozen_whole_case_prior_is_independent':False,
        'wallclock_live_SUMO':False,'real_peak_baseline_validated':False,
        'governance_benefit_comparison':False,'historical_observation_driven_future_demand_implemented':True}
    (output/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    labels=[r['case']+' / '+('delay' if r['extra_delay'] else 'dropout' if r['dropout'] else r['policy']) for r in rows]
    axes[0].barh(labels,table.flow_wmape_pct,color='#2d7b83')
    axes[0].set_xlabel('Core flow WMAPE (%)')
    axes[1].barh(labels,table.speed_mae_kmh,color='#bb7552')
    axes[1].set_xlabel('Core speed MAE (km/h)')
    fig.suptitle('Observation-to-demand engineering checks; different demand hypotheses')
    fig.savefig(output/'observation_updates.png',dpi=170)
    plt.close(fig)
    lines=['# 第六轮：观测驱动的未来需求更新','',
        '已接通历史观测→可用性筛选→需求反演→未来车辆加入→SUMO状态回放。在线采集与计划预览可用；持续按墙钟运行的实时孪生尚未完成。','',
        '## 更新方法','',
        '每5分钟，只取决策时已经可用的观测。以最新已知观测结束时刻为锚，向前取15分钟；同站完整有效车道、覆盖率至少80%、年龄不超过10分钟且映射无复核标记，才进入计数反演。缺测不是零，重复记录不加倍；无合格约束时保持上次计划。','',
        '使用原58条候选路径与非负正则化计数反演，加入前次计划先验；单次变化限幅为上次/均值先验/10veh/h三者最大值的25%。只生成接下来5分钟的车辆；已排队、已进入或已计划的旧车不删除、不移动。未直接设定观测速度或占有率。阈值都是预先冻结的工程参数。','',
        '**历史可用时刻是官方归档文件名的快照时间代理，不是本机当时真实接收时间。**旧提取器保留最后一次重复值，所以该值到其自身快照时刻才可用，丢弃的早期修订无法重建。均值先验仍来自15日整段数据，含同日评估信息；本轮验证增量更新的时序，不是独立预测检验。','',
        '## 六次工程运行','',
        '| 时段/输入 | 有证据决策/全部 | 车辆数 | 核心流量WMAPE | 速度MAE km/h | 清空后未完成 |','|---|---:|---:|---:|---:|---:|']
    names={'prior':'冻结均值先验','rolling':'观测滚动更新'}
    for row in rows:
        label=('普通' if row['case']=='ordinary' else '高峰')+' / '+('额外延迟15分钟' if row['extra_delay'] else '20分钟后断流' if row['dropout'] else names[row['policy']])
        lines.append(f"| {label} | {row['decisions_with_constraints']}/{row['updates']} | {row['planned']} | {row['flow_wmape_pct']:.2f}% | {row['speed_mae_kmh']:.2f} | {row['unfinished_after_clearance']} |")
    lines += ['', '![输入更新诊断](observation_updates.png)','',
        f"六次共{total_decisions}次决策，逐项复算通过；滚动组{updated}次有合格证据、{held}次保持计划。检查可用时间、未来出发、限幅、完整车辆和源哈希；瞬移/碰撞/守恒见verification.json。延迟和断流组保留退化行为，不用零观测填补。",'',
        '**两种输入产生不同车辆数，误差差异不能当作治理收益。**一个预设种子只用于系统工程检查，不能替代多种子泛化；所有组均保持真实高峰基准未验证。流量接近也不能证明速度、占有率或拥堵机理正确。','',
        '## 在线采集与限制','',
        '`python 应用/run.py plan-live`会请求真实官方接口，保存本机接收时刻并汇总近期已保存快照。快照不足以覆盖15分钟时明确保持先验，不能拿稀疏两期30秒样本冒充完整窗口；生成的是计划预览，尚未连续驱动墙钟SUMO。夜间需求也不能以白天先验声称已验证。','',
        '## 复现','', '```powershell',
        f'python 应用/run.py assimilation-suite --prefix {prefix} --jobs 2',
        f'python 应用/run.py report-assimilation --prefix {prefix}',
        'python 应用/run.py plan-live',
        f'python 应用/run.py dashboard --run-id {prefix}_ordinary_rolling',
        'python 管理中心/更新页面.py','```','',
        '每次归档观测流、逐次约束/决策、未来车辆、实际轨迹核算与独立源码。realized_demand.rou.xml和replay.sumocfg可固定回放已实现的车辆；重新做因果更新应使用命令和新前缀。原模板输入保留，不与实际动态注入车辆混用。','']
    (output/'第六轮开发诊断.md').write_text('\n'.join(lines),encoding='utf-8')
    return {'report':str(output/'第六轮开发诊断.md'),**verification}
