"""阶段八隔离改进实验；旧模型、参数、协议与证据不覆盖。AI辅助：Codex。"""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import time

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid, trapezoid
from scipy.optimize import least_squares
from scipy.signal import place_poles

from .calibration import CalibrationProblem, forward, prepare_trip, protocol as calibration_protocol
from .control import FeedbackController, Rule, control_protocol, scenario_parameters
from .cycles import load_cltc
from .durability import DriveAndRecoveryController, compare_checkpoint, repeat_cycle, summarize_trace
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import InitialState, InfeasibleSimulation, load_curves, load_parameters, simulate
from .paper import verify_evidence

EXPERIMENT=PROJECT_ROOT/'records/experiments/remediation_v1'


def protocol():
    return read_json(PROJECT_ROOT/'configs/remediation_protocol.json')


def identity():
    files=['configs/remediation_protocol.json','src/bev/remediation.py','checks/remediation_checks.py',
           'src/bev/model.py','src/bev/calibration.py','src/bev/control.py','src/bev/durability.py',
           'configs/calibration_protocol.json','configs/control_protocol.json','configs/durability_protocol.json',
           'configs/reference.json','configs/calibrated.json','configs/optimized_rule.json',
           'data/processed/component_curves.json','records/source_manifest.json']
    return {name:sha256(PROJECT_ROOT/name) for name in files}


def begin():
    verify_sources();verify_evidence()
    fingerprint=identity()
    path=EXPERIMENT/'protocol_freeze.json'
    if path.exists():
        if read_json(path)['fingerprint']!=fingerprint:
            raise ValueError('阶段八代码或协议变化，须另存新实验版本。')
    else:
        write_json(path,{'date_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                         'fingerprint':fingerprint,'prior_failures_already_observed':True,
                         'original_scientific_evidence':verify_evidence()})


def scaled_curves(scale):
    if not np.isfinite(scale) or scale<=0:
        raise ValueError('OCV缩放须为有限正数')
    curves=load_curves()
    anchor=float(np.interp(protocol()['ocv_anchor_soc'],curves['ocv_soc'],curves['ocv_cell_v']))
    curves['ocv_cell_v']=(anchor+scale*(np.asarray(curves['ocv_cell_v'])-anchor)).tolist()
    return curves


def corrected_forward(trip,parameters,step_s):
    trace,out=simulate(trip.cycle,parameters,trip.initial,max_step_s=step_s,
                       curves=scaled_curves(parameters.get('ocv_scale',1.)),scenario=f'stage8_trip_{trip.trip_id}')
    t=trip.observation.time_s.to_numpy()
    pred={'soc':np.interp(t,np.r_[trip.cycle.time_s.iloc[0],trace.time_s],np.r_[trip.initial.soc,trace.soc]),
          'voltage_v':np.interp(t,(trace.time_start_s+trace.time_s)/2,trace.voltage_v)}
    return trace,out,pred


class CorrectedProblem:
    def __init__(self):
        self.cfg=calibration_protocol()
        self.trips=[prepare_trip(tid,self.cfg) for tid in protocol()['train_trip_ids']]
        self.names=self.cfg['parameters']+['ocv_scale']
        self.base=load_parameters()
        self.size=None
        self.calls=0

    def parameters(self,vector):
        return {**self.base,**dict(zip(self.names,map(float,vector))),'parameter_status':'stage8_training_corrected_candidate'}

    def residual(self,vector):
        self.calls+=1
        chunks=[]
        for trip in self.trips:
            try:
                trace,_,pred=corrected_forward(trip,self.parameters(vector),self.cfg['fit_max_step_s'])
            except InfeasibleSimulation:
                if self.size is None:raise
                return np.full(self.size,1e4)
            obs=trip.observation;t=obs.time_s.to_numpy()
            w=np.r_[np.diff(t)/2,0.]+np.r_[0.,np.diff(t)/2];w=np.sqrt(w/w.sum())
            edges=np.r_[np.arange(t[0],t[-1],self.cfg['power_window_s']),t[-1]]
            measured=cumulative_trapezoid(obs.battery_power_w,t,initial=0)
            predicted=np.r_[0.,np.cumsum(trace.battery_w*trace.dt_s)]
            mw=np.diff(np.interp(edges,t,measured))/np.diff(edges)
            pw=np.diff(np.interp(edges,np.r_[t[0],trace.time_s],predicted))/np.diff(edges)
            chunks.extend([(pred['soc']-obs.soc.to_numpy())/self.cfg['soc_scale_fraction']*w,
                           (pred['voltage_v']-obs.voltage_v.to_numpy())/self.cfg['voltage_scale_v']*w,
                           (pw-mw)/self.cfg['power_window_scale_w']/np.sqrt(len(mw)),
                           np.array([(predicted[-1]-measured[-1])/(self.cfg['net_energy_relative_scale']*abs(measured[-1]))])])
        residual=np.concatenate(chunks);self.size=len(residual)
        return residual


