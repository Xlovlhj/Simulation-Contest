#!/usr/bin/env python3
"""Offline, source-hashed extraction of route steps and censored traffic speeds.

All range summaries use the previously frozen training partition only. Route
step speeds are distance / platform step ETA, not point speeds or SUMO limits.
No API access, private configuration, source rewrites or dependency install.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/processed/identification_v2"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def packed(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def stamp(value: str) -> float:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Observation timestamps must have a timezone")
    return moment.timestamp()


def number(value: object, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a measurement")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
        raise ValueError("Measurement must be finite and positive")
    return result


def flag(value: object) -> bool:
    if value in (True, "True", "true"):
        return True
    if value in (False, None, "", "False", "false"):
        return False
    raise ValueError("Invalid Boolean value")


def partition(requested: str, received: str, windows: dict) -> str:
    """Request and receipt must both fall within the same half-open window."""
    start, stop = stamp(requested), stamp(received)
    if stop < start:
        raise ValueError("Receipt precedes request")
    hits = [name for name, w in windows.items()
            if stamp(w["start"]) <= start <= stop < stamp(w["end"])]
    if len(hits) > 1:
        raise ValueError("Analysis windows overlap")
    return hits[0] if hits else "outside_declared_windows"


def quantile(values: list[float], probability: float) -> float:
    """Linear interpolation at (n-1)*p, equivalent to conventional type 7."""
    ordered = sorted(values)
    if not ordered or not 0 <= probability <= 1:
        raise ValueError("Quantiles need observations and a probability")
    index = (len(ordered)-1)*probability
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (index-lower)*(ordered[upper]-ordered[lower])


def summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {"count": len(values), "mean": statistics.fmean(values),
            **{name: quantile(values, q) for name, q in
               (("min", 0), ("p10", .1), ("p25", .25), ("median", .5),
                ("p75", .75), ("p90", .9), ("max", 1))}}


def lag_one_correlation(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    before, after = values[:-1], values[1:]
    mean_before, mean_after = statistics.fmean(before), statistics.fmean(after)
    denominator = math.sqrt(sum((v-mean_before)**2 for v in before) * sum((v-mean_after)**2 for v in after))
    return sum((a-mean_before)*(b-mean_after) for a, b in zip(before, after))/denominator if denominator else None


def points(path: str) -> list[tuple[float, float]]:
    result = []
    for part in path.split(";"):
        longitude, latitude = (float(n) for n in part.split(","))
        if not all(math.isfinite(n) for n in (longitude, latitude)):
            raise ValueError("Nonfinite geometry")
        point = round(longitude, 6), round(latitude, 6)
        if not result or point != result[-1]:
            result.append(point)
    return result


def edge_lookup(catalog: dict) -> tuple[dict, dict]:
    nodes = {n["id"]: tuple(n["point"]) for n in catalog["network"]["nodes"]}
    lookup = {}
    for edge in catalog["network"]["edges"]:
        pair = nodes[edge["from"]], nodes[edge["to"]]
        if pair in lookup:
            raise ValueError("Ambiguous duplicate directed geometry")
        lookup[pair] = edge
    return lookup, nodes


def match_step(path: str, lookup: dict) -> dict:
    geometry = points(path)
    segments = list(zip(geometry, geometry[1:]))
    matched = [lookup[pair] for pair in segments if pair in lookup]
    return {"point_count": len(geometry), "edge_ids": [e["id"] for e in matched],
            "model_length_m": sum(e["length_m"] for e in matched),
            "unmatched_segment_count": len(segments)-len(matched),
            "exact_directed_geometry_match": bool(segments) and len(segments) == len(matched),
            "geometry_signature": digest(packed(geometry)),
            "start": geometry[0] if geometry else None,
            "end": geometry[-1] if geometry else None}


def road_name(instruction: str) -> str:
    cleaned = html.unescape(re.sub("<[^>]+>", "", instruction))
    found = re.search(r"沿(.+?)行驶", cleaned)
    return found.group(1) if found else "UNRESOLVED"


def preceding_traffic(requested: str, phase: str, snapshots: list[dict]) -> dict:
    """Causal receipt-time join; retain age rather than impose an invented cutoff."""
    candidates = [s for s in snapshots if s["phase"] == phase and stamp(s["received_at"]) <= stamp(requested)]
    if not candidates:
        return {"preceding_traffic_sample_id": "", "preceding_traffic_age_s": ""}
    latest = max(candidates, key=lambda s: (stamp(s["received_at"]), s["sample_id"]))
    return {"preceding_traffic_sample_id": latest["sample_id"],
            "preceding_traffic_age_s": round(stamp(requested)-stamp(latest["received_at"]), 6)}


def csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("Refusing to write an empty observation table")
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: compact(value) if isinstance(value, (list, dict, tuple)) else value
                         for key, value in row.items()})
    return out.getvalue().encode()


def training_summaries(routes: list[dict], steps: list[dict], sections: list[dict]) -> dict:
    route_groups, step_groups, traffic_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    route_totals = {r["sample_id"]: r["route_duration_s"] for r in routes}
    for row in routes:
        if row["fit_eligible"] and row["phase"] == "training":
            route_groups[row["route_pair_id"]].append(row)
    for row in steps:
        if row["fit_eligible"] and row["phase"] == "training":
            step_groups[(row["route_pair_id"], row["step_index"], row["geometry_signature"])].append(row)
    for row in sections:
        if row["phase"] == "training" and row["speed_valid"]:
            traffic_groups[(row["road_name"], row["section_desc"], row["congestion_distance_m"])].append(row)
    return {
        "scope": "training only; descriptive ranges, not independently measured physical parameters",
        "quantile_method": "type 7 linear interpolation at (n-1)*p",
        "route_eta": {key: {"eta_s": summary([r["route_duration_s"] for r in rows]),
                                  "platform_distance_m": summary([r["route_distance_m"] for r in rows]),
                                  "route_mean_speed_kmh": summary([r["route_mean_speed_kmh"] for r in rows]),
                                  "lag_one_eta_correlation": lag_one_correlation([r["route_duration_s"] for r in sorted(rows, key=lambda r: (r["requested_at"], r["sample_id"]))]),
                                  "repeated_observation_note": "Twelve sequential provider estimates of the same path, not twelve independent vehicle trips. Lag-one correlation is descriptive only; no IID confidence interval or inferred sample-size correction.",
                                  "sample_ids": [r["sample_id"] for r in rows]}
                      for key, rows in sorted(route_groups.items())},
        "route_steps": [{"route_pair_id": key[0], "step_index": key[1], "geometry_signature": key[2],
                         "road_name": rows[0]["road_name"], "edge_ids": rows[0]["edge_ids"],
                         "platform_distance_m": rows[0]["step_distance_m"],
                         "model_length_m": rows[0]["model_length_m"],
                         "duration_s": summary([r["step_duration_s"] for r in rows]),
                         "effective_step_speed_kmh": summary([r["step_effective_speed_kmh"] for r in rows]),
                         "step_fraction_of_route_eta": summary([r["step_duration_s"]/route_totals[r["sample_id"]] for r in rows]),
                         "step_fraction_population_sd": statistics.pstdev([r["step_duration_s"]/route_totals[r["sample_id"]] for r in rows]),
                         "sample_ids": [r["sample_id"] for r in rows]}
                        for key, rows in sorted(step_groups.items())],
        "traffic_sections": [{"road_name": key[0], "section_desc": key[1], "congestion_distance_m": key[2],
                              "reported_speed_kmh": summary([r["reported_speed_kmh"] for r in rows]),
                              "sample_ids": [r["sample_id"] for r in rows]}
                             for key, rows in sorted(traffic_groups.items())]}


def extract() -> dict:
    sources = {}
    expected_sources = {}

    def read(path: str | Path) -> bytes:
        path = (ROOT / path).resolve()
        if not path.is_relative_to(ROOT) or path.name.endswith(".local.json"):
            raise ValueError("Input must be a public workspace file")
        content = path.read_bytes()
        relative = str(path.relative_to(ROOT))
        if relative in expected_sources and digest(content) != expected_sources[relative]:
            raise ValueError("Source changed since final observation freeze: " + relative)
        sources[relative] = {"path": relative, "bytes": len(content), "sha256": digest(content)}
        return content

    config = json.loads(read("config/final_validation.json"))
    frozen = json.loads(read("data/processed/final/data_manifest.json"))
    expected_sources.update({r["path"]: r["sha256"] for r in frozen["source_files"]})
    frozen_samples = read("data/processed/final/samples.csv")
    if digest(frozen_samples) != frozen["samples_sha256"]:
        raise ValueError("Frozen sample table hash mismatch")
    baseline_rows = list(csv.DictReader(io.StringIO(frozen_samples.decode())))
    baseline = {r["sample_id"]: r for r in baseline_rows}
    if len(baseline) != len(baseline_rows) or config["windows"] != frozen["protocol"]["windows"]:
        raise ValueError("Frozen temporal protocol mismatch")
    catalog = json.loads(read("data/processed/baidu/route_catalog_v1.json"))
    lookup, nodes = edge_lookup(catalog)
    read(Path(__file__))
    requests, snapshots, road_entries, sections, routes, step_rows = [], [], [], [], [], []
    for folder_name in config["collections"]:
        folder = ROOT / folder_name
        for lineno, line in enumerate(read(folder / "requests.jsonl").decode().splitlines(), 1):
            record = json.loads(line)
            sid = f"{folder.name}/line_{lineno:06d}"
            path = (folder / record["response_file"]).resolve()
            if not path.is_relative_to(folder):
                raise ValueError("Response escapes its collection")
            content = read(path)
            payload = json.loads(content)
            if record["http_status"] != 200 or payload["status"] not in (0, "0"):
                raise ValueError("Formal collection has an unsuccessful response")
            common = {"sample_id": sid, "collection_id": folder.name,
                      "requested_at": record["requested_at"], "received_at": record["received_at"],
                      "phase": partition(record["requested_at"], record["received_at"], config["windows"]),
                      "source_observation_time": record.get("source_observation_time"),
                      "response_file": str(path.relative_to(ROOT)), "response_sha256": digest(content)}
            requests.append((record, payload, common))
            if record["kind"] != "traffic":
                continue
            roads = payload.get("road_traffic", [])
            count = sum(len(road.get("congestion_sections", [])) for road in roads)
            snapshots.append({**common, "road_count": len(roads), "section_count": count,
                              "evaluation_status": payload.get("evaluation", {}).get("status"),
                              "evaluation_description": payload.get("evaluation", {}).get("status_desc"),
                              "advanced_georeferenced_traffic_detail_present": any("traffic_detail" in road for road in roads)})
            for road_index, road in enumerate(roads):
                road_sections = road.get("congestion_sections", [])
                road_entries.append({**common, "road_index": road_index, "road_name": road.get("road_name", "UNKNOW"),
                                     "congestion_section_count": len(road_sections),
                                     "numeric_speed_available": any(s.get("speed") is not None for s in road_sections),
                                     "missing_section_interpretation": "No numerical congestion-section speed returned; do not impute zero or free-flow speed" if not road_sections else ""})
                for section_index, section in enumerate(road_sections):
                    try:
                        speed = number(section.get("speed"))
                    except (TypeError, ValueError):
                        speed = None
                    desc = section.get("section_desc", "")
                    sections.append({**common, "road_index": road_index, "section_index": section_index,
                                     "road_name": road.get("road_name", "UNKNOW"), "section_desc": desc,
                                     "direction_text": re.split("[,，]", desc)[0],
                                     "congestion_distance_m": section.get("congestion_distance"),
                                     "reported_speed_kmh": speed, "speed_valid": speed is not None,
                                     "status": section.get("status"), "road_type": section.get("road_type"),
                                     "speed_spatial_support": "entire described congestion section; unprovided geometry",
                                     "exact_model_edge_mapping_available": False})
    for record, payload, common in requests:
        if record["kind"] != "direction":
            continue
        raw = baseline[common["sample_id"]]
        if common["response_sha256"] != raw["response_sha256"]:
            raise ValueError("Direction response changed from frozen sample")
        if common["phase"] != raw["phase"]:
            raise ValueError("Frozen direction partition mismatch")
        eligible = {"fit_eligible": flag(raw["fit_eligible"]), "evaluation_eligible": flag(raw["evaluation_eligible"]),
                    "selected_in_bucket": flag(raw["selected_in_bucket"]), "duplicate_of": raw["duplicate_of"]}
        route_options = payload["result"]["routes"]
        if len(route_options) != 1:
            raise ValueError("Frozen observations expected exactly one route per response")
        route = route_options[0]
        duration, distance = number(route["duration"]), number(route["distance"])
        pieces = route["steps"]
        row = {**common, "route_pair_id": record["route_pair_id"], **eligible,
               "route_duration_s": duration, "route_distance_m": distance,
               "route_mean_speed_kmh": distance/duration*3.6,
               "step_count": len(pieces), "step_duration_sum_s": sum(number(s["duration"]) for s in pieces),
               "step_distance_sum_m": sum(number(s["distance"]) for s in pieces),
               **preceding_traffic(common["requested_at"], common["phase"], snapshots)}
        row.update(route_minus_step_duration_s=duration-row["step_duration_sum_s"],
                   route_minus_step_distance_m=distance-row["step_distance_sum_m"])
        routes.append(row)
        for step_index, step in enumerate(pieces):
            match = match_step(step["path"], lookup)
            dur, dist = number(step["duration"]), number(step["distance"])
            step_rows.append({**common, "route_pair_id": record["route_pair_id"], **eligible,
                              "step_index": step_index, "road_name": road_name(step["instruction"]),
                              "step_duration_s": dur, "step_distance_m": dist,
                              "step_effective_speed_kmh": dist/dur*3.6,
                              "speed_semantics": "distance / provider step ETA, includes unresolved intersection or terminal effects; not point speed or a physical speed limit",
                              **match, "direction_code": step.get("direction"), "turn_code": step.get("turn"),
                              "traffic_condition": step.get("traffic_condition", [])})
    training = training_summaries(routes, step_rows, sections)
    section_groups = defaultdict(list)
    for section in sections:
        section_groups[(section["road_name"], section["section_desc"], section["congestion_distance_m"])].append(section)
    section_series = {
        "grouping": "Exact road name, direction-containing section description, and congestion length. Different descriptions/lengths are never pooled as a local-edge speed.",
        "groups": [{"road_name": key[0], "section_desc": key[1], "congestion_distance_m": key[2],
                    "training_speed_kmh": summary([r["reported_speed_kmh"] for r in rows if r["phase"] == "training" and r["speed_valid"]]),
                    "observations": [{name: r[name] for name in ("sample_id", "phase", "requested_at", "received_at", "reported_speed_kmh", "status", "response_file", "response_sha256")}
                                     for r in sorted(rows, key=lambda row: (row["requested_at"], row["sample_id"]))]}
                   for key, rows in sorted(section_groups.items())]}
    phase_counts = {}
    for phase in config["windows"]:
        phase_counts[phase] = {"traffic_snapshots": sum(r["phase"] == phase for r in snapshots),
                               "raw_direction_responses": sum(r["phase"] == phase for r in routes),
                               "eligible_direction_responses": sum(r["phase"] == phase and (r["fit_eligible"] or r["evaluation_eligible"]) for r in routes),
                               "congestion_speed_sections": sum(r["phase"] == phase and r["speed_valid"] for r in sections)}
    ruijin = [r for r in sections if r["road_name"] == "瑞金二路"]
    ruijin_candidate = [e for e in catalog["network"]["edges"] if "瑞金二路" in e["road_names"]
                       and nodes[e["to"]][1] > nodes[e["from"]][1]]
    audit = {
        "schema_version": 2, "original_data_snapshot_id": frozen["snapshot_id"],
        "coordinate_system": config["coordinate_system"], "windows": config["windows"],
        "counts": {"traffic_snapshots": len(snapshots), "traffic_road_entries": len(road_entries),
                   "traffic_sections": len(sections), "numeric_speed_sections": sum(r["speed_valid"] for r in sections),
                   "raw_direction_responses": len(routes), "raw_route_steps": len(step_rows),
                   "eligible_route_steps": sum(r["fit_eligible"] or r["evaluation_eligible"] for r in step_rows),
                   "exactly_mapped_route_steps": sum(r["exact_directed_geometry_match"] for r in step_rows),
                   "missing_source_observation_timestamps": sum(not c["source_observation_time"] for _, _, c in requests)},
        "phases": phase_counts,
        "step_route_consistency": {"route_minus_step_duration_s": summary([r["route_minus_step_duration_s"] for r in routes]),
                                   "duration_residual_counts": dict(sorted(Counter(r["route_minus_step_duration_s"] for r in routes).items())),
                                   "all_route_distances_equal_sum_of_steps": all(r["route_minus_step_distance_m"] == 0 for r in routes),
                                   "training_step_fraction_population_sd": summary([r["step_fraction_population_sd"] for r in training["route_steps"]]),
                                   "step_dependence_observation": "Within each training OD, step-duration / route-duration fractions vary very little (see exact summaries). The source algorithm is unknown; steps are not independent sensors.",
                                   "interpretation": "Retain route total and step totals separately. No allocation of residual seconds to an arbitrary edge."},
        "temporal_alignment": {"traffic_receipt_to_route_request_age_s": summary([r["preceding_traffic_age_s"] for r in routes if r["preceding_traffic_age_s"] != ""]),
                               "join": "Latest already received traffic response in the same phase, never a future response; age retained, no imposed cutoff",
                               "sampling": "traffic approximately 120s; four OD queries approximately 300s each; session restarts shift cadence",
                               "clock": "Request and receive timestamps are available; independent source observation times are absent"},
        "ruijin_road_speed_mapping": {"returned_sections": len(ruijin),
                                      "snapshots_with_speed": len({r["sample_id"] for r in ruijin}),
                                      "descriptions_and_lengths": [dict(road_name=k[0], section_desc=k[1], congestion_distance_m=k[2], count=v)
                                                                  for k, v in sorted(Counter((r["road_name"],r["section_desc"],r["congestion_distance_m"]) for r in ruijin).items())],
                                      "candidate_northbound_model_edge_ids": [e["id"] for e in ruijin_candidate],
                                      "candidate_union_length_m": sum(e["length_m"] for e in ruijin_candidate),
                                      "exact_spatial_match": False,
                                      "interpretation": "Road name and direction identify plausible overlapping model edges only. Text section endpoints vary and lack geometry; reported 510–1580m sections cannot be treated as the old 68.735m event speed limit."},
        "identifiability": [
            "Step ETA supplies finer spatial constraints than route ETA and can be matched by full directed geometry.",
            "Step distance/ETA is an effective travel speed including unresolved delay, not a measured cruise speed.",
            "Ordinary traffic responses report selected congestion sections. Missing numerical speed is censored/not returned, not zero or known free flow.",
            "Route ETA and its constituent step ETAs come from one provider response; do not count them as independent observations or double their statistical weight.",
            "No actual traffic counts, vehicle trajectories, traffic-signal cycles, phase splits or observed physical incidents are recovered by this extraction.",
            "All quantiles/range summaries in training_summary.json exclude duplicates and all held-out phases."
        ]}
    artifacts = {"routes.csv": csv_bytes(routes), "route_steps.csv": csv_bytes(step_rows),
                 "traffic_snapshots.csv": csv_bytes(snapshots), "traffic_roads.csv": csv_bytes(road_entries),
                 "traffic_sections.csv": csv_bytes(sections), "training_summary.json": packed(training),
                 "traffic_section_series.json": packed(section_series),
                 "audit.json": packed(audit)}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (OUTPUT / name).write_bytes(content)
    manifest = {"schema_version": 2, "purpose": "Source-hashed observations for an explicitly inverse/identification model",
                "network_calls": 0, "source_files": sorted(sources.values(), key=lambda item: item["path"]),
                "artifacts": {name: {"sha256": digest(content), "bytes": len(content)} for name, content in sorted(artifacts.items())}}
    manifest["snapshot_id"] = "OBS2_" + digest(packed(manifest))[:16]
    (OUTPUT / "manifest.json").write_bytes(packed(manifest))
    return {"snapshot_id": manifest["snapshot_id"], "counts": audit["counts"], "phases": phase_counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    print(json.dumps(extract(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
