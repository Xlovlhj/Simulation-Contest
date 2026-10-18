"""离线网格寻优、在线反馈规则和公平控制对照；AI辅助见records。"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product
import time

import numpy as np
import pandas as pd

from .cycles import load_cltc
from .data import read_reference_cycle
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import InitialState, InfeasibleSimulation, load_parameters, simulate


@dataclass(frozen=True)
class Rule:
    regen_fraction: float
    cooling_on_c: float
    cooling_hysteresis_c: float
    cabin_economy_offset_c: float


def control_protocol() -> dict:
    return read_json(PROJECT_ROOT/"configs/control_protocol.json")


class FeedbackController:
    """5s采样反馈；只看当前状态和计划剩余时间，不预读未来车速。"""
    def __init__(self, parameters: dict, rule: Rule, cfg: dict, *, sensor_bias_c: float = 0.):
        self.parameters, self.rule, self.cfg = parameters, rule, cfg
        self.sensor_bias_c = sensor_bias_c
        self.next_decision = -float("inf")
        self.last_switch = -float("inf")
        self.cooler_on = False
        self.switch_times = []
        self.compute_times = []
        self.requested_ac = self.requested_heater = 0.

    def __call__(self, observation: dict) -> dict:
        p, cfg, rule = self.parameters, self.cfg, self.rule
        now = observation["time_s"]
        tb = observation["battery_c"]+self.sensor_bias_c
        tc = observation["cabin_c"]+self.sensor_bias_c
        ta = observation["ambient_c"]
        if now+1e-9 >= self.next_decision:
            started = time.perf_counter()
            desired = self.cooler_on
            if tb >= rule.cooling_on_c:
                desired = True
            elif tb <= rule.cooling_on_c-rule.cooling_hysteresis_c:
                desired = False
            if tb >= cfg["safety_cooling_on_c"]:
                desired = True
            if desired != self.cooler_on and (now-self.last_switch >= cfg["minimum_switch_dwell_s"] or tb>=cfg["safety_cooling_on_c"]):
                self.cooler_on = desired
                self.last_switch = now
                self.switch_times.append(now)
            direction = 1 if ta>=cfg["cabin_target_c"] else -1
            target = cfg["cabin_target_c"]+direction*rule.cabin_economy_offset_c
            # 计划到达前回到共同的24℃终态；该时间信息需求需在报告中说明。
            if observation["remaining_s"] <= cfg["terminal_cabin_return_s"]:
                target = cfg["cabin_target_c"]
            passive_heat = p["cabin_environment_w_k"]*(ta-target)+p["solar_heat_w"]+p["occupant_heat_w"]
            requested_heat = cfg["cabin_feedback_w_k"]*(target-tc)-passive_heat
            self.requested_heater = min(p["heater_max_w"], max(0., requested_heat)/p["heater_efficiency"])
            self.requested_ac = min(p["ac_max_w"], max(0., -requested_heat)/p["cooling_cop"])
            self.next_decision = now+cfg["control_period_s"]
            self.compute_times.append(time.perf_counter()-started)
        # 制冷功率预算属于执行器分配，按当前移热功率扣除电池支路。
        battery_cooling_w = p["cooler_conductance_w_k"]*max(observation["battery_c"]-p["coolant_temperature_c"],0)/p["cooling_cop"] if self.cooler_on else 0.
        remaining_compressor = max(0., p["compressor_total_max_w"]-battery_cooling_w)
        return {"regen_fraction": rule.regen_fraction, "cooler_on": self.cooler_on,
                "ac_w": min(self.requested_ac, remaining_compressor), "heater_w": self.requested_heater}

    def statistics(self) -> dict:
        durations = np.asarray(self.compute_times)
        return {"decisions": len(durations), "decision_period_s": self.cfg["control_period_s"],
                "decision_median_us": float(np.median(durations)*1e6) if len(durations) else None,
                "decision_p95_us": float(np.quantile(durations,.95)*1e6) if len(durations) else None,
                "decision_max_us": float(np.max(durations)*1e6) if len(durations) else None,
                "cooling_switches": len(self.switch_times), "switch_times_s": self.switch_times,
                "minimum_observed_dwell_s": float(np.min(np.diff(self.switch_times))) if len(self.switch_times)>1 else None}


def scenario_parameters(base: dict, ambient_c: float, cfg: dict) -> dict:
    return {**base, "solar_heat_w": 500. if ambient_c>=35 else 0.,
            "compressor_total_max_w": cfg["compressor_total_max_w"]}


def simulate_rule(base: dict, rule: Rule, ambient_c: float, *, cycle_name: str = "CLTC", step_s: float=.5,
                  sensor_bias_c: float=0.) -> tuple[pd.DataFrame, dict]:
    cfg = control_protocol()
    p = scenario_parameters(base, ambient_c, cfg)
    cycle = load_cltc(ambient_c)[0] if cycle_name=="CLTC" else read_reference_cycle().assign(ambient_c=ambient_c)
    controller = FeedbackController(p, rule, cfg, sensor_bias_c=sensor_bias_c)
    initial = InitialState(soc=cfg["initial_soc"], battery_c=ambient_c, cabin_c=cfg["initial_cabin_c"])
    started = time.perf_counter()
    trace, out = simulate(cycle, p, initial, max_step_s=step_s, scenario=f"control_{cycle_name}_{ambient_c:g}C",
                          verified_cltc=cycle_name=="CLTC", controller=controller)
    elapsed = time.perf_counter()-started
    dt = trace.dt_s.to_numpy()
    tc = np.r_[initial.cabin_c, trace.cabin_c]
    tb = np.r_[initial.battery_c, trace.battery_c]
    comfort_violation = (trace.cabin_c<cfg["cabin_comfort_min_c"]-1e-8)|(trace.cabin_c>cfg["cabin_comfort_max_c"]+1e-8)
    hot_terminal_ok = ambient_c<35 or out["battery_final_c"] <= initial.battery_c+cfg["battery_terminal_hot_rise_max_c"]+1e-8
    statistics = controller.statistics()
    dwell_ok = statistics["minimum_observed_dwell_s"] is None or statistics["minimum_observed_dwell_s"]>=cfg["minimum_switch_dwell_s"]-1e-8
    constraints = {"cabin_comfort": bool(not comfort_violation.any()),
                   "cabin_terminal": bool(abs(out["cabin_final_c"]-cfg["cabin_target_c"])<=cfg["terminal_cabin_tolerance_c"]),
                   "battery_temperature": bool(tb.min()>=cfg["battery_temperature_min_c"] and tb.max()<=cfg["battery_temperature_max_c"]),
                   "battery_hot_terminal": bool(hot_terminal_ok), "minimum_switch_dwell": bool(dwell_ok),
                   "compressor_budget": bool((trace.ac_w+trace.cooler_w<=cfg["compressor_total_max_w"]+1e-8).all())}
    metrics = {"cycle": cycle_name, "ambient_c": ambient_c, "energy_kwh": out["net_energy_kwh"],
               "ocv_energy_kwh": out["ocv_electric_energy_kwh"], "consumption_kwh_100km": out["consumption_kwh_100km"],
               "regen_energy_kwh": out["regen_dc_energy_kwh"], "cooling_energy_kwh": out["energy_kwh"]["cooler_w"],
               "ac_energy_kwh": out["energy_kwh"]["ac_w"], "heater_energy_kwh": out["energy_kwh"]["heater_w"],
               "battery_final_c": out["battery_final_c"], "battery_max_c": float(tb.max()),
               "cabin_final_c": out["cabin_final_c"], "cabin_min_c": float(tc.min()), "cabin_max_c": float(tc.max()),
               "comfort_violation_s": float(np.sum(dt*comfort_violation)),
               "mean_abs_cabin_deviation_c": float(np.sum(dt*np.abs(trace.cabin_c-24))/dt.sum()),
               "soc_final": out["soc_final"], "time_outside_training_SOC_fraction": float(np.sum(dt*(trace.soc<.876829299927))/dt.sum()),
               "feasible": bool(all(constraints.values())), "constraints": constraints,
               "dc_balance_residual_kwh": out["dc_balance_residual_kwh"], "runtime_s": elapsed,
               "controller": statistics, "rule": asdict(rule)}
    return trace, metrics


def compare_thermal_accounting(base: dict, baseline: dict, candidate: dict) -> dict:
    # 只对候选比基准更热的电池计入理想移热电耗；冷环境不把有益余热当成必须制冷。
    extra_battery = base["battery_heat_capacity_j_k"]*max(candidate["battery_final_c"]-baseline["battery_final_c"],0)/base["cooling_cop"]/3.6e6 if candidate["ambient_c"]>=25 else 0.
    extra_cabin = base["cabin_heat_capacity_j_k"]*abs(candidate["cabin_final_c"]-baseline["cabin_final_c"])/min(base["cooling_cop"],base["heater_efficiency"])/3.6e6
    corrected = candidate["energy_kwh"]+extra_battery+extra_cabin
    return {"terminal_battery_heat_correction_kwh": extra_battery, "terminal_cabin_correction_kwh": extra_cabin,
            "corrected_energy_kwh": corrected,
            "raw_saving_pct": 100*(baseline["energy_kwh"]-candidate["energy_kwh"])/baseline["energy_kwh"],
            "heat_corrected_saving_pct": 100*(baseline["energy_kwh"]-corrected)/baseline["energy_kwh"]}


def _identity() -> dict:
    return {name: sha256(PROJECT_ROOT/name) for name in ["configs/control_protocol.json", "configs/calibrated.json",
            "configs/reference.json", "records/cltc_downloads.json", "src/bev/model.py", "src/bev/control.py"]}


def optimize_rules(*, recompute: bool=False) -> dict:
    verify_sources()
    cfg = control_protocol()
    freeze_path = PROJECT_ROOT/"records/control_freeze.json"
    identity = _identity()
    if freeze_path.exists() and not recompute:
        cached = read_json(freeze_path)
        if cached.get("fingerprint")==identity:
            return cached
    base = read_json(PROJECT_ROOT/"configs/calibrated.json")
    baseline_rule = Rule(**cfg["baseline"])
    baselines = {}
    for ambient in cfg["design_temperatures_c"]:
        _, baseline = simulate_rule(base, baseline_rule, ambient, step_s=cfg["fit_step_s"])
        if not baseline["feasible"]:
            raise RuntimeError(f"基准策略在{ambient}C不满足共同约束：{baseline['constraints']}")
        baselines[ambient] = baseline
    grid = cfg["grid"]
    names = list(grid)
    candidates = [Rule(**dict(zip(names, values))) for values in product(*(grid[name] for name in names))]
    rows, totals = [], []
    started = time.perf_counter()
    for index, rule in enumerate(candidates):
        feasible, ratios, switches = True, [], 0
        for ambient in cfg["design_temperatures_c"]:
            try:
                _, metrics = simulate_rule(base, rule, ambient, step_s=cfg["fit_step_s"])
                accounting = compare_thermal_accounting(base, baselines[ambient], metrics)
                ratios.append(accounting["corrected_energy_kwh"]/baselines[ambient]["energy_kwh"])
                feasible = feasible and metrics["feasible"]
                switches += metrics["controller"]["cooling_switches"]
                rows.append({"candidate_id": index, **asdict(rule), "ambient_c": ambient,
                             **{k: v for k,v in metrics.items() if k not in ["rule","controller","constraints"]}, **accounting})
            except InfeasibleSimulation as error:
                feasible = False
                rows.append({"candidate_id": index, **asdict(rule), "ambient_c": ambient,
                             "feasible": False, "failure": str(error)})
        totals.append({"candidate_id": index, **asdict(rule), "feasible": feasible,
                       "design_score": float(np.mean(ratios)) if len(ratios)==len(baselines) else None,
                       "cooling_switches": switches})
        if (index+1)%12==0:
            print(f"Control design grid: {index+1}/{len(candidates)} candidates", flush=True)
    valid = [r for r in totals if r["feasible"] and r["design_score"] is not None]
    if not valid:
        raise RuntimeError("没有策略满足共同约束；不可报告不可行方案的节能率")
    best = min(valid, key=lambda r: (r["design_score"],r["cooling_switches"]))
    selected = {name: best[name] for name in names}
    pd.DataFrame(rows).to_csv(PROJECT_ROOT/"results/tables/control_design_grid.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(totals).to_csv(PROJECT_ROOT/"results/tables/control_design_candidates.csv", index=False, encoding="utf-8-sig")
    write_json(PROJECT_ROOT/"configs/optimized_rule.json", selected)
    result = {"status": "design_frozen_before_robustness_and_transfer", "fingerprint": identity,
              "selected_rule": selected, "design_score": best["design_score"],
              "candidates": len(candidates), "feasible_candidates": len(valid), "elapsed_s": time.perf_counter()-started,
              "design_temperatures_c": cfg["design_temperatures_c"], "baseline_metrics": list(baselines.values()),
              "selection": "Minimum equal-scenario heat-corrected energy ratio among feasible finite-grid rules; not a global optimum over all controls.",
              "empirical_validation": False, "parameter_model": "v2 provisional; OCV and Cr limitations retained"}
    write_json(freeze_path, result)
    return result


def evaluate_control() -> dict:
    cfg = control_protocol()
    freeze = read_json(PROJECT_ROOT/"records/control_freeze.json")
    if freeze["fingerprint"] != _identity():
        raise ValueError("控制模型或协议已变化，请重建控制设计版本")
    base = read_json(PROJECT_ROOT/"configs/calibrated.json")
    baseline_rule = Rule(**cfg["baseline"])
    selected = Rule(**freeze["selected_rule"])
    # 设计选定后才评估参考参数、扰动与新温度/新工况，不从中挑选另一条规则。
    variants = {"baseline": baseline_rule,
                "regen_only": Rule(**{**cfg["baseline"], "regen_fraction": selected.regen_fraction}),
                "thermal_only": Rule(**{**asdict(selected), "regen_fraction": baseline_rule.regen_fraction}),
                "optimized": selected}
    nominal, robustness, convergence = [], [], []
    for ambient in cfg["design_temperatures_c"]:
        baseline_metrics = None
        for label, rule in variants.items():
            trace, metrics = simulate_rule(base, rule, ambient, step_s=cfg["final_step_s"])
            if label=="baseline":
                baseline_metrics = metrics
            accounting = compare_thermal_accounting(base, baseline_metrics, metrics)
            nominal.append({"strategy": label, **metrics, **accounting})
            if label in ["baseline","optimized"]:
                trace.to_csv(PROJECT_ROOT/f"results/timeseries/control_{ambient:g}C_{label}.csv", index=False, encoding="utf-8-sig")
            if label=="optimized":
                _, fine = simulate_rule(base, rule, ambient, step_s=.25)
                check = {"ambient_c": ambient, "energy_relative_difference": abs(metrics["energy_kwh"]-fine["energy_kwh"])/abs(fine["energy_kwh"]),
                         "battery_max_difference_c": abs(metrics["battery_max_c"]-fine["battery_max_c"]),
                         "cabin_final_difference_c": abs(metrics["cabin_final_c"]-fine["cabin_final_c"])}
                check["passed"] = check["energy_relative_difference"]<.01 and check["battery_max_difference_c"]<.1 and fine["feasible"]
                convergence.append(check)
    cases = [("reference_parameters",load_parameters(),0.), ("Cr_0.008",{**base,"rolling_coefficient":.008},0.),
             ("Rref_plus25pct",{**base,"resistance_ref_ohm":base["resistance_ref_ohm"]*1.25},0.),
             ("heat_capacity_minus20pct",{**base,"battery_heat_capacity_j_k":base["battery_heat_capacity_j_k"]*.8},0.),
             ("COP_minus20pct",{**base,"cooling_cop":base["cooling_cop"]*.8},0.),
             ("sensor_temperature_plus1C",base,1.)]
    for case, parameters, bias in cases:
        for ambient in cfg["design_temperatures_c"]:
            _, b = simulate_rule(parameters, baseline_rule, ambient, step_s=.5, sensor_bias_c=bias)
            _, o = simulate_rule(parameters, selected, ambient, step_s=.5, sensor_bias_c=bias)
            robustness.append({"case": case, "cycle": "CLTC", "ambient_c": ambient,
                               "baseline_feasible": b["feasible"], "optimized_feasible": o["feasible"],
                               "baseline_constraints": b["constraints"], "optimized_constraints": o["constraints"],
                               "baseline_energy_kwh": b["energy_kwh"], "optimized_energy_kwh": o["energy_kwh"],
                               "optimized_battery_max_c": o["battery_max_c"], "optimized_cabin_min_c": o["cabin_min_c"],
                               "optimized_cabin_max_c": o["cabin_max_c"], **compare_thermal_accounting(parameters,b,o)})
        print(f"Control robustness evaluated: {case}", flush=True)
    for cycle_name in ["CLTC", "E"]:
        for ambient in cfg["transfer"]["temperatures_c"]:
            _, b = simulate_rule(base, baseline_rule, ambient, cycle_name=cycle_name)
            _, o = simulate_rule(base, selected, ambient, cycle_name=cycle_name)
            robustness.append({"case": "new_scenario_transfer", "cycle": cycle_name, "ambient_c": ambient,
                               "baseline_feasible": b["feasible"], "optimized_feasible": o["feasible"],
                               "baseline_constraints": b["constraints"], "optimized_constraints": o["constraints"],
                               "baseline_energy_kwh": b["energy_kwh"], "optimized_energy_kwh": o["energy_kwh"],
                               **compare_thermal_accounting(base,b,o)})
    flat_nominal = [{k:v for k,v in row.items() if k not in ["controller","constraints","rule"]} for row in nominal]
    pd.DataFrame(flat_nominal).to_csv(PROJECT_ROOT/"results/tables/control_nominal_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{k:v for k,v in row.items() if k not in ["baseline_constraints","optimized_constraints"]} for row in robustness]).to_csv(
        PROJECT_ROOT/"results/tables/control_robustness.csv", index=False, encoding="utf-8-sig")
    result = {"freeze_sha256": sha256(PROJECT_ROOT/"records/control_freeze.json"), "nominal": nominal,
              "robustness": robustness, "step_convergence": convergence,
              "all_nominal_feasible": all(r["feasible"] for r in nominal),
              "all_robustness_feasible": all(r["baseline_feasible"] and r["optimized_feasible"] for r in robustness),
              "all_step_checks_passed": all(r["passed"] for r in convergence),
              "limitations": ["Simulation-only control evidence. Calibrated OCV transfer, Cr, initial temperatures and unmeasured thermal dynamics remain uncertain.",
                              "Cabin starts preconditioned24C, initialSOC94%; excludes energy before trip and differs from Q1 reference initialSOC80%.",
                              "Known planned trip end is required for terminal cabin return; no future speed supplied.",
                              "Thermal correction uses an idealized COP accounting estimate; it does not replace long-duration cyclic or recovery validation.",
                              "Temperature sensor bias may violate tight terminal equality even when safety and comfort hold; report failures rather than silently relaxing the constraint."]}
    write_json(PROJECT_ROOT/"results/tables/control_evaluation.json", result)
    verify_sources()
    return result