def calibration_experiment():
    begin()
    result_path=EXPERIMENT/'calibration_results.json'
    if result_path.exists():return read_json(result_path)
    cfg=protocol();problem=CorrectedProblem()
    bounds=(np.r_[problem.cfg['lower_bounds'],cfg['ocv_scale_bounds'][0]],
            np.r_[problem.cfg['upper_bounds'],cfg['ocv_scale_bounds'][1]])
    old=read_json(PROJECT_ROOT/'configs/calibrated.json')
    estimate=np.array([old[name] for name in problem.cfg['parameters']])
    runs=[]
    for scale in cfg['ocv_scale_starts']:
        started=time.perf_counter()
        fit=least_squares(problem.residual,np.r_[estimate,scale],bounds=bounds,x_scale='jac',
                           diff_step=cfg['least_squares_diff_step'],max_nfev=cfg['max_nfev'],
                           ftol=2e-5,xtol=2e-5,gtol=2e-5)
        row={'initial_ocv_scale':scale,'estimate':fit.x.tolist(),'cost':float(fit.cost),'success':bool(fit.success),
             'message':str(fit.message),'nfev':fit.nfev,'active_mask':fit.active_mask.tolist(),'runtime_s':time.perf_counter()-started}
        runs.append(row);write_json(EXPERIMENT/'fit_progress.json',{'runs':runs})
        print('Stage8 training candidate:',row,flush=True)
    best=min(runs,key=lambda row:row['cost'])
    parameters=problem.parameters(best['estimate'])
    # 先冻结训练选解，再读取评价路程。已查看的588/600不重新包装成盲测。
    write_json(EXPERIMENT/'candidate_parameters.json',parameters)
    write_json(EXPERIMENT/'candidate_freeze.json',{'selected_by':'training_objective_only','runs':runs,
               'parameters_sha256':sha256(EXPERIMENT/'candidate_parameters.json'),
               'date_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')})
    rows=[]
    for tid in cfg['train_trip_ids']+cfg['evaluation_trip_ids']:
        trip=prepare_trip(tid)
        for label,p in [('reference',load_parameters()),('frozen_v2',old),('ocv_scaled_candidate',parameters)]:
            fn=corrected_forward if label=='ocv_scaled_candidate' else forward
            trace,out,pred=fn(trip,p,.5)
            measured=trapezoid(trip.observation.battery_power_w,trip.observation.time_s)/3.6e6
            rows.append({'trip_id':tid,'role':'train' if tid in cfg['train_trip_ids'] else 'retrospective_diagnostic',
                         'variant':label,'energy_ape_pct':float(100*abs(out['net_energy_kwh']-measured)/abs(measured)),
                         'soc_rmse_pp':float(100*np.sqrt(np.mean((pred['soc']-trip.observation.soc)**2))),
                         'voltage_rmse_v':float(np.sqrt(np.mean((pred['voltage_v']-trip.observation.voltage_v)**2))),
                         'dc_balance_residual_kwh':out['dc_balance_residual_kwh']})
    # 不用评价路程选Cr范围；每个网格值都重新训练其他五个参数。
    profile=[];start=np.array(best['estimate'])
    for cr in cfg['cr_profile_grid']:
        free=[i for i in range(len(start)) if i!=1]
        def expand(x):
            vector=start.copy();vector[1]=cr;vector[free]=x;return vector
        fit=least_squares(lambda v:problem.residual(expand(v)),start[free],bounds=(bounds[0][free],bounds[1][free]),
                          x_scale='jac',diff_step=cfg['least_squares_diff_step'],max_nfev=cfg['max_nfev'],
                          ftol=2e-5,xtol=2e-5,gtol=2e-5)
        row={'fixed_Cr':cr,'cost':float(fit.cost),'relative_cost_increase':float((fit.cost-best['cost'])/best['cost']),
             'success':bool(fit.success),'estimate':expand(fit.x).tolist()}
        profile.append(row);write_json(EXPERIMENT/'cr_profile_progress.json',{'rows':profile})
        print('Stage8 Cr profile:',row,flush=True)
    result={'candidate':parameters,'fits':runs,'metrics':rows,'Cr_profile':profile,
            'selection_used_trip588_or600':False,'evaluation_is_untouched':False,
            'thermal_accuracy_measured':False,'old_model_unchanged':True}
    write_json(result_path,result)
    pd.DataFrame(rows).to_csv(EXPERIMENT/'calibration_metrics.csv',index=False,encoding='utf-8-sig')
    return result


