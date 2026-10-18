"""Audit simulated section storage with actual route exit timestamps.

Official point counts are estimated and may have different cut locations.
Lateral flows cannot be silently folded into a claimed observed queue.
"""
# AI辅助：OpenAI Codex；精确型号/发布日期不可得，提交前据真实使用记录补齐。
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import REFINED
from .evaluation import WARMUP


def ledger(events, begin, end):
    """Half-open temporal windows; every lateral entry/exit is accounted for."""
    initial = sum(e["sign"] for e in events if e["time"] < begin)
    final = sum(e["sign"] for e in events if e["time"] < end)
    selected = [e for e in events if begin <= e["time"] < end]
    counts = {kind: sum(1 for e in selected if e["kind"] == kind)
        for kind in ["upstream", "lateral_in", "downstream", "lateral_out"]}
    change = counts["upstream"] + counts["lateral_in"] - counts["downstream"] - counts["lateral_out"]
    return {"storage_begin": initial, "storage_end": final, **counts,
        "storage_change": final - initial, "balance_residual": final - initial - change}


def audit_storage(directory, output):
    output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    definition = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))
    south = definition["south_mainline"]
    upstream, downstream = "1433971795", "1095134817"
    # Cuts at external-edge exits, including connecting internal junction space.
    segment = set(south[south.index(upstream) + 1:south.index(downstream) + 1])
    events, traversals, crossings = [], [], []
    for vehicle in ET.parse(directory / "vehroute.xml").getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None or route.get("exitTimes") is None:
            continue
        edges = route.get("edges").split()
        times = [float(x) for x in route.get("exitTimes").split()]
        departures = float(vehicle.get("depart"))
        if departures < 0:
            continue
        last_inside, in_time, up_time = False, None, None
        for i, edge in enumerate(edges):
            inside = edge in segment
            previous_time = departures if i == 0 else times[i - 1] if i - 1 < len(times) else -1
            exit_time = times[i] if i < len(times) else -1
            if inside and not last_inside and previous_time >= 0:
                in_time = previous_time
                kind = "upstream" if i and edges[i - 1] == upstream else "lateral_in"
                events.append({"vehicle_id": vehicle.get("id"), "time": in_time, "sign": 1, "kind": kind})
            if inside and (i + 1 == len(edges) or edges[i + 1] not in segment) and exit_time >= 0:
                kind = "downstream" if edge == downstream else "lateral_out"
                events.append({"vehicle_id": vehicle.get("id"), "time": exit_time, "sign": -1, "kind": kind})
            if edge == upstream and exit_time >= 0:
                up_time = exit_time
            if edge == downstream and exit_time >= 0 and up_time is not None:
                traversals.append({"vehicle_id": vehicle.get("id"), "upstream_exit_seconds": up_time,
                    "downstream_exit_seconds": exit_time, "section_travel_seconds": exit_time - up_time})
            if edge in {"514553082", "1095134800"} and exit_time >= 0:
                crossings.append({"edge": edge, "exit_time": exit_time})
            last_inside = inside
            if exit_time < 0:
                break
    pd.DataFrame(events).to_csv(output / "section_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(traversals).to_csv(output / "section_travel_times.csv", index=False, encoding="utf-8-sig")
    start = pd.Timestamp(metadata["physical_start"])
    observed = pd.read_csv(REFINED / "traffic_15min_estimates.csv")
    rows, meter_rows = [], []
    for offset in range(0, metadata["evaluation_seconds"], 900):
        begin, end = WARMUP + offset, WARMUP + offset + 900
        window = (start + pd.Timedelta(seconds=offset)).isoformat()
        row = {"window": window, **ledger(events, begin, end)}
        a = observed[observed.detector_id.eq("AID01210") & observed.window.eq(window)].iloc[0]
        b = observed[observed.detector_id.eq("AID01212") & observed.window.eq(window)].iloc[0]
        eligible = bool(a.eligible and b.eligible)
        row.update(observed_pair_eligible=eligible,
            observed_upstream_estimated_count=a.flow_vph_estimated / 4 if eligible else np.nan,
            observed_downstream_estimated_count=b.flow_vph_estimated / 4 if eligible else np.nan,
            observed_difference_NOT_storage=(a.flow_vph_estimated - b.flow_vph_estimated) / 4 if eligible else np.nan)
        rows.append(row)
        for edge in ["514553082", "1095134800"]:
            n = sum(1 for c in crossings if c["edge"] == edge and begin <= c["exit_time"] < end)
            meter_rows.append({"window": window, "stop_edge": edge, "actual_simulated_crossings": n,
                "actual_simulated_rate_vph": n * 4})
    table = pd.DataFrame(rows)
    if not table.balance_residual.eq(0).all() or (table[["storage_begin", "storage_end"]] < 0).any().any():
        raise ValueError("Section storage conservation failed")
    table.to_csv(output / "section_balance_15min.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(meter_rows).to_csv(output / "meter_actual_discharge_15min.csv", index=False, encoding="utf-8-sig")
    trips = pd.DataFrame(traversals)
    if len(trips):
        complete = trips[trips.upstream_exit_seconds.ge(WARMUP) & trips.downstream_exit_seconds.lt(WARMUP + metadata["evaluation_seconds"])]
        travel = {"completed_section_traversals": len(complete), "completed_only_median_travel_s": float(complete.section_travel_seconds.median()),
            "note": "Completed traversals only; descriptive and subject to completion selection, not all-agent travel cost."}
    else:
        travel = {}
    summary = {"cuts": [upstream, downstream], "all_section_balance_residual_zero": True,
        "evaluation_upstream_entries": int(table.upstream.sum()), "evaluation_downstream_exits": int(table.downstream.sum()),
        "evaluation_lateral_entries": int(table.lateral_in.sum()), "evaluation_lateral_exits": int(table.lateral_out.sum()),
        "storage_initial": int(table.iloc[0].storage_begin), "storage_final": int(table.iloc[-1].storage_end), **travel,
        "limitations": ["Simulation section uses edge-exit cuts; official detector loops can be inside edges and are not exactly colocated.",
            "Observation difference uses expanded counts; lateral flows and travel lag are unknown, so it is NOT observed queue or storage.",
            "No Oct16 data used in this audit, and no observation speed imposed."]}
    (output / "section_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
