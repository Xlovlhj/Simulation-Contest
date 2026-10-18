"""隔离的一阶RC候选：复制冻结model.py的车辆/热方程，只扩展电池支路。

原Rint模型不覆盖。RC分支按子步恒流精确积分，分别核算电阻热与电容储能。
AI辅助：Codex；使用边界与实际运行见阶段十协议。
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from .io import PROJECT_ROOT, read_json
from .model import InitialState, InfeasibleSimulation, battery_current, motor_flow


def load_parameters() -> dict:
    return read_json(PROJECT_ROOT / "configs/reference.json")


def load_curves() -> dict:
    return read_json(PROJECT_ROOT / "data/processed/component_curves.json")


def simulate(cycle: pd.DataFrame, parameters: dict | None = None, initial_state: InitialState | None = None,
             *, max_step_s: float = 0.5, scenario: str = "E_reference_unverified_CLTC",
             curves: dict | None = None, verified_cltc: bool = False,
             controller: Callable[[dict], dict] | None = None) -> tuple[pd.DataFrame, dict]:
    """分段线性车速，子步中点动力功率、显式状态积分；全程记录收支。

    cycle可提供实测ac_w/heater_w作为已知负载；没有时用声明的规则控制。
    不读取真实SOC、电流或电压作为逐时预测输入。
    """
    p = dict(load_parameters() if parameters is None else parameters)
    c = load_curves() if curves is None else curves
    x = initial_state or InitialState()
    if not 0 <= x.soc <= 1 or max_step_s <= 0 or p["capacity_ah"] <= 0 or p["cooling_cop"] <= 0:
        raise ValueError("初始SOC、步长、容量或COP无效")
    t = cycle.time_s.to_numpy(dtype=float)
    v = cycle.speed_m_s.to_numpy(dtype=float)
    ambient = cycle.ambient_c.to_numpy(dtype=float)
    measured_ac = cycle.ac_w.to_numpy(dtype=float) if "ac_w" in cycle else None
    measured_heater = cycle.heater_w.to_numpy(dtype=float) if "heater_w" in cycle else None
    if controller is not None and (measured_ac is not None or measured_heater is not None):
        raise ValueError("控制试验与已知实测附件负载模式不能同时启用")
    if len(t) < 2 or not np.isfinite(np.column_stack([t, v, ambient])).all() or (np.diff(t) <= 0).any() or (v < 0).any():
        raise ValueError("工况必须含至少两个有限且严格递增时间点与非负速度")
    soc, tb, tc = x.soc, x.battery_c, x.cabin_c
    polarization = float(p.get('polarization_initial_v', 0.))
    rp, tau = float(p['polarization_resistance_ohm']), float(p['polarization_tau_s'])
    if rp <= 0 or tau <= 0:
        raise ValueError('RC支路电阻和时间常数必须为正')
    cooler_on = False
    records = []
    kappa_ref = float(np.interp(0.5, c["resistance_soc"], c["resistance_soc_factor"]))
    for j, interval in enumerate(np.diff(t)):
        count = int(math.ceil(interval / max_step_s))
        dt = interval / count
        acceleration = (v[j+1] - v[j]) / interval
        for n in range(count):
            fraction = (n + 0.5) / count
            now = t[j] + n * dt
            vmid = v[j] + fraction * (v[j+1] - v[j])
            ta = ambient[j] + fraction * (ambient[j+1] - ambient[j])
            if not 0 <= soc <= 1:
                raise InfeasibleSimulation(f"t={now:.2f}s：SOC超出闭区间[0,1]")
            if not c["valid_temperature_c"][0] <= tb <= c["valid_temperature_c"][1]:
                raise InfeasibleSimulation(f"t={now:.2f}s：电池温度超出内阻参考关系适用范围")
            soc_factor = float(np.interp(soc, c["resistance_soc"], c["resistance_soc_factor"])) / kappa_ref
            resistance = p["resistance_ref_ohm"] * soc_factor * (1 + c["resistance_temp_slope_per_c"] * (25-tb))
            ocv = float(np.interp(soc, c["ocv_soc"], c["ocv_cell_v"])) * c["series_cells"] + p.get("ocv_offset_v", 0.0)
            rolling = p["mass_kg"] * p["gravity_m_s2"] * p["rolling_coefficient"] if vmid > 0 else 0.0
            aerodynamic = 0.5 * p["air_density_kg_m3"] * p["drag_coefficient"] * p["frontal_area_m2"] * vmid**2
            inertia = p["rotating_mass_factor"] * p["mass_kg"] * acceleration
            wheel = (rolling + aerodynamic + inertia) * vmid
            action = {} if controller is None else controller(
                {"time_s": float(now), "remaining_s": float(t[-1]-now), "dt_s": dt,
                 "soc": soc, "battery_c": tb, "cabin_c": tc, "ambient_c": float(ta),
                 "speed_m_s": float(vmid), "acceleration_m_s2": float(acceleration), "wheel_w": float(wheel)})
            requested_regen = float(action.get("regen_fraction", p["regen_fraction"]))
            if not 0 <= requested_regen <= 1:
                raise InfeasibleSimulation("回收强度必须在[0,1]内")
            temp_regen = float(np.clip((tb-p["regen_temperature_zero_c"]) /
                                      (p["regen_temperature_full_c"]-p["regen_temperature_zero_c"]), 0, 1))
            regen = requested_regen * temp_regen if soc < p["regen_soc_cutoff"] else 0.0
            flow = motor_flow(wheel, vmid, acceleration, p, c, regen)
            if tb >= p["cooling_on_c"]:
                cooler_on = True
            elif tb <= p["cooling_off_c"]:
                cooler_on = False
            if "cooler_on" in action:
                if not isinstance(action["cooler_on"], (bool, np.bool_)):
                    raise InfeasibleSimulation("冷却启停指令必须是布尔值")
                cooler_on = bool(action["cooler_on"])
            cooling_heat = p["cooler_conductance_w_k"] * max(tb-p["coolant_temperature_c"], 0) if cooler_on else 0.0
            cooling_power = cooling_heat / p["cooling_cop"]
            ac_power = min(p["ac_max_w"], p["cabin_gain_w_k"] * max(tc-p["cabin_target_c"]-p["cabin_deadband_c"], 0))
            heater_power = min(p["heater_max_w"], p["cabin_gain_w_k"] * max(p["cabin_target_c"]-p["cabin_deadband_c"]-tc, 0))
            if measured_ac is not None:
                ac_power = float(measured_ac[j] + fraction * (measured_ac[j+1] - measured_ac[j]))
            if measured_heater is not None:
                heater_power = float(measured_heater[j] + fraction * (measured_heater[j+1] - measured_heater[j]))
            ac_power = float(action.get("ac_w", ac_power))
            heater_power = float(action.get("heater_w", heater_power))
            if not 0 <= ac_power <= p["ac_max_w"] or not 0 <= heater_power <= p["heater_max_w"]:
                raise InfeasibleSimulation("空调/加热功率超附件额定范围")
            if ac_power+cooling_power > p.get("compressor_total_max_w", math.inf)+1e-8:
                raise InfeasibleSimulation("座舱与电池冷却合计超出声明的共用制冷功率预算")
            power = flow["motor_dc_w"] + p["low_voltage_w"] + ac_power + heater_power + cooling_power
            rc = rc_step(power, ocv, resistance, rp, tau, polarization, dt)
            current, voltage = rc['current_a'], rc['voltage_v']
            joule = rc['heat_w']
            reversible = -current * (tb+273.15) * c["entropy_pack_v_k"] if p["include_entropy_heat"] else 0.0
            battery_environment = p["battery_environment_w_k"] * (tb-ta)
            battery_net_heat = joule + reversible - battery_environment - cooling_heat
            cabin_environment = p["cabin_environment_w_k"] * (ta-tc)
            cabin_net_heat = cabin_environment + p["solar_heat_w"] + p["occupant_heat_w"] + p["heater_efficiency"]*heater_power - p["cooling_cop"]*ac_power
            next_soc = soc-current*dt/(3600*p["capacity_ah"])
            next_tb = tb+battery_net_heat*dt/p["battery_heat_capacity_j_k"]
            next_tc = tc+cabin_net_heat*dt/p["cabin_heat_capacity_j_k"]
            if not 0 <= next_soc <= 1:
                raise InfeasibleSimulation(f"t={now+dt:.2f}s：下一状态SOC越界")
            if not c["valid_temperature_c"][0] <= next_tb <= c["valid_temperature_c"][1]:
                raise InfeasibleSimulation(f"t={now+dt:.2f}s：下一电池温度超出参考关系适用范围")
            motor_residual = (flow["motor_dc_w"] - wheel - flow["gear_loss_w"] - flow["motor_loss_w"]
                              - flow["inverter_loss_w"] - flow["friction_brake_w"])
            records.append({"time_start_s": now, "time_s": now+dt, "dt_s": dt, "speed_m_s": vmid,
                            "acceleration_m_s2": acceleration, "ambient_c": ta, "soc_start": soc, "soc": next_soc,
                            "battery_start_c": tb, "battery_c": next_tb, "cabin_start_c": tc, "cabin_c": next_tc,
                            "resistance_ohm": resistance, "ocv_v": ocv, "voltage_v": voltage, "current_a": current,
                            "polarization_start_v": polarization, "polarization_v": rc['next_v'],
                            "polarization_heat_w": rc['polarization_heat_w'],
                            "polarization_storage_w": rc['storage_w'],
                            "battery_w": power, "ocv_electric_w": ocv*current, "low_voltage_w": p["low_voltage_w"], "ac_w": ac_power,
                            "heater_w": heater_power, "cooler_w": cooling_power, "cooler_on": int(cooler_on),
                            "regen_fraction_requested": requested_regen, "regen_fraction_effective": regen,
                            "cooling_heat_w": cooling_heat, "joule_heat_w": joule, "entropy_heat_w": reversible,
                            "battery_environment_w": battery_environment, "cabin_environment_w": cabin_environment,
                            "battery_net_heat_w": battery_net_heat, "cabin_net_heat_w": cabin_net_heat,
                            "rolling_w": rolling*vmid, "aerodynamic_w": aerodynamic*vmid, "inertia_w": inertia*vmid,
                            "motor_balance_residual_w": motor_residual,
                            "battery_electrical_residual_w": ocv*current-power-joule-rc['storage_w'],
                            "battery_thermal_residual_w": p["battery_heat_capacity_j_k"]*(next_tb-tb)/dt-battery_net_heat,
                            "cabin_thermal_residual_w": p["cabin_heat_capacity_j_k"]*(next_tc-tc)/dt-cabin_net_heat,
                            "resistance_soc_endpoint_hold": int(soc < c["resistance_soc"][0] or soc > c["resistance_soc"][-1]),
                            **flow})
            soc, tb, tc = next_soc, next_tb, next_tc
            polarization = rc['next_v']
    trace = pd.DataFrame.from_records(records)
    dt_array = trace.dt_s.to_numpy()

    def energy(name: str) -> float:
        return float(np.sum(trace[name].to_numpy()*dt_array)/3.6e6)

    distance = float(np.sum(trace.speed_m_s.to_numpy()*dt_array)/1000)
    electric = {name: energy(name) for name in ["battery_w", "ocv_electric_w", "motor_dc_w", "low_voltage_w", "ac_w", "heater_w", "cooler_w",
                                               "gear_loss_w", "motor_loss_w", "inverter_loss_w", "friction_brake_w", "wheel_w", "joule_heat_w"]}
    discharge = float(np.sum(np.maximum(trace.battery_w, 0)*dt_array)/3.6e6)
    charged = float(np.sum(np.maximum(-trace.battery_w, 0)*dt_array)/3.6e6)
    tractive_dc = float(np.sum(np.maximum(trace.motor_dc_w, 0)*dt_array)/3.6e6)
    recovered_dc = float(np.sum(np.maximum(-trace.motor_dc_w, 0)*dt_array)/3.6e6)
    dc_residual = electric["battery_w"]-tractive_dc+recovered_dc-sum(electric[key] for key in ["low_voltage_w", "ac_w", "heater_w", "cooler_w"])
    summary = {"model_version": "stage10_one_RC", "parameter_status": p.get("parameter_status", "uncalibrated_reference"),
               "scenario": scenario, "is_verified_cltc": verified_cltc,
               "duration_s": float(t[-1]-t[0]), "steps": len(trace), "max_step_s": max_step_s,
               "distance_km": distance, "net_energy_kwh": electric["battery_w"],
               "discharge_energy_kwh": discharge, "charged_energy_kwh": charged,
               "ocv_electric_energy_kwh": electric["ocv_electric_w"],
               "traction_dc_energy_kwh": tractive_dc, "regen_dc_energy_kwh": recovered_dc,
               "consumption_kwh_100km": 100*electric["battery_w"]/distance if distance else None,
               "soc_initial": x.soc, "soc_final": soc, "battery_initial_c": x.battery_c,
               "battery_final_c": tb, "battery_max_c": max(x.battery_c, float(trace.battery_c.max())),
               "cabin_initial_c": x.cabin_c, "cabin_final_c": tc,
               "cabin_max_c": max(x.cabin_c, float(trace.cabin_c.max())),
               "energy_kwh": electric, "dc_balance_residual_kwh": dc_residual,
               "max_motor_balance_residual_w": float(trace.motor_balance_residual_w.abs().max()),
               "max_battery_electrical_residual_w": float(trace.battery_electrical_residual_w.abs().max()),
               "max_battery_thermal_residual_w": float(trace.battery_thermal_residual_w.abs().max()),
               "max_cabin_thermal_residual_w": float(trace.cabin_thermal_residual_w.abs().max()),
               "resistance_soc_endpoint_hold_steps": int(trace.resistance_soc_endpoint_hold.sum()),
               "capacity_ah": p["capacity_ah"], "reference_nominal_product_kwh": p["capacity_ah"]*p["nominal_voltage_v"]/1000,
               "ocv_offset_v": p.get("ocv_offset_v", 0.0),
               "entropy_heat_included": p["include_entropy_heat"],
               "limitations": ["No measured battery/cabin temperatures; thermal output is simulation only.",
                               "Declared control assumptions and stationary motor-off model.",
                               "No certified pack current rating supplied; only Rint, SOC, motor limits and declared regen cap checked."]}
    return trace, summary


def rc_step(power, ocv, r0, rp, tau, vp, dt):
    """恒流子步内的精确RC积分；端口功率按该步平均电压闭合。"""
    if dt <= 0 or rp <= 0 or tau <= 0 or r0 < 0:
        raise ValueError('无效RC参数或步长')
    h = dt/tau
    decay = math.exp(-h)
    a = -math.expm1(-h)/h
    current, voltage = battery_current(power, ocv-a*vp, r0+rp*(1-a))
    steady = rp*current
    delta = vp-steady
    next_v = steady+delta*decay
    avg_square = steady**2+2*steady*delta*a+delta**2*(-math.expm1(-2*h)/(2*h))
    polarization_heat = max(0., avg_square/rp)  # 仅浮点抵消；解析量为平方积分
    storage = .5*(tau/rp)*(next_v**2-vp**2)/dt
    heat = current**2*r0+polarization_heat
    return {'current_a':current, 'voltage_v':voltage, 'next_v':next_v,
            'polarization_heat_w':polarization_heat, 'storage_w':storage, 'heat_w':heat}