class CabinBiasObserver:
    """只接收含偏差读数、环境与实际执行功率；不读取隐藏温度或偏差。"""
    def __init__(self,parameters,*,euler_step_s=.5):
        self.p=parameters
        self.cfg=protocol()['cabin_observer']
        self.euler_step_s=euler_step_s
        self.state=None;self.last_time=None
        self.previous_action={'ac_w':0.,'heater_w':0.}
        self.samples=[]
        self.gains={}

    def update(self,now,measurement,ambient):
        if self.state is None:
            self.state=np.array([float(measurement),0.]);self.last_time=now
        elif now-self.last_time>=self.cfg['measurement_period_s']-1e-8:
            dt=now-self.last_time
            p=self.p;k=p['cabin_environment_w_k'];c=p['cabin_heat_capacity_j_k']
            # 与原显式积分器相同的离散热动力学；观测器只使用已执行功率。
            n=max(1,int(round(dt/self.euler_step_s)));step=dt/n
            a=(1-k*step/c)**n
            heat=p['solar_heat_w']+p['occupant_heat_w']+p['heater_efficiency']*self.previous_action['heater_w']-p['cooling_cop']*self.previous_action['ac_w']
            predicted=np.array([a*self.state[0]+(1-a)*(ambient+heat/k),self.state[1]])
            f=np.diag([a,1.]);h=np.array([[1.,1.]])
            poles=np.exp(-dt/np.asarray(self.cfg['pole_time_constants_s']))
            # posterior update (I-LH)F；先配置F-KH，再用L=F^{-1}K。
            if dt not in self.gains:
                gain=place_poles(f.T,h.T,poles).gain_matrix.T
                self.gains[dt]=np.linalg.solve(f,gain).ravel()
            self.state=predicted+self.gains[dt]*(measurement-float((h@predicted).item()))
            self.last_time=now
        self.samples.append((now,float(self.state[0]),float(self.state[1])))
        return float(self.state[0])


class ImprovedController(DriveAndRecoveryController):
    """同一温度观测器及到达前电池回温用于两种策略；偏差只由外部传感器注入。"""
    def __init__(self,parameters,rule,driving_end_s,*,observer_parameters=None,step_s=.5):
        super().__init__(parameters,rule,driving_end_s,sensor_bias_c=0.)
        self.original_rule=rule
        self.observer=CabinBiasObserver(observer_parameters or parameters,euler_step_s=step_s)

    def __call__(self,observation):
        now=observation['time_s']
        estimated=self.observer.update(now,observation['cabin_c'],observation['ambient_c'])
        corrected={**observation,'cabin_c':estimated}
        remaining=self.driving_end_s-now
        cfg=protocol()
        if observation['ambient_c']>=35 and remaining<=cfg['battery_terminal_return_s']:
            self.drive.rule=replace(self.original_rule,cooling_on_c=cfg['hot_battery_return_on_c'],
                                    cooling_hysteresis_c=cfg['hot_battery_return_hysteresis_c'])
        action=super().__call__(corrected)
        # 不向观测器提供原始真实温度；只记录实际执行器动作。
        self.observer.previous_action=dict(action)
        return action


