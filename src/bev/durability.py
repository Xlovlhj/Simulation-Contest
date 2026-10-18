"""冻结规则的连续工况、实际停车温控与测温偏差检验。"""
from __future__ import annotations

from dataclasses import asdict
import time

import numpy as np
import pandas as pd

from .control import FeedbackController, Rule, compare_thermal_accounting, control_protocol, scenario_parameters
from .cycles import load_cltc
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import InitialState, InfeasibleSimulation, load_parameters, simulate


def protocol() -> dict:
    return read_json(PROJECT_ROOT / "configs/durability_protocol.json")


def repeat_cycle(cycle: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    """同一连续行程，仅保留一次公共零速端点；状态不会在循环边界重置。"""
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("循环次数必须是正整数")
    if abs(float(cycle.speed_m_s.iloc[0]))>1e-12 or abs(float(cycle.speed_m_s.iloc[-1]))>1e-12:
        raise ValueError("拼接工况必须以零速起止，不能凭空连接不同车速")
    source = cycle.assign(time_s=cycle.time_s-cycle.time_s.iloc[0])
    duration = float(source.time_s.iloc[-1])
    if duration<=0 or (np.diff(source.time_s)<=0).any():
        raise ValueError("工况时间必须严格递增")
    parts = [source.assign(time_s=source.time_s+index*duration).iloc[0 if index==0 else 1:] for index in range(repetitions)]
    return pd.concat(parts, ignore_index=True)


class DriveAndRecoveryController:
    """驾驶保持原冻结控制；停车切共同策略，保留真实时间与冷却开关历史。"""
    def __init__(self, parameters: dict, rule: Rule, driving_end_s: float, *, sensor_bias_c: float=0.):
        self.p, self.cfg, self.extension = parameters, control_protocol(), protocol()
        self.driving_end_s = driving_end_s
        self.drive = FeedbackController(parameters, rule, self.cfg, sensor_bias_c=sensor_bias_c)
        self.in_parking = False
        self.cooler_on = False
        self.last_switch = -float("inf")
        self.switch_times = []
        self.next_decision = -float("inf")
        self.ac_w = self.heater_w = 0.

    def __call__(self, observation: dict) -> dict:
        now = observation["time_s"]
        if now < self.driving_end_s-1e-8:
            # simulate的总段终点不影响驾驶者已声明的计划到达时间。
            return self.drive({**observation, "remaining_s":self.driving_end_s-now})
        if not self.in_parking:
            self.in_parking = True
            self.cooler_on = self.drive.cooler_on
            self.last_switch = self.drive.last_switch
            self.switch_times = list(self.drive.switch_times)
        p, cfg, ext = self.p, self.cfg, self.extension
        tb, tc, ta = observation["battery_c"], observation["cabin_c"], observation["ambient_c"]
        if now+1e-9>=self.next_decision:
            if ta<25:
                desired = False
            else:
                target = ext["parking_hot_target_c"] if ta>=35 else ext["parking_warm_target_c"]
                desired = self.cooler_on
                if tb>=target+ext["parking_cooling_halfband_c"]:
                    desired = True
                elif tb<=target-ext["parking_cooling_halfband_c"]:
                    desired = False
            if tb>=cfg["safety_cooling_on_c"]:
                desired = True
            if desired!=self.cooler_on and (now-self.last_switch>=cfg["minimum_switch_dwell_s"] or tb>=cfg["safety_cooling_on_c"]):
                self.cooler_on=desired
                self.last_switch=now
                self.switch_times.append(now)
            target = ext["parking_cabin_target_c"]
            passive = p["cabin_environment_w_k"]*(ta-target)+p["solar_heat_w"]+p["occupant_heat_w"]
            requested = cfg["cabin_feedback_w_k"]*(target-tc)-passive
            self.heater_w=min(p["heater_max_w"],max(requested,0)/p["heater_efficiency"])
            self.ac_w=min(p["ac_max_w"],max(-requested,0)/p["cooling_cop"])
            self.next_decision=now+cfg["control_period_s"]
        cooler_w = p["cooler_conductance_w_k"]*max(tb-p["coolant_temperature_c"],0)/p["cooling_cop"] if self.cooler_on else 0.
        return {"regen_fraction":0.,"cooler_on":self.cooler_on,
                "ac_w":min(self.ac_w,max(0.,p["compressor_total_max_w"]-cooler_w)),"heater_w":self.heater_w}


def summarize_trace(trace: pd.DataFrame, initial: InitialState, p: dict, controller: DriveAndRecoveryController) -> dict:
    cfg, ext = control_protocol(), protocol()
    dt=trace.dt_s.to_numpy()
    final=trace.iloc[-1]
    switches=controller.switch_times if controller.in_parking else controller.drive.switch_times
    observed_dwell=min(np.diff(switches)) if len(switches)>1 else None
    tb=np.r_[initial.battery_c,trace.battery_c]
    tc=np.r_[initial.cabin_c,trace.cabin_c]
    energy=float(np.sum(trace.battery_w*dt)/3.6e6)
    ocv_energy=float(np.sum(trace.ocv_electric_w*dt)/3.6e6)
    heat_in=float(np.sum(trace.battery_net_heat_w*dt))
    cabin_heat_in=float(np.sum(trace.cabin_net_heat_w*dt))
    conditions={"cabin_comfort":bool(tc.min()>=cfg["cabin_comfort_min_c"]-1e-8 and tc.max()<=cfg["cabin_comfort_max_c"]+1e-8),
                "cabin_terminal":bool(abs(final.cabin_c-cfg["cabin_target_c"])<=ext["strict_cabin_terminal_tolerance_c"]),
                "battery_temperature":bool(tb.min()>=cfg["battery_temperature_min_c"] and tb.max()<=cfg["battery_temperature_max_c"]),
                "battery_hot_terminal":bool(initial.battery_c<35 or final.battery_c<=initial.battery_c+cfg["battery_terminal_hot_rise_max_c"]+1e-8),
                "compressor_budget":bool((trace.ac_w+trace.cooler_w<=p["compressor_total_max_w"]+1e-8).all()),
                "minimum_switch_dwell":bool(observed_dwell is None or observed_dwell>=cfg["minimum_switch_dwell_s"]-1e-8)}
    branch=trace.motor_dc_w+trace.low_voltage_w+trace.ac_w+trace.heater_w+trace.cooler_w
    return {"energy_kwh":energy,"ocv_energy_kwh":ocv_energy,"distance_km":float(np.sum(trace.speed_m_s*dt)/1000),
            "duration_s":float(dt.sum()),"soc_final":float(final.soc),"battery_final_c":float(final.battery_c),
            "battery_max_c":float(tb.max()),"cabin_final_c":float(final.cabin_c),"cabin_min_c":float(tc.min()),"cabin_max_c":float(tc.max()),
            "comfort_violation_s":float(np.sum(dt*((trace.cabin_c<cfg["cabin_comfort_min_c"]-1e-8)|(trace.cabin_c>cfg["cabin_comfort_max_c"]+1e-8)))),
            "soc_outside_training_fraction":float(np.sum(dt*(trace.soc<ext["training_soc_min"]))/dt.sum()),
            "cooling_energy_kwh":float(np.sum(trace.cooler_w*dt)/3.6e6),"ac_energy_kwh":float(np.sum(trace.ac_w*dt)/3.6e6),
            "heater_energy_kwh":float(np.sum(trace.heater_w*dt)/3.6e6),
            "battery_heat_balance_residual_j":float(p["battery_heat_capacity_j_k"]*(final.battery_c-initial.battery_c)-heat_in),
            "cabin_heat_balance_residual_j":float(p["cabin_heat_capacity_j_k"]*(final.cabin_c-initial.cabin_c)-cabin_heat_in),
            "dc_balance_residual_kwh":float(np.sum((trace.battery_w-branch)*dt)/3.6e6),
            "minimum_observed_dwell_s":float(observed_dwell) if observed_dwell is not None else None,
            "constraints":conditions,"feasible":bool(all(conditions.values()))}


def run_journey(base: dict, rule: Rule, ambient_c: float, repetitions: int, *, step_s: float=.5,
                parking_checkpoints: list[float]|None=None) -> tuple[dict, pd.DataFrame]:
    ext, cfg = protocol(),control_protocol()
    checkpoints=ext["parking_checkpoint_s"] if parking_checkpoints is None else parking_checkpoints
    if any(b<=a for a,b in zip([0.]+list(checkpoints),checkpoints)):
        raise ValueError("停车检查点必须严格递增且为正")
    cycle=repeat_cycle(load_cltc(ambient_c)[0],repetitions)
    drive_end=float(cycle.time_s.iloc[-1])
    p=scenario_parameters(base,ambient_c,cfg)
    initial=InitialState(soc=ext["initial_soc"],battery_c=ambient_c,cabin_c=ext["initial_cabin_c"])
    controller=DriveAndRecoveryController(p,rule,drive_end)
    segments=[]
    outcomes=[]
    state=initial
    started=time.perf_counter()
    for parked in [0.]+list(checkpoints):
        segment=cycle if parked==0 else pd.DataFrame({"time_s":[drive_end+(outcomes[-1]["parking_s"]),drive_end+parked],"speed_m_s":0.,"ambient_c":ambient_c})
        try:
            trace,_=simulate(segment,p,state,max_step_s=step_s,
                             scenario="repeated_public_CLTC_P" if parked==0 else "common_powered_parking",
                             controller=controller,verified_cltc=parked==0 and repetitions==1)
        except InfeasibleSimulation as error:
            outcomes.append({"parking_s":parked,"completed":False,"failure":str(error),"feasible":False})
            for remaining in checkpoints:
                if remaining>parked:
                    outcomes.append({"parking_s":remaining,"completed":False,"failure":"Earlier segment failed; not simulated", "feasible":False})
            break
        segments.append(trace)
        state=InitialState(soc=float(trace.soc.iloc[-1]),battery_c=float(trace.battery_c.iloc[-1]),cabin_c=float(trace.cabin_c.iloc[-1]))
        entire=pd.concat(segments,ignore_index=True)
        summary=summarize_trace(entire,initial,p,controller)
        outcomes.append({"parking_s":parked,"completed":True,"ambient_c":ambient_c,**summary})
    return {"ambient_c":ambient_c,"repetitions":repetitions,"driving_duration_s":drive_end,"rule":asdict(rule),"outcomes":outcomes,
            "runtime_s":time.perf_counter()-started},pd.concat(segments,ignore_index=True) if segments else pd.DataFrame()


def compare_checkpoint(base: dict, baseline: dict, optimized: dict) -> dict:
    ext=protocol()
    completed=baseline["completed"] and optimized["completed"]
    result={"parking_s":baseline["parking_s"],"completed":completed,"baseline_feasible":baseline["feasible"],"optimized_feasible":optimized["feasible"]}
    if not completed:
        result["failure"]={"baseline":baseline.get("failure"),"optimized":optimized.get("failure")}
        return result
    db=optimized["battery_final_c"]-baseline["battery_final_c"]
    dc=optimized["cabin_final_c"]-baseline["cabin_final_c"]
    equivalent=abs(db)<=ext["equivalent_battery_end_difference_c"] and abs(dc)<=ext["equivalent_cabin_end_difference_c"]
    return {**result,"baseline_energy_kwh":baseline["energy_kwh"],"optimized_energy_kwh":optimized["energy_kwh"],
            "saved_energy_kwh":baseline["energy_kwh"]-optimized["energy_kwh"],
            "battery_end_difference_c":db,"cabin_end_difference_c":dc,"near_equal_thermal_end":bool(equivalent),
            "valid_close_end_comparison":bool(baseline["feasible"] and optimized["feasible"] and equivalent),
            "baseline_battery_final_c":baseline["battery_final_c"],"optimized_battery_final_c":optimized["battery_final_c"],
            "baseline_soc_final":baseline["soc_final"],"optimized_soc_final":optimized["soc_final"],
            "baseline_soc_outside_training_fraction":baseline["soc_outside_training_fraction"],
            "optimized_soc_outside_training_fraction":optimized["soc_outside_training_fraction"],
            **compare_thermal_accounting(base,baseline,optimized)}


def evaluate_sensor_bias(base: dict, baseline_rule: Rule, optimized_rule: Rule) -> list[dict]:
    cfg, ext=control_protocol(),protocol()
    rows=[]
    for ambient in ext["temperatures_c"]:
        cycle=repeat_cycle(load_cltc(ambient)[0],ext["sensor_sweep_repetitions"])
        p=scenario_parameters(base,ambient,cfg)
        initial=InitialState(ext["initial_soc"],ambient,ext["initial_cabin_c"])
        for bias in ext["sensor_bias_sweep_c"]:
            for label,rule in [("baseline",baseline_rule),("optimized",optimized_rule)]:
                controller=DriveAndRecoveryController(p,rule,float(cycle.time_s.iloc[-1]),sensor_bias_c=bias)
                try:
                    trace,_=simulate(cycle,p,initial,max_step_s=ext["step_s"],controller=controller)
                except InfeasibleSimulation as error:
                    rows.append({"ambient_c":ambient,"sensor_bias_c":bias,"strategy":label,
                                 "completed":False,"feasible":False,"failure":str(error)})
                    continue
                summary=summarize_trace(trace,initial,p,controller)
                gain=cfg["cabin_feedback_w_k"]/(cfg["cabin_feedback_w_k"]+p["cabin_environment_w_k"])
                rows.append({"ambient_c":ambient,"sensor_bias_c":bias,"strategy":label,
                             "completed":True,"true_cabin_end_error_c":summary["cabin_final_c"]-cfg["cabin_target_c"],
                             "ideal_steady_error_c":-gain*bias,"feasible":summary["feasible"],
                             "cabin_comfort":summary["constraints"]["cabin_comfort"],
                             "cabin_terminal":summary["constraints"]["cabin_terminal"],"cabin_min_c":summary["cabin_min_c"],
                             "cabin_max_c":summary["cabin_max_c"],"battery_max_c":summary["battery_max_c"]})
    return rows


def identity() -> dict:
    names=["configs/durability_protocol.json","configs/control_protocol.json","configs/calibrated.json","configs/reference.json",
           "configs/optimized_rule.json","records/control_freeze.json","records/cltc_downloads.json","src/bev/model.py","src/bev/control.py","src/bev/durability.py","checks/test_durability.py"]
    return {name:sha256(PROJECT_ROOT/name) for name in names}


def evaluate_durability(*, recompute: bool=False) -> dict:
    verify_sources()
    cfg,ext=control_protocol(),protocol()
    frozen=read_json(PROJECT_ROOT/"records/control_freeze.json")
    for name,digest in frozen["fingerprint"].items():
        if sha256(PROJECT_ROOT/name)!=digest:
            raise ValueError("阶段三冻结输入已变化："+name)
    # 在任何新实验运行前记录协议与代码；重复运行保留原冻结，不悄悄改协议。
    freeze_path=PROJECT_ROOT/"records/durability_freeze.json"
    if freeze_path.exists():
        if read_json(freeze_path)["fingerprint"]!=identity():
            raise ValueError("阶段四评价协议或代码已变化；请保留原实验并显式建立新版本")
    else:
        write_json(freeze_path,{"status":"evaluation_protocol_fixed_before_runs","fingerprint":identity(),"retuning":False})
    result_path=PROJECT_ROOT/"results/tables/durability_evaluation.json"
    if not recompute and result_path.exists():
        cached=read_json(result_path)
        if cached.get("freeze_sha256")==sha256(freeze_path):
            return cached
    baseline_rule,optimized_rule=Rule(**cfg["baseline"]),Rule(**frozen["selected_rule"])
    parameters={"calibrated_v2_provisional":read_json(PROJECT_ROOT/"configs/calibrated.json"),"original_reference":load_parameters()}
    journeys,comparisons,convergence=[],[],[]
    for name,base in parameters.items():
        for ambient in ext["temperatures_c"]:
            for repetitions in ext["repetitions"]:
                pair={}
                for label,rule in [("baseline",baseline_rule),("optimized",optimized_rule)]:
                    journey,trace=run_journey(base,rule,ambient,repetitions,step_s=ext["step_s"])
                    journey.update(parameter_set=name,strategy=label)
                    pair[label]=journey
                    journeys.append(journey)
                    if name=="calibrated_v2_provisional" and repetitions==3:
                        trace.to_csv(PROJECT_ROOT/f"results/timeseries/durability_{ambient:g}C_{label}.csv",index=False,encoding="utf-8-sig")
                for baseline,optimized in zip(pair["baseline"]["outcomes"],pair["optimized"]["outcomes"]):
                    comparison=compare_checkpoint(base,baseline,optimized)
                    comparison.update(parameter_set=name,ambient_c=ambient,repetitions=repetitions)
                    comparisons.append(comparison)
                print(f"Continuous/recovery: {name}, {ambient:g}C, {repetitions} CLTC cycles",flush=True)
                if name==ext["convergence_parameter_set"] and repetitions==ext["convergence_repetitions"]:
                    for label,rule in [("baseline",baseline_rule),("optimized",optimized_rule)]:
                        fine,_=run_journey(base,rule,ambient,repetitions,step_s=ext["convergence_step_s"])
                        for coarse_point,fine_point in zip(pair[label]["outcomes"],fine["outcomes"]):
                            check={"ambient_c":ambient,"strategy":label,"parking_s":coarse_point["parking_s"],"passed":False}
                            if coarse_point["completed"] and fine_point["completed"]:
                                check.update(energy_relative_difference=abs(coarse_point["energy_kwh"]-fine_point["energy_kwh"])/abs(fine_point["energy_kwh"]),
                                             battery_end_abs_difference_c=abs(coarse_point["battery_final_c"]-fine_point["battery_final_c"]),
                                             cabin_end_abs_difference_c=abs(coarse_point["cabin_final_c"]-fine_point["cabin_final_c"]))
                                check["passed"]=check["energy_relative_difference"]<.01 and check["battery_end_abs_difference_c"]<.1 and check["cabin_end_abs_difference_c"]<.1
                            convergence.append(check)
    sensors=evaluate_sensor_bias(parameters["calibrated_v2_provisional"],baseline_rule,optimized_rule)
    result={"freeze_sha256":sha256(PROJECT_ROOT/"records/durability_freeze.json"),"journeys":journeys,"comparisons":comparisons,
            "sensor_sweep":sensors,"step_convergence":convergence,"all_step_checks_passed":all(c["passed"] for c in convergence),
            "limits":["Finite 30/90/180-minute driving; no claim of asymptotic thermal periodic state.",
                      "Original calibration and stage3 controller frozen; lowSOC and temperatures remain outside measured training support.",
                      "Common powered parking includes cabin holding, solar/occupant and low-voltage energy; not an unattended vehicle parking prediction.",
                      "Near-equal terminal temperatures use explicit tolerances; residual thermal accounting remains an estimate.",
                      "No real thermal or sensor calibration data; sensor sweep is a controlled simulation."]}
    write_json(PROJECT_ROOT/"results/tables/durability_evaluation.json",result)
    pd.DataFrame([{k:v for k,v in row.items() if k!="failure"} for row in comparisons]).to_csv(PROJECT_ROOT/"results/tables/durability_comparisons.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(sensors).to_csv(PROJECT_ROOT/"results/tables/sensor_bias_sweep.csv",index=False,encoding="utf-8-sig")
    verify_sources()
    return result
