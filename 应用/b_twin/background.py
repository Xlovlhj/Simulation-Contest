"""Fixed synthetic feeder traffic for explicit boundary sensitivity scenarios."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际使用记录补齐并人工复核。
from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET

import pandas as pd

from .demand import fixed_vehicles, sha256
from .evaluation import CASES, WARMUP
from .paths import INPUTS


def add_background(output, root, manifest, definition, net, case, seed, total_vph):
    """Equal thirds across three approaches, beginning at evaluation start.

    Amount, split, fleet and initial state are scenario assumptions, not data.
    Core agents remain unchanged across background sensitivity hypotheses.
    """
    shutil.copyfile(output / "vehicles.csv", output / "core_vehicles.csv")
    core = pd.read_csv(output / "vehicles.csv")
    start, duration = CASES[case]
    routes = pd.DataFrame([{**r, "edges": " ".join(r["edges"])} for r in definition["feeders"]])
    rows = []
    for route in routes.itertuples():
        for s in range(0, duration, 900):
            rows.append({"route_id": route.route_id, "direction": route.direction, "begin": WARMUP + s,
                "end": WARMUP + s + 900, "estimated_vph": total_vph / len(routes), "phase": "evaluation",
                "source_window": (pd.Timestamp(start) + pd.Timedelta(seconds=s)).isoformat(),
                "evidence": "DESIGNED_BACKGROUND_SCENARIO_NOT_measured_inlet_flow"})
    segments = pd.DataFrame(rows)
    fleet = pd.read_csv(INPUTS / "vehicle_class_priors.csv")
    fleet = fleet[fleet.case.eq(case)]
    background, permissions = fixed_vehicles(routes, segments, fleet, net, seed, case)
    background.to_csv(output / "background_vehicles.csv", index=False, encoding="utf-8-sig")
    segments.to_csv(output / "background_segments.csv", index=False, encoding="utf-8-sig")
    agents = (pd.concat([core.assign(population="core"), background.assign(population="background")], ignore_index=True)
        if len(background) else core.assign(population="core"))
    agents = agents.sort_values(["depart", "vehicle_id"]).reset_index(drop=True)
    if not agents.vehicle_id.is_unique:
        raise ValueError("Background and core vehicle IDs overlap")
    agents.to_csv(output / "vehicles.csv", index=False, encoding="utf-8-sig")
    # Same type IDs/attributes as the fixed core. Insert any missing type
    # definitions before routes and departure-sorted vehicles.
    templates = {v.get("id"): v.attrib for v in ET.parse(INPUTS / f"{case}_diagnostic.rou.xml").getroot().findall("vType")}
    types = {t.get("id"): t for t in root.findall("vType")}
    for row in background.itertuples():
        tid = f"fixed_{row.base_type}_{int(round(row.speed_factor_assumed * 1000))}"
        if tid not in types:
            element = ET.Element("vType", **dict(templates[row.base_type], id=tid,
                speedFactor=str(row.speed_factor_assumed), speedDev="0"))
            root.insert(len(types), element)
            types[tid] = element
    for v in root.findall("vehicle"):
        root.remove(v)
    for route in routes.itertuples():
        ET.SubElement(root, "route", id=route.route_id, edges=route.edges)
    for row in agents.itertuples():
        ET.SubElement(root, "vehicle", id=row.vehicle_id, depart=str(row.depart), route=row.route_id,
            type=f"fixed_{row.base_type}_{int(round(row.speed_factor_assumed * 1000))}",
            departLane=row.depart_lane, departSpeed=row.depart_speed)
    scenario = {"total_background_vph": total_vph, "approaches": len(routes),
        "share_per_approach_assumed": 1 / len(routes), "background_during_warmup": False,
        "planned_background_agents": len(background), "fleet_permissions": permissions,
        "core_vehicle_manifest_sha256": sha256(output / "core_vehicles.csv"),
        "background_vehicle_manifest_sha256": sha256(output / "background_vehicles.csv"),
        "provenance": "Supply-mechanism sensitivity scenario, NOT measured or uniquely inferred background traffic",
        "limitations": ["Equal shares, 08:00/11:15 onset and north-to-south fleet transfer are assumptions.",
            "Unrepresented alternative feeder turns and traffic remain unknown.",
            "Comparisons with different background rates are mechanism diagnostics, not governance benefits."]}
    (output / "background.json").write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest.update(planned_vehicles=len(agents), planned_evaluation_vehicles=int(agents.phase.eq("evaluation").sum()),
        planned_warmup_vehicles=int(agents.phase.eq("warmup").sum()), vehicle_manifest_sha256=sha256(output / "vehicles.csv"),
        core_vehicle_manifest_sha256=scenario["core_vehicle_manifest_sha256"], background=scenario)
    manifest["limitations"].append("Explicit synthetic background scenario added; its rate/shares are not observed.")
    (output / "demand_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return root, manifest
