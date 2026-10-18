"""Paced SUMO sessions with asynchronous official HTTP observations.

AI-assisted: OpenAI Codex. Keep actual use and human review for submission.
This is a short operational demonstration, not validated current traffic.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import hashlib
import json
import math
import time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

from .paths import APP, INPUTS, REFINED, sumo_exe
from .live import TZ, URL, parse_snapshot
from .assimilation import PROTOCOL, counts_at, core_mapping, expected_lanes, stamp, update_rates
from .demand import fixed_vehicles
from .runner import digest, prepare_run, write_xml
import sumolib


LIVE_PROTOCOL = {
    **PROTOCOL, "version": "paced_official_http_v2_high_resolution", "poll_seconds": 60,
    "sample_seconds": 60, "step_seconds": .5, "clock_scale": 1.,
    "seed": 20261028, "initial_state": "empty_unmeasured",
    "prior": "day15_ordinary_mean_UNVALIDATED_at_current_time",
    "clearance_seconds": 0,
}
OBS_COLUMNS = ["detector_id", "observation_start", "observation_end", "available_at",
               "quality", "count", "availability_source"]


class PollGate:
    """At most one request at a time; missed ticks never create a burst."""
    def __init__(self, interval=60):
        self.interval, self.next_due, self.inflight = interval, 0., False

    def reserve(self, elapsed):
        if self.inflight or elapsed < self.next_due:
            return False
        self.inflight, self.next_due = True, elapsed + self.interval
        return True

    def finish(self):
        self.inflight = False


def step_delay(simulation_seconds, elapsed, step=.5):
    """Never advance a step beyond already elapsed physical time."""
    return max(0., simulation_seconds + step - elapsed)


def snapshot_rows(payload, lanes, received):
    rows = parse_snapshot(payload, lanes, received)
    # Additional transport-boundary checks: duplicate lanes and nonfinite counts
    # are invalid observations, never extra coverage or a genuine zero.
    root = ET.fromstring(payload)
    duplicated = set()
    for period in root.findall('./periods/period'):
        for detector in period.findall('./detectors/detector'):
            labels = [l.findtext('lane_id') for l in detector.findall('./lanes/lane')]
            if len(labels) != len(set(labels)):
                duplicated.add((detector.findtext('detector_id'), period.findtext('period_from')))
    for row in rows:
        bad = (row['detector_id'], row['observation_start'][11:19]) in duplicated
        count = row['observed_count_30s']
        if bad or (count is not None and not math.isfinite(count)):
            row['quality'], row['observed_count_30s'] = 'invalid_or_incomplete', None
    return [{"detector_id": r['detector_id'], "observation_start": r['observation_start'],
        "observation_end": r['observation_end'], "available_at": r['received_at'],
        "quality": r['quality'], "count": r['observed_count_30s'],
        "availability_source": "actual_session_http_receipt"} for r in rows]


def acquire(output, lanes, sequence, injected=False):
    record = {"sequence": sequence, "requested_at": datetime.now(TZ).isoformat(),
        "source": "injected_transport_outage" if injected else "official_http", "url": URL}
    try:
        if injected:
            raise requests.ConnectionError('Designed transport outage')
        response = requests.get(URL, timeout=(5, 15))
        response.raise_for_status()
        received = datetime.now(TZ)
        payload = response.content
        filename = f"raw/snapshot_{sequence:04d}.xml.gz"
        (output / filename).write_bytes(gzip.compress(payload, mtime=0))
        record.update(received_at=received.isoformat(), file=filename,
            sha256_uncompressed=hashlib.sha256(payload).hexdigest())
        rows = snapshot_rows(payload, lanes, received)
        if not rows:
            raise ValueError('No regional observations')
        record.update(success=True, observation_rows=len(rows),
            valid_rows=sum(r['quality']=='valid' for r in rows),
            newest_observation_end=max(r['observation_end'] for r in rows))
    except (requests.RequestException, ET.ParseError, ValueError, TypeError) as exc:
        record.update(success=False, failed_at=datetime.now(TZ).isoformat(), error_type=type(exc).__name__)
        rows = []
    return record, rows


def make_batch(routes, rates, fleet, original_net, now, end, decision, slot):
    begin = now + LIVE_PROTOCOL['step_seconds']
    frame = pd.DataFrame([{"route_id": r.route_id, "direction": r.direction,
        "source_window": decision.isoformat(), "begin": begin, "end": end,
        "phase": "live_operation", "estimated_vph": float(q),
        "evidence": "available_count_update_or_explicit_prior_hold"}
        for r,q in zip(routes.itertuples(), rates)])
    agents, _ = fixed_vehicles(routes, frame, fleet, original_net, LIVE_PROTOCOL['seed']+slot, 'ordinary')
    agents['vehicle_id'] = f"live{slot:03d}_" + agents.vehicle_id
    agents['planned_at'] = decision.isoformat()
    agents['planning_slot'] = slot
    return agents, frame


def save_replay(output, demand, agents):
    realized = ET.Element('routes')
    base = {r.get('id'):r.attrib for r in demand.findall('vType')}
    for node in demand.findall('vType'):
        realized.append(ET.fromstring(ET.tostring(node)))
    added = set()
    for a in agents.itertuples():
        tid = f"live_{a.base_type}_{int(round(a.speed_factor_assumed*1000))}"
        if tid not in added:
            ET.SubElement(realized, 'vType', **dict(base[a.base_type], id=tid,
                speedFactor=str(a.speed_factor_assumed), speedDev='0'))
            added.add(tid)
    for node in demand.findall('route'):
        realized.append(ET.fromstring(ET.tostring(node)))
    for a in agents.sort_values(['depart','vehicle_id']).itertuples():
        ET.SubElement(realized, 'vehicle', id=a.vehicle_id, route=a.route_id,
            type=f"live_{a.base_type}_{int(round(a.speed_factor_assumed*1000))}",
            depart=str(a.depart), departLane=a.depart_lane, departSpeed=a.depart_speed)
    write_xml(realized, output/'realized_demand.rou.xml')
    cfg = ET.parse(output/'run.sumocfg').getroot()
    cfg.find('input/route-files').set('value','realized_demand.rou.xml')
    cfg.find('input/additional-files').set('value','replay_detectors.add.xml')
    detectors = ET.parse(output/'detectors.add.xml').getroot()
    for node in detectors:
        if node.get('file'):
            node.set('file','replay_'+node.get('file'))
    write_xml(detectors, output/'replay_detectors.add.xml')
    for node in cfg.find('output'):
        if node.get('value','').endswith('.xml'):
            node.set('value','replay_'+node.get('value'))
    write_xml(cfg, output/'replay.sumocfg')


def execute_wallclock(run_id, duration=960, drop_after=None):
    if not math.isfinite(duration) or duration < 2 or duration > 86400 or duration % .5:
        raise ValueError('Duration must be 2..86400 seconds, in half-second steps')
    if drop_after is not None and (not math.isfinite(drop_after) or drop_after < 0):
        raise ValueError('Invalid designed dropout time')
    output, meta = prepare_run('ordinary','through',LIVE_PROTOCOL['seed'],run_id,'fixedmean',0)
    (output/'raw').mkdir()
    for name in ['vehicles.csv','demand.rou.xml','demand_segments.csv','demand_constraints.csv','demand_manifest.json']:
        (output/name).rename(output/('template_'+name))
    routes = pd.read_csv(INPUTS/'ordinary_inferred_routes.csv')
    fleet = pd.read_csv(INPUTS/'vehicle_class_priors.csv')
    fleet = fleet[fleet.case.eq('ordinary')]
    mapping, lanes = core_mapping(), expected_lanes()
    net = sumolib.net.readNet(str(output/'network.net.xml'))
    original = sumolib.net.readNet(str(REFINED/'corridor.net.xml'))
    definition = json.loads((output/'network_definition.json').read_text(encoding='utf-8'))
    demand = ET.Element('routes')
    for node in ET.parse(INPUTS/'ordinary_diagnostic.rou.xml').getroot().findall('vType'):
        demand.append(node)
    for row in routes.itertuples():
        edges = row.edges.split()
        edges += definition['route_continuations'].get(edges[-1],[])
        ET.SubElement(demand,'route',id=row.route_id,edges=' '.join(edges))
    write_xml(demand,output/'demand.rou.xml')
    cfg = ET.parse(output/'run.sumocfg').getroot()
    cfg.find('time/end').set('value',str(duration))
    ET.SubElement(ET.SubElement(cfg,'processing'),'time-to-teleport',value='-1')
    write_xml(cfg,output/'run.sumocfg')
    source_names=['wallclock.py','assimilation.py','live.py','demand.py','runner.py','evaluation.py',
        'background.py','boundary.py','paths.py']
    (output/'frozen_sources').mkdir()
    for name in source_names:
        (output/'frozen_sources'/name).write_bytes((APP/'b_twin'/name).read_bytes())
    meta.update(status='prepared',clock_implementation=time.get_clock_info('perf_counter').implementation,clock_resolution_seconds=time.get_clock_info('perf_counter').resolution,wallclock_protocol=LIVE_PROTOCOL,simulation_end_seconds=duration,
        warmup_seconds=0,evaluation_seconds=duration,physical_start=None,physical_origin=None,
        mode='paced_actual_HTTP_and_SUMO_OPERATIONAL_DEMO_NOT_validated_current_traffic',
        demand_mode='live_future_batches_with_unvalidated_daytime_prior',planned_vehicles=0,
        designed_dropout_after_seconds=drop_after,teleport_timeout_seconds=-1,
        source_sha256={n:digest(output/'frozen_sources'/n) for n in source_names},
        prior_input_sha256={p.relative_to(APP.parent).as_posix():digest(p) for p in
            [INPUTS/'ordinary_inferred_routes.csv',INPUTS/'vehicle_class_priors.csv',REFINED/'detector_mapping.csv']},
        demand_sha256=digest(output/'demand.rou.xml'),
        limitations=['Current demand and initial queues are unvalidated; starts from empty.',
            'Day15 ordinary prior is not a measured current or nighttime demand.',
            'Counts are delayed, incomplete and nonunique; coverage threshold is not relaxed.',
            'No real road actuation, measured individual identities, or emergency task in this run.',
            'This short run measures operation and timing, not traffic accuracy, governance benefit or long-term stability.'])
    (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    monitored = pd.read_csv(REFINED/'detector_mapping.csv',dtype={'sumo_edge':str})
    monitored = monitored[monitored.sumo_edge.map(net.hasEdge)]
    observations, batches, segments, collections, decisions = [], [], [], [], []
    previous = routes.estimated_vph.to_numpy(dtype=float)
    connection, future = None, None
    pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='official-feed')
    gate = PollGate(LIVE_PROTOCOL['poll_seconds'])
    sequence = slot = teleports = collisions = 0
    created_types = set()
    next_update = next_sample = 0.
    last_decision = None
    started = None
    try:
        import traci
        traci.start([sumo_exe(),'-c',str(output/'run.sumocfg'),'--log',str(output/'sumo.log'),
            '--error-log',str(output/'warnings.log')],label=run_id)
        connection = traci.getConnection(run_id)
        origin = datetime.now(TZ)
        started = time.perf_counter()
        meta.update(status='running',physical_start=origin.isoformat(),physical_origin=origin.isoformat())
        (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        with (output/'states_60s.jsonl').open('w',encoding='utf-8') as states, \
             (output/'update_decisions.jsonl').open('w',encoding='utf-8') as logs, \
             (output/'collection.jsonl').open('w',encoding='utf-8') as acquisition_log:
            def finish_request():
                nonlocal future
                record, rows = future.result()
                record['processed_at'] = datetime.now(TZ).isoformat()
                record['processed_elapsed_seconds'] = time.perf_counter()-started
                observations.extend(rows)
                collections.append(record)
                acquisition_log.write(json.dumps(record,ensure_ascii=False)+'\n')
                acquisition_log.flush()
                pd.DataFrame(observations,columns=OBS_COLUMNS).to_csv(output/'observation_stream.csv',index=False,encoding='utf-8-sig')
                gate.finish()
                future = None
                print(json.dumps({'collection':sequence-1,'success':record['success'],'elapsed':round(record['processed_elapsed_seconds'],2)},ensure_ascii=False),flush=True)
            while connection.simulation.getTime() < duration:
                now = connection.simulation.getTime()
                elapsed = time.perf_counter()-started
                if future is not None and future.done():
                    finish_request()
                if gate.reserve(elapsed):
                    future = pool.submit(acquire,output,lanes,sequence,drop_after is not None and elapsed>=drop_after)
                    sequence += 1
                if now >= next_update:
                    decision = stamp(origin)+pd.Timedelta(seconds=now)
                    stream = pd.DataFrame(observations,columns=OBS_COLUMNS)
                    counts = counts_at(stream,decision,mapping,LIVE_PROTOCOL)
                    rates, selected, info = update_rates(routes,counts,mapping,previous,LIVE_PROTOCOL)
                    end = min(now+LIVE_PROTOCOL['update_seconds'],duration)
                    agents, frame = make_batch(routes,rates,fleet,original,now,end,decision,slot)
                    route_edges={r.get('id'):r.get('edges').split() for r in demand.findall('route')}
                    for a in agents.itertuples():
                        assert all(net.getEdge(e).allows(a.sumo_vclass) for e in route_edges[a.route_id])
                        tid=f"live_{a.base_type}_{int(round(a.speed_factor_assumed*1000))}"
                        if tid not in created_types:
                            connection.vehicletype.copy(a.base_type,tid)
                            connection.vehicletype.setSpeedFactor(tid,a.speed_factor_assumed)
                            connection.vehicletype.setSpeedDeviation(tid,0.)
                            created_types.add(tid)
                        connection.vehicle.add(a.vehicle_id,a.route_id,typeID=tid,depart=str(a.depart),
                            departLane=a.depart_lane,departSpeed=a.depart_speed)
                    selected.to_csv(output/f'constraints_{slot:03d}.csv',index=False,encoding='utf-8-sig')
                    used=selected[selected.constraint_used]
                    last_decision={'slot':slot,'simulation_seconds':now,'decision_at':decision.isoformat(),
                        'decided_at_wallclock':datetime.now(TZ).isoformat(),'effective_begin_seconds':now+.5,
                        'effective_end_seconds':end,'constraints':info['constraints'],'action':info['evidence'],
                        'latest_used_available_at':used.latest_available_at.max() if len(used) else None,
                        'latest_used_observation_end':used.last_observation_end.max() if len(used) else None,
                        'known_receipt_rows':len(stream),'future_vehicles_added':len(agents),
                        'admitted_boundary_vph':float(rates.sum()),'used_rows_sha256':digest(output/f'constraints_{slot:03d}.csv'),
                        'collection_successes_so_far':sum(c['success'] for c in collections),
                        'collection_failures_so_far':sum(not c['success'] for c in collections)}
                    logs.write(json.dumps(last_decision,ensure_ascii=False)+'\n');logs.flush()
                    decisions.append(last_decision);batches.append(agents);segments.append(frame)
                    previous=rates.copy();next_update=end;slot+=1
                    print(json.dumps({'decision':slot,'constraints':info['constraints'],'future_vehicles':len(agents)},ensure_ascii=False),flush=True)
                while True:
                    wait=step_delay(now,time.perf_counter()-started)
                    if wait<=0:break
                    time.sleep(min(wait,.2))
                connection.simulationStep()
                now=connection.simulation.getTime()
                teleports+=connection.simulation.getStartingTeleportNumber()
                collisions+=connection.simulation.getCollidingVehiclesNumber()
                if now>=next_sample or now>=duration:
                    elapsed=time.perf_counter()-started
                    state={'simulation_seconds':now,'physical_time':(stamp(origin)+pd.Timedelta(seconds=now)).isoformat(),
                        'sampled_at_wallclock':datetime.now(TZ).isoformat(),'wallclock_elapsed_seconds':elapsed,
                        'simulation_lag_seconds':elapsed-now,'mode':meta['mode'],'phase':'live_operation',
                        'running':connection.vehicle.getIDCount(),'pending':len(connection.simulation.getPendingVehicles()),
                        'assimilation':last_decision,'collection_successes':sum(c['success'] for c in collections),
                        'collection_failures':sum(not c['success'] for c in collections),'edges':[]}
                    for r in monitored.itertuples():
                        e=net.getEdge(r.sumo_edge)
                        lon,lat=net.convertXY2LonLat(*e.getShape()[len(e.getShape())//2])
                        state['edges'].append({'detector_id':r.detector_id,'edge_id':r.sumo_edge,
                            'longitude':lon,'latitude':lat,'speed_kmh':3.6*connection.edge.getLastStepMeanSpeed(r.sumo_edge),
                            'vehicles':connection.edge.getLastStepVehicleNumber(r.sumo_edge),
                            'halting_vehicles':connection.edge.getLastStepHaltingNumber(r.sumo_edge),
                            'mapping_requires_review':bool(r.requires_review),'source':'simulated_NOT_observed'})
                    states.write(json.dumps(state,ensure_ascii=False)+'\n');states.flush()
                    next_sample=(int(now//60)+1)*60
            meta['simulation_stopped_at_wallclock']=datetime.now(TZ).isoformat()
            meta['paced_elapsed_seconds']=time.perf_counter()-started
            if future is not None:
                finish_request()
        connection.close();connection=None
        all_agents=pd.concat(batches,ignore_index=True).sort_values(['depart','vehicle_id'])
        all_agents.to_csv(output/'vehicles.csv',index=False,encoding='utf-8-sig')
        pd.concat(segments,ignore_index=True).to_csv(output/'demand_segments.csv',index=False,encoding='utf-8-sig')
        save_replay(output,demand,all_agents)
        trips=ET.parse(output/'tripinfo.xml').getroot().findall('tripinfo')
        ids={t.get('id') for t in trips}
        arrived=sum(float(t.get('arrival'))>=0 for t in trips)
        departed=sum(float(t.get('depart'))>=0 for t in trips)
        absent=set(all_agents.vehicle_id)-ids
        final_state=state
        metrics={'planned':len(all_agents),'arrived':arrived,'departed':departed,
            'running_at_cutoff':final_state['running'],'pending_at_cutoff':final_state['pending'],
            'tripinfo_rows':len(trips),'not_departed_tripinfo':len(trips)-departed,
            'not_in_tripinfo_ids':sorted(absent),'conservation_residual':len(all_agents)-arrived-final_state['running']-final_state['pending']-len(absent),
            'teleports':teleports,'collision_vehicle_events':collisions,
            'traffic_accuracy_assessed':False,'governance_benefit_assessed':False}
        (output/'operational_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        meta.update(status='completed',planned_vehicles=len(all_agents),update_count=len(decisions),
            actual_collection_count=len(collections),vehicle_manifest_sha256=digest(output/'vehicles.csv'),
            observation_stream_sha256=digest(output/'observation_stream.csv'),
            realized_demand_sha256=digest(output/'realized_demand.rou.xml'))
    except BaseException as exc:
        meta.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error_type=type(exc).__name__)
        raise
    finally:
        if connection is not None:connection.close()
        pool.shutdown(wait=True)
        meta['closed_at']=datetime.now(TZ).isoformat()
        (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'run_dir':str(output),'paced_seconds':meta['paced_elapsed_seconds'],'collections':len(collections),
        'decisions':len(decisions),**metrics}