def improved_journey(base,rule,ambient,repetitions,*,parking=(3600.,7200.),step_s=.5,
                     sensor_bias=0.,noise=0.,plant_changes=None,ambient_bias=0.):
    cfg=control_protocol()
    nominal=scenario_parameters(base,ambient,cfg)
    plant={**nominal,**(plant_changes or {})}
    cycle=repeat_cycle(load_cltc(ambient)[0],repetitions)
    end=float(cycle.time_s.iloc[-1])
    initial=InitialState(.94,ambient,24.)
    controller=ImprovedController(nominal,rule,end,step_s=step_s)
    def sensor_interface(actual):
        # 隐藏真值仅在仿真环境注入偏差/计算评分；控制器收到读数副本。
        wave=noise*np.sin(2*np.pi*actual['time_s']/37.)
        return controller({**actual,'cabin_c':actual['cabin_c']+sensor_bias+wave,
                           'battery_c':actual['battery_c']+sensor_bias,
                           'ambient_c':actual['ambient_c']+ambient_bias})
    segments=[];outcomes=[];state=initial;previous=0.
    for parked in [0.,*parking]:
        segment=cycle if parked==0 else pd.DataFrame({'time_s':[end+previous,end+parked],'speed_m_s':0.,'ambient_c':ambient})
        try:
            trace,_=simulate(segment,plant,state,max_step_s=step_s,controller=sensor_interface)
        except InfeasibleSimulation as error:
            outcomes.append({'parking_s':parked,'completed':False,'feasible':False,'failure':str(error)})
            break
        segments.append(trace)
        state=InitialState(float(trace.soc.iloc[-1]),float(trace.battery_c.iloc[-1]),float(trace.cabin_c.iloc[-1]))
        entire=pd.concat(segments,ignore_index=True)
        outcomes.append({'parking_s':parked,'completed':True,'ambient_c':ambient,
                         **summarize_trace(entire,initial,plant,controller)})
        previous=parked
    return {'ambient_c':ambient,'repetitions':repetitions,'rule':asdict(rule),'outcomes':outcomes,
            'bias_estimate_final_c':float(controller.observer.state[1]),
            'true_injected_sensor_bias_c':sensor_bias,'noise_amplitude_c':noise},pd.concat(segments,ignore_index=True)


