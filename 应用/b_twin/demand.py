"""Historical count inversion and reproducible synthetic vehicle manifests.

AI-assisted: OpenAI Codex; exact model/version and release date unavailable.
Record actual usage and human review before competition submission.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from .paths import INPUTS, REFINED
from .evaluation import CASES, WARMUP
import sumolib

VERSION = "fixed_agents_v1"
RIDGE = .2
TEMPORAL_RIDGE = .15


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invert_quarter(routes, observations, mapping, window, prior, previous=None, entry_ridge=0):
    """Fit only eligible, unflagged station counts; absence is never zero.

    A route incidence fit ignores storage/travel-time lag. It is an initial
    nonunique departure estimate, not observed OD or simulated validation.
    """
    selected = observations[observations.window.eq(window)].merge(
        mapping[["detector_id", "sumo_edge", "requires_review"]], on="detector_id", validate="one_to_one")
    selected["constraint_used"] = (selected.eligible.astype(bool)
        & ~selected.requires_review.astype(bool) & selected.flow_vph_estimated.notna())
    train = selected[selected.constraint_used]
    sets = [set(edges.split()) for edges in routes.edges]
    a = np.array([[float(edge in route) for route in sets] for edge in train.sumo_edge], dtype=float)
    a = a.reshape(len(train), len(routes))
    b = train.flow_vph_estimated.to_numpy(dtype=float)
    sd = ((train.conditional_flow_ci95_high - train.conditional_flow_ci95_low) / 3.92).to_numpy()
    weights = 500 / np.maximum(100, np.nan_to_num(sd, nan=500))
    matrices = [a * weights[:, None], RIDGE * np.eye(len(routes))]
    targets = [b * weights, RIDGE * prior]
    if previous is not None:
        matrices.append(TEMPORAL_RIDGE * np.eye(len(routes)))
        targets.append(TEMPORAL_RIDGE * previous)
    if entry_ridge:
        # Sensitivity assumption: favor fewer unsupported external injections.
        # This is a penalty, not evidence that minor-road demand is truly zero.
        origins = routes.edges.str.split().str[0]
        sources = sorted(set(origins[~routes.source_mainline.astype(bool)]))
        source_matrix = np.array([origins.eq(source).to_numpy(dtype=float) for source in sources])
        matrices.append(entry_ridge * source_matrix)
        targets.append(np.zeros(len(sources)))
    if len(train):
        result = lsq_linear(np.vstack(matrices), np.concatenate(targets), bounds=(0, np.inf),
                            tol=1e-9, max_iter=300)
        if not result.success:
            raise RuntimeError(f"Nonnegative demand inversion failed: {window}")
        rates = result.x
    else:
        # With no count evidence, keep the stated mean prior exactly.
        rates = np.array(prior, dtype=float, copy=True)
    selected["static_predicted_flow_vph"] = [
        sum(q for q, route in zip(rates, sets) if edge in route) for edge in selected.sumo_edge]
    selected["constraint_reason"] = np.select(
        [selected.requires_review.astype(bool), ~selected.eligible.astype(bool)],
        ["mapping_requires_review_EXCLUDED", "missing_or_low_coverage_EXCLUDED"], default="eligible_count_constraint")
    info = {"window": window, "constraints": len(train),
        "rank": int(np.linalg.matrix_rank(a)) if len(train) else 0, "routes": len(routes),
        "static_fit_wmape_pct": float(100 * abs(a @ rates - b).sum() / b.sum()) if b.sum() else None,
        "inferred_boundary_vph": float(rates.sum()),
        "evidence": "eligible_counts_plus_regularized_prior" if len(train) else "prior_only_NO_count_evidence"}
    return rates, selected, info


def estimate_segments(case, mode, entry_ridge=0):
    routes = pd.read_csv(INPUTS / f"{case}_inferred_routes.csv")
    observations = pd.read_csv(REFINED / "traffic_15min_estimates.csv")
    mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    core = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))["core_edges"]
    mapping = mapping[mapping.sumo_edge.isin(core)]
    start, duration = CASES[case]
    t0 = pd.Timestamp(start)
    windows = [t0 - pd.Timedelta(minutes=15)] + [t0 + pd.Timedelta(seconds=s) for s in range(0, duration, 900)]
    prior = routes.estimated_vph.to_numpy()
    segments, constraints, fits = [], [], []
    previous = None
    for j, window in enumerate(windows):
        rates, counts, info = invert_quarter(routes, observations, mapping, window.isoformat(), prior, previous, entry_ridge)
        if mode == "fixedmean":
            rates = prior.copy()
            info["evidence"] = "legacy_window_mean_route_prior_NOT_quarter_fit"
            info["inferred_boundary_vph"] = float(rates.sum())
            counts["static_predicted_flow_vph"] = [sum(q for q, edges in zip(rates, routes.edges)
                if edge in edges.split()) for edge in counts.sumo_edge]
            used = counts[counts.constraint_used]
            info["static_fit_wmape_pct"] = float(100 * abs(used.static_predicted_flow_vph
                - used.flow_vph_estimated).sum() / used.flow_vph_estimated.sum()) if used.flow_vph_estimated.sum() else None
        previous = rates
        begin = 0 if j == 0 else WARMUP + (j - 1) * 900
        end = WARMUP if j == 0 else begin + 900
        for route, rate in zip(routes.itertuples(), rates):
            segments.append({"route_id": route.route_id, "direction": route.direction,
                "source_window": window.isoformat(), "begin": begin, "end": end,
                "phase": "warmup" if j == 0 else "evaluation", "estimated_vph": float(rate),
                "evidence": info["evidence"]})
        counts["phase"] = "warmup" if j == 0 else "evaluation"
        constraints.append(counts)
        fits.append(info)
    return routes, pd.DataFrame(segments), pd.concat(constraints, ignore_index=True), fits


def fixed_vehicles(routes, segments, fleet, net, seed, case):
    """Stratified departures, cumulative rounding, fixed types/speed factors.

    For each route, total count differs from the continuous expected count by
    less than one. Randomness is sampled here, before SUMO/control execution.
    The seed does not depend on network version or future control policy.
    """
    key = int.from_bytes(hashlib.sha256(f"{VERSION}/{case}/{seed}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(key)
    rows, permissions = [], []
    for route in routes.itertuples():
        edges = route.edges.split()
        mix = fleet[fleet.direction.eq(route.direction)].copy()
        usable = mix[mix.sumo_vclass.map(lambda c: all(net.getEdge(e).allows(c) for e in edges))]
        probabilities = usable.proportion.to_numpy(dtype=float)
        removed = 1 - probabilities.sum()
        permissions.append({"route_id": route.route_id, "renormalized_fraction": float(max(0, removed))})
        if probabilities.sum() <= 0:
            raise ValueError("No vehicle class allowed on " + route.route_id)
        probabilities /= probabilities.sum()
        balance = float(rng.random())
        sequence = 0
        for segment in segments[segments.route_id.eq(route.route_id)].itertuples():
            expected = segment.estimated_vph * (segment.end - segment.begin) / 3600
            count = int(np.floor(balance + expected))
            balance += expected - count
            if not count:
                continue
            departures = segment.begin + (np.arange(count) + rng.random(count)) * (segment.end - segment.begin) / count
            types = rng.choice(len(usable), size=count, p=probabilities)
            speed_factors = np.round(np.clip(rng.normal(1, .1, count), .7, 1.3), 3)
            for depart, cls, factor in zip(departures, types, speed_factors):
                vehicle = usable.iloc[int(cls)]
                rows.append({"vehicle_id": f"{case}_{route.route_id}_{sequence:05d}",
                    "depart": min(round(float(depart), 3), segment.end - .001), "phase": segment.phase,
                    "route_id": route.route_id, "origin_edge": edges[0], "original_destination_edge": edges[-1],
                    "base_type": vehicle.vtype_id, "sumo_vclass": vehicle.sumo_vclass,
                    "length_m_assumed": vehicle.length_m_assumed, "speed_factor_assumed": float(factor),
                    "sigma_assumed": .5, "depart_lane": "best", "depart_speed": "max",
                    "source_window": segment.source_window, "fleet_provenance": vehicle.provenance,
                    "provenance": "fixed_synthetic_agent_NOT_observed_identity_or_trajectory"})
                sequence += 1
    columns = ["vehicle_id", "depart", "phase", "route_id", "origin_edge", "original_destination_edge",
        "base_type", "sumo_vclass", "length_m_assumed", "speed_factor_assumed", "sigma_assumed",
        "depart_lane", "depart_speed", "source_window", "fleet_provenance", "provenance"]
    vehicles = pd.DataFrame(rows, columns=columns).sort_values(["depart", "vehicle_id"]).reset_index(drop=True)
    return vehicles, permissions


def build_fixed_demand(output, case, mode, seed, entry_ridge=0):
    routes, segments, constraints, fits = estimate_segments(case, mode, entry_ridge)
    fleet = pd.read_csv(INPUTS / "vehicle_class_priors.csv")
    fleet = fleet[fleet.case.eq(case)]
    # Draw against the same original route permissions for both network versions.
    original = sumolib.net.readNet(str(REFINED / "corridor.net.xml"))
    vehicles, permissions = fixed_vehicles(routes, segments, fleet, original, seed, case)
    vehicles.to_csv(output / "vehicles.csv", index=False, encoding="utf-8-sig")
    segments.to_csv(output / "demand_segments.csv", index=False, encoding="utf-8-sig")
    constraints.to_csv(output / "demand_constraints.csv", index=False, encoding="utf-8-sig")
    root = ET.Element("routes")
    templates = {v.get("id"): v.attrib for v in ET.parse(INPUTS / f"{case}_diagnostic.rou.xml").getroot().findall("vType")}
    type_ids = {}
    for row in vehicles.itertuples():
        key = (row.base_type, row.speed_factor_assumed)
        if key not in type_ids:
            tid = f"fixed_{row.base_type}_{int(round(row.speed_factor_assumed * 1000))}"
            attrs = dict(templates[row.base_type], id=tid, speedFactor=str(row.speed_factor_assumed), speedDev="0")
            ET.SubElement(root, "vType", **attrs)
            type_ids[key] = tid
    for row in routes.itertuples():
        ET.SubElement(root, "route", id=row.route_id, edges=row.edges)
    for row in vehicles.itertuples():
        ET.SubElement(root, "vehicle", id=row.vehicle_id, depart=str(row.depart), route=row.route_id,
            type=type_ids[(row.base_type, row.speed_factor_assumed)], departLane=row.depart_lane, departSpeed=row.depart_speed)
    manifest = {"version": VERSION, "mode": mode, "seed": seed, "case": case,
        "vehicle_manifest_sha256": sha256(output / "vehicles.csv"), "planned_vehicles": len(vehicles),
        "planned_evaluation_vehicles": int(vehicles.phase.eq("evaluation").sum()),
        "planned_warmup_vehicles": int(vehicles.phase.eq("warmup").sum()),
        "source_sha256": {p.name: sha256(p) for p in [INPUTS / f"{case}_inferred_routes.csv",
            INPUTS / "vehicle_class_priors.csv", REFINED / "traffic_15min_estimates.csv", REFINED / "detector_mapping.csv"]},
        "regularization": {"mean_prior_ridge": RIDGE, "previous_quarter_ridge": TEMPORAL_RIDGE,
            "unobserved_entry_sum_ridge": entry_ridge},
        "quarter_fits": fits, "fleet_permissions": permissions,
        "limitations": ["Historical inverse count fit is nonunique; travel-time lag and storage are not resolved.",
            "Missing/low-coverage and flagged-mapping counts never become zero constraints.",
            "Warmup uses the preceding quarter's inferred rate for 300 seconds; it does not supply an observed queue.",
            "Mean prior uses eligible day-15 counts; all comparisons are calibration diagnostics.",
            "Representative lengths and clipped normal speed factors are assumptions; south fleet transfers north mix.",
            "Paired runs fix IDs, intended departures, OD, types and speed factors; SUMO's per-step stochastic draws can still diverge.",
            "No unobserved downstream merging demand is added."]}
    if entry_ridge:
        manifest["limitations"].append("Entry-sum shrinkage is a sensitivity assumption; near-zero inferred inlet flows are not measured zeros.")
    (output / "demand_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return root, manifest
