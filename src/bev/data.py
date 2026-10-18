"""原始数据审计；不以测试集选择模型。AI辅助信息见records/ai_usage.md。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.integrate import trapezoid

from .io import PROJECT_ROOT, attachment, read_json, verify_sources, write_json

TRIP_COLUMNS = ["vehicle_id", "trip_id", "time_ms", "speed_kmh", "ambient_c", "ac_w", "heater_w",
                "raw_current_a", "soc_pct", "voltage_v"]


def read_trips() -> pd.DataFrame:
    frame = pd.read_excel(attachment(2))
    if frame.shape[1] != len(TRIP_COLUMNS):
        raise ValueError("附件2字段数量改变，请重新核对数据字典")
    frame.columns = TRIP_COLUMNS
    for column in frame:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame.isna().any().any():
        raise ValueError("附件2存在缺失：需明确处理，不能默默填充")
    frame["time_s"] = frame.time_ms / 1000
    frame["speed_m_s"] = frame.speed_kmh / 3.6
    frame["discharge_current_a"] = -frame.raw_current_a
    frame["soc"] = frame.soc_pct / 100
    frame["battery_power_w"] = frame.discharge_current_a * frame.voltage_v
    return frame


def read_reference_cycle() -> pd.DataFrame:
    frame = pd.read_excel(attachment(3))
    if frame.shape[1] != 2:
        raise ValueError("附件3格式变化")
    frame.columns = ["time_s", "speed_kmh"]
    frame = frame.apply(pd.to_numeric, errors="raise")
    if frame.isna().any().any() or (np.diff(frame.time_s) <= 0).any() or (frame.speed_kmh < 0).any():
        raise ValueError("参考工况缺失、时间非单调或速度为负")
    return pd.DataFrame({"time_s": frame.time_s, "speed_m_s": frame.speed_kmh / 3.6,
                         "ambient_c": 25.0})


def extract_curves() -> dict:
    frame = pd.read_excel(attachment(4))
    category = frame.iloc[:, 0].astype(str)
    ocv = frame[category.eq("OCV-SOC(单体电芯)")]
    soc_r = frame[category.eq("R0的SOC修正系数")]
    return {"source": attachment(4).name, "series_cells": 96,
            "ocv_soc": ocv.iloc[:, 1].astype(float).tolist(),
            "ocv_cell_v": ocv.iloc[:, 4].astype(float).tolist(),
            "resistance_soc": soc_r.iloc[:, 1].astype(float).tolist(),
            "resistance_soc_factor": soc_r.iloc[:, 4].astype(float).tolist(),
            "resistance_temp_slope_per_c": 0.020, "valid_temperature_c": [-20.0, 55.0],
            "entropy_pack_v_k": 0.0144,
            "motor_loss": {"constant_w": 300.0, "torque_coefficient": 0.222, "speed_coefficient": 0.004},
            "resistance_soc_policy": "Normalize at SOC=0.5; endpoint hold outside tabulated SOC=0.1..0.9, flagged in results"}


def audit_data() -> dict:
    manifest = verify_sources()
    processed = PROJECT_ROOT / "data/processed"
    tables = PROJECT_ROOT / "results/tables"
    processed.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    frame = read_trips()
    split = read_json(PROJECT_ROOT / "configs/split.json")
    rows = []
    for (vehicle_id, trip_id), group in frame.groupby(["vehicle_id", "trip_id"], sort=False):
        times = group.time_s.to_numpy()
        delta = np.diff(times)
        if (delta <= 0).any():
            raise ValueError(f"路程{trip_id}时间不单调，不允许静默排序或去重")
        energy = trapezoid(group.battery_power_w, times) / 3.6e6
        charge = trapezoid(group.discharge_current_a, times) / 3600
        soc_drop = float(group.soc.iloc[0] - group.soc.iloc[-1])
        role = next((role for role in ("train", "validation", "test") if int(trip_id) in split[f"{role}_trip_ids"]), "unassigned")
        if role == "unassigned":
            raise ValueError(f"路程{trip_id}未预先分组")
        rows.append({"vehicle_id": int(vehicle_id), "trip_id": int(trip_id), "role": role,
                     "records": len(group), "duration_s": float(times[-1] - times[0]),
                     "distance_km": float(trapezoid(group.speed_m_s, times) / 1000),
                     "dt_min_s": float(delta.min()), "dt_max_s": float(delta.max()),
                     "gap_over_5s_count": int((delta > 5).sum()), "gap_over_5s_total_s": float(delta[delta > 5].sum()),
                     "max_speed_kmh": float(group.speed_kmh.max()),
                     "ambient_min_c": float(group.ambient_c.min()), "ambient_max_c": float(group.ambient_c.max()),
                     "soc_start_pct": float(group.soc_pct.iloc[0]), "soc_end_pct": float(group.soc_pct.iloc[-1]),
                     "net_discharge_ah": float(charge), "net_energy_kwh": float(energy),
                     "diagnostic_capacity_ah": float(charge / soc_drop) if soc_drop else None})
        group.to_csv(processed / f"trip_{int(trip_id)}.csv", index=False, encoding="utf-8-sig")
    summary = pd.DataFrame(rows)
    summary.to_csv(tables / "data_audit_trips.csv", index=False, encoding="utf-8-sig")
    reference = read_reference_cycle()
    reference.to_csv(processed / "reference_cycle_E.csv", index=False, encoding="utf-8-sig")
    parameter_table = pd.read_excel(attachment(1))
    parameter_table.to_csv(tables / "attachment1_parameters.csv", index=False, encoding="utf-8-sig")
    components = pd.read_excel(attachment(4))
    components.to_csv(tables / "attachment4_components.csv", index=False, encoding="utf-8-sig")
    curves = extract_curves()
    write_json(processed / "component_curves.json", curves)
    audit = {"status": "quality_audit_only_no_calibration", "records": len(frame), "trips": len(summary),
             "raw_current_to_discharge_multiplier": -1, "missing_numeric_cells": int(frame.isna().sum().sum()),
             "reference_cycle": {"scenario": "E", "is_verified_cltc": False,
                                 "duration_s": float(reference.time_s.iloc[-1] - reference.time_s.iloc[0]),
                                 "records": len(reference), "max_speed_kmh": float(reference.speed_m_s.max() * 3.6),
                                 "distance_km": float(trapezoid(reference.speed_m_s, reference.time_s) / 1000)},
             "capacity_conflict": {"stated_kwh": 30.0, "capacity_ah": 160.0, "nominal_voltage_v": 375.0,
                                   "product_kwh": 60.0},
             "thermal_observations_present": False, "manifest_file_count": len(manifest), "trip_summary": rows,
             "limitations": ["Current/SOC capacity is diagnostic, not identified true capacity.",
                             "Trapezoidal integration bridges observed gaps; long gaps are quantified, not repaired.",
                             "Reference cycle must not be labeled CLTC before verification."]}
    write_json(tables / "data_audit.json", audit)
    return audit
