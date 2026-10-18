"""Export evidence and unresolved boundaries without inventing supply/queues."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际记录补齐并人工复核。
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .boundary import MODEL
from .paths import REFINED
import sumolib


def inspect_supply(output):
    definition = json.loads((MODEL / "definition.json").read_text(encoding="utf-8"))
    net = sumolib.net.readNet(str(MODEL / "corridor.net.xml"))
    extension = definition["extension_edges"]
    existing_routes = pd.read_csv(REFINED / "all_counts/peak_inferred_routes.csv")
    served_edges = set(" ".join(existing_routes.edges).split()) | set(extension)
    rows = []
    for i, eid in enumerate(extension):
        e = net.getEdge(eid)
        for inlet in e.getIncoming():
            if i and inlet.getID() == extension[i - 1]:
                continue
            if inlet.getID() in served_edges:
                continue
            rows.append({"merge_edge": eid, "unmeasured_inlet_edge": inlet.getID(),
                "name": inlet.getName(), "road_type": inlet.getType(), "lanes": inlet.getLaneNumber(),
                "length_m": inlet.getLength(), "model_inflow_vph": 0,
                "evidence": "No candidate route enters here; zero is a model omission, NOT an observed zero"})
    pd.DataFrame(rows).to_csv(output / "unmeasured_downstream_inlets.csv", index=False, encoding="utf-8-sig")
    lane_changes = []
    for before, after in zip(extension, extension[1:]):
        a, b = net.getEdge(before), net.getEdge(after)
        if a.getLaneNumber() > b.getLaneNumber():
            lane_changes.append({"from_edge": before, "to_edge": after,
                "from_lanes": a.getLaneNumber(), "to_lanes": b.getLaneNumber(),
                "evidence": "OSM-derived model topology; real usable capacity NOT observed"})
    pd.DataFrame(lane_changes).to_csv(output / "downstream_lane_reductions.csv", index=False, encoding="utf-8-sig")
    observed = pd.read_csv(REFINED / "traffic_15min_estimates.csv")
    selected = observed[observed.detector_id.isin(["AID01210", "AID01212"])
        & observed.window.ge("2026-10-15T07:30:00") & observed.window.lt("2026-10-15T09:00:00")].copy()
    selected["density_proxy_veh_per_km_all_lanes"] = selected.flow_vph_estimated / selected.speed_kmh_observed.replace(0, np.nan)
    selected["proxy_note"] = "q/v only a density proxy: reported time-mean point speed is not space-mean speed; occupancy is not queue length"
    selected.to_csv(output / "peak_supply_evidence.csv", index=False, encoding="utf-8-sig")
    q = selected.pivot(index="window", columns="detector_id", values="flow_vph_estimated")
    valid = selected.pivot(index="window", columns="detector_id", values="eligible")
    balance = pd.DataFrame({"window": q.index,
        "upstream_minus_downstream_vph": (q.AID01210 - q.AID01212).to_numpy(),
        "both_eligible": (valid.AID01210 & valid.AID01212).to_numpy()})
    balance["interpretation"] = "Qup-Qdown = net side exits + storage change (plus sampling/lag error); not an identified turning flow"
    balance.to_csv(output / "flow_balance_ambiguity.csv", index=False, encoding="utf-8-sig")
    facts = {"unmeasured_downstream_inlets": rows, "downstream_lane_reductions": lane_changes,
        "initial_queue_evidence": "At 07:45 reliable core points 01210/01212 have sample speeds about 59.8/59.2 km/h. Initial queue count is unavailable; do not invent it from occupancy. Farther-downstream mapping remains flagged.",
        "capacity_identified": False, "observed_initial_queue_available": False,
        "next_step": "Resolve lane/turn topology and downstream mapping first; formulate residual-supply scenarios with explicit assumptions, fixed paired demand and all-vehicle cost. Do not clamp speeds and validate against the same observations.",
        "core_limitation": "Congested internal throughput is not unconstrained external demand. Static route incidence can explain a lower count by exits instead of accumulation; count fitting cannot identify the true queue mechanism."}
    (output / "supply_audit.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return facts