def thermal_experiment():
    begin();target=EXPERIMENT/'thermal_results.json'
    if target.exists():return read_json(target)
    cfg=protocol();old=read_json(PROJECT_ROOT/'configs/calibrated.json');ref=load_parameters()
    rules={'baseline':Rule(**control_protocol()['baseline']),
           'optimized':Rule(**read_json(PROJECT_ROOT/'configs/optimized_rule.json'))}
    comparisons=[];journeys=[];sensors=[];stress=[];convergence=[];transfer=[]
    for label,base in [('calibrated_v2_provisional',old),('original_reference',ref)]:
        for ambient in cfg['temperatures_c']:
            for reps in cfg['long_repetitions']:
                pair={}
                for strategy,rule in rules.items():
                    journey,trace=improved_journey(base,rule,ambient,reps)
                    journey.update(parameter_set=label,strategy=strategy);journeys.append(journey);pair[strategy]=journey
                for b,o in zip(pair['baseline']['outcomes'],pair['optimized']['outcomes']):
                    comparisons.append({'parameter_set':label,'ambient_c':ambient,'repetitions':reps,**compare_checkpoint(base,b,o)})
                print('Stage8 thermal:',label,ambient,reps,flush=True)
    for ambient in cfg['temperatures_c']:
        for bias in cfg['cabin_observer']['sensor_bias_sweep_c']:
            for strategy,rule in rules.items():
                journey,_=improved_journey(old,rule,ambient,1,parking=(),sensor_bias=bias)
                point=journey['outcomes'][-1]
                sensors.append({'ambient_c':ambient,'sensor_bias_c':bias,'strategy':strategy,
                                'estimated_bias_c':journey['bias_estimate_final_c'],**point})
        for name in cfg['cabin_observer']['stress_cases']:
            changes={};noise=0.;ambient_bias=0.
            nominal=scenario_parameters(old,ambient,control_protocol())
            if name=='measurement_noise':noise=cfg['cabin_observer']['noise_amplitude_c']
            elif name=='cabin_heat_capacity_plus20pct':changes['cabin_heat_capacity_j_k']=nominal['cabin_heat_capacity_j_k']*1.2
            elif name=='cabin_conductance_plus20pct':changes['cabin_environment_w_k']=nominal['cabin_environment_w_k']*1.2
            elif name=='occupant_heat_plus50W':changes['occupant_heat_w']=nominal['occupant_heat_w']+50.
            elif name=='ambient_sensor_plus0.5C':ambient_bias=.5
            for bias in [-1.,1.]:
                for strategy,rule in rules.items():
                    journey,_=improved_journey(old,rule,ambient,1,parking=(),sensor_bias=bias,noise=noise,
                                               plant_changes=changes,ambient_bias=ambient_bias)
                    stress.append({'case':name,'ambient_c':ambient,'sensor_bias_c':bias,'strategy':strategy,
                                   'estimated_bias_c':journey['bias_estimate_final_c'],**journey['outcomes'][-1]})
    for ambient,reps in [(30.,2),(35.,4),(45.,2)]:
        pair={}
        for strategy,rule in rules.items():
            journey,_=improved_journey(old,rule,ambient,reps)
            pair[strategy]=journey
        for b,o in zip(pair['baseline']['outcomes'],pair['optimized']['outcomes']):
            transfer.append({'ambient_c':ambient,'repetitions':reps,**compare_checkpoint(old,b,o)})
    for ambient in cfg['temperatures_c']:
        for strategy,rule in rules.items():
            coarse,_=improved_journey(old,rule,ambient,6,step_s=.5)
            fine,_=improved_journey(old,rule,ambient,6,step_s=.25)
            for a,b in zip(coarse['outcomes'],fine['outcomes']):
                if not(a['completed'] and b['completed']):
                    convergence.append({'ambient_c':ambient,'strategy':strategy,'passed':False});continue
                energy=abs(a['energy_kwh']-b['energy_kwh'])/abs(b['energy_kwh'])
                tb=abs(a['battery_final_c']-b['battery_final_c']);tc=abs(a['cabin_final_c']-b['cabin_final_c'])
                convergence.append({'ambient_c':ambient,'strategy':strategy,'parking_s':a['parking_s'],
                                    'energy_relative_difference':energy,'battery_difference_c':tb,'cabin_difference_c':tc,
                                    'passed':bool(energy<.01 and tb<.1 and tc<.1)})
    result={'journeys':journeys,'comparisons':comparisons,'sensor_trials':sensors,'stress_trials':stress,
            'transfer':transfer,'convergence':convergence,'new_policy_after_old_failures':True,
            'real_sensor_calibration':False,'measured_thermal_validation':False}
    write_json(target,result)
    for name in ['comparisons','sensor_trials','stress_trials','transfer','convergence']:
        pd.DataFrame(result[name]).to_csv(EXPERIMENT/(name+'.csv'),index=False,encoding='utf-8-sig')
    return result


def run_remediation(part='all'):
    if part in ['calibration','all']:calibration_experiment()
    if part in ['thermal','all']:thermal_experiment()
    verify_sources();verify_evidence()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('part',nargs='?',default='all',choices=['all','calibration','thermal'])
    run_remediation(parser.parse_args().part)
