#!/usr/bin/env python3
"""Canonicalise duplicate directed polyline sampling without inventing roads.

AI-assisted, 2026-10-18. A long observed edge may represent the same road as
an existing, same-direction, same-name chain with identical endpoint nodes.
Only such chains within 0.5 m cross-track and 0.5 m length difference qualify.
Original v1 evidence remains read-only; equivalence is an engineering hypothesis.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
try:
    from . import run_baseline as b
    from . import audit_network as audit
except ImportError:
    import run_baseline as b
    import audit_network as audit

ROOT = b.ROOT
SOURCE = ROOT / "data/processed/baidu/route_catalog_v1.json"
TARGET = ROOT / "data/processed/baidu/route_catalog_v2.json"
MAPPING = ROOT / "data/processed/baidu/route_catalog_v2_mapping.json"


def map_chain(chain, mapping):
    return [canonical for old in chain for canonical in mapping[old]]


def reachable(graph, start):
    found, queue = {start}, [start]
    while queue:
        current = queue.pop()
        for successor in graph.get(current, ()):
            if successor not in found:
                found.add(successor); queue.append(successor)
    return found


def find_equivalences(catalog, tolerance_m=.5):
    nodes = {n["id"]: n["xy_m"] for n in catalog["network"]["nodes"]}
    edges = {e["id"]: e for e in catalog["network"]["edges"]}
    allowed = {(c["from"], c["to"]) for c in catalog["network"]["connections"]
               if c["eligible_for_policy_path"] and not c["explicit_uturn"]}
    outgoing = {}
    for edge in edges.values():
        outgoing.setdefault(edge["from"], []).append(edge)
    results = []
    for old in sorted(edges.values(), key=lambda x: (x["length_m"], x["id"])):
        start, finish = nodes[old["from"]], nodes[old["to"]]
        vector = [finish[i] - start[i] for i in (0, 1)]
        squared = sum(x*x for x in vector)
        if squared < 1e-8:
            continue
        def position(node):
            delta = [nodes[node][i] - start[i] for i in (0, 1)]
            along = sum(delta[i] * vector[i] for i in (0, 1)) / squared
            perpendicular = math.hypot(*(delta[i] - along * vector[i] for i in (0, 1)))
            return along, perpendicular
        candidates = []
        def walk(node, path, length, maximum_offset):
            if len(path) > 60 or len(candidates) > 8:
                return
            if node == old["to"]:
                if len(path) >= 2 and abs(length - old["length_m"]) <= tolerance_m:
                    candidates.append({"replacement_chain": path, "chain_length_m": length,
                                       "length_difference_m": length - old["length_m"],
                                       "maximum_cross_track_m": maximum_offset})
                return
            current_t = position(node)[0]
            for edge in outgoing.get(node, ()):
                if edge["id"] == old["id"] or edge["length_m"] >= old["length_m"] - 1e-6:
                    continue
                if not set(edge["road_names"]).intersection(old["road_names"]):
                    continue
                # An existing long observed segment itself supports travel
                # through its intermediate sampling points. Its canonical
                # straight chain may cross an endpoint at which the shorter
                # source route happened to end, so adjacency need not have
                # appeared in that shorter route's record.
                t, residual = position(edge["to"])
                next_length = length + edge["length_m"]
                if current_t + 1e-8 < t <= 1 + 1e-8 and residual <= tolerance_m and next_length <= old["length_m"] + tolerance_m:
                    walk(edge["to"], path + [edge["id"]], next_length, max(maximum_offset, residual))
        walk(old["from"], [], 0., 0.)
        if candidates:
            candidates.sort(key=lambda x: (x["maximum_cross_track_m"], abs(x["length_difference_m"]), x["replacement_chain"]))
            results.append({"old_edge_id": old["id"], "old_length_m": old["length_m"],
                            "road_names": old["road_names"], "same_start_node": old["from"], "same_end_node": old["to"],
                            "alternatives_found": len(candidates), **candidates[0]})
    return results


def normalize(catalog):
    replacements = find_equivalences(catalog)
    tentative = {item["old_edge_id"]: item["replacement_chain"] for item in replacements
                 if item["alternatives_found"] == 1}
    edges = {e["id"]: e for e in catalog["network"]["edges"]}
    def expand(edge, stack=()):
        if edge in stack:
            raise ValueError("Cyclic geometric equivalence")
        return [x for child in tentative[edge] for x in expand(child, stack+(edge,))] if edge in tentative else [edge]
    mapping = {edge: expand(edge) for edge in edges}
    canonical = {edge: copy.deepcopy(value) for edge, value in edges.items() if edge not in tentative}
    for old, chain in mapping.items():
        for edge in chain:
            canonical[edge]["source_routes"] = sorted(set(canonical[edge]["source_routes"]) | set(edges[old]["source_routes"]))
    translated = {}
    def add_connection(a, z, original):
        if canonical[a]["to"] != canonical[z]["from"]:
            raise ValueError("Canonical transition changes physical endpoints")
        key = (a, z)
        if key not in translated:
            translated[key] = {"from": a, "to": z, "source_routes": [], "explicit_uturn": False,
                               "eligible_for_policy_path": True, "normalization_sources": []}
        value = translated[key]
        value["source_routes"] = sorted(set(value["source_routes"]) | set(original["source_routes"]))
        value["explicit_uturn"] |= original.get("explicit_uturn", False)
        value["eligible_for_policy_path"] &= original.get("eligible_for_policy_path", True)
        value["normalization_sources"].append({"from": original["from"], "to": original["to"]})
    for connection in catalog["network"]["connections"]:
        add_connection(mapping[connection["from"]][-1], mapping[connection["to"]][0], connection)
    # An introduced interior chain transition is supported either by the old
    # adjacency or by traversal of the geometrically equivalent long edge.
    original_by_pair = {(c["from"], c["to"]): c for c in catalog["network"]["connections"]}
    for old_edge,chain in mapping.items():
        for a, z in zip(chain, chain[1:]):
            if (a, z) not in translated:
                if (a,z) in original_by_pair:
                    original=original_by_pair[a,z]
                else:
                    original={"from":old_edge,"to":old_edge,"source_routes":edges[old_edge]["source_routes"],
                              "explicit_uturn":False,"eligible_for_policy_path":True}
                add_connection(a,z,original)
    result = copy.deepcopy(catalog)
    result["catalog_version"] = 2
    result["generated_at"] = b.utc_now()
    result["normalization"] = "同向、同名、完全相同首末节点的重叠采样链规范化；最大横向残差及长度差均不超过0.5m；不将邻近道路直接吸附。阈值是工程规则而非现场测绘精度。"
    result["source_sha256"] = {**catalog["source_sha256"], str(SOURCE.relative_to(ROOT)): b.sha256(SOURCE)}
    result["builder_sha256"] = b.sha256(Path(__file__))
    result["normalization_mapping_file"] = str(MAPPING.relative_to(ROOT))
    result["network"]["edges"] = sorted(canonical.values(), key=lambda e:e["id"])
    result["network"]["connections"] = [translated[key] for key in sorted(translated)]
    continuity = []
    allowed = {(c["from"],c["to"]) for c in result["network"]["connections"] if c["eligible_for_policy_path"]}
    for key, chain_key in (("demand_ods", "baseline_edge_ids"), ("topology_evidence_routes", "edge_ids"), ("route_candidates", "edge_ids")):
        for row in result[key]:
            original = row[chain_key]
            chain = map_chain(original, mapping)
            row[chain_key] = chain
            before = sum(edges[e]["length_m"] for e in original)
            after = sum(canonical[e]["length_m"] for e in chain)
            contiguous = all(canonical[a]["to"] == canonical[z]["from"] for a,z in zip(chain,chain[1:]))
            moves_allowed = all((a,z) in allowed for a,z in zip(chain,chain[1:]))
            if not contiguous or (key != "topology_evidence_routes" and not moves_allowed):
                raise ValueError("Normalized route lost continuity or allowed movements")
            if "model_length_m" in row:
                row["model_length_m"] = round(after,6)
            continuity.append({"kind": key, "id": row["id"], "old_length_m": before,"new_length_m": after,
                               "length_change_m": after-before,"endpoints_preserved": True,
                               "directed_continuity": contiguous,"all_turns_policy_eligible": moves_allowed})
    graph = {edge:set() for edge in canonical}
    for a,z in allowed: graph[a].add(z)
    queries=[]
    for demand in result["demand_ods"]:
        chain=demand["baseline_edge_ids"]
        queries.append({"demand_od_id":demand["id"],"fixed_baseline_edges":audit.classify_paths(graph,{chain[0]},{chain[-1]},chain),
                        "mandatory_edge_ids":audit.mandatory_edges(graph,{chain[0]},{chain[-1]},chain),
                        "eligible_catalog_candidate_ids":[c["id"] for c in result["route_candidates"] if c["demand_od_id"]==demand["id"]]})
    result["path_audit"]=queries
    hospital=[q for q in queries if next(d for d in result["demand_ods"] if d["id"]==q["demand_od_id"])["destination_id"]=="RJ_YJ"]
    common=set.intersection(*(set(q["mandatory_edge_ids"]) for q in hospital))
    result["hospital_common_mandatory"].update(edge_ids=sorted(common),edge_count=len(common),model_length_m=round(sum(canonical[e]["length_m"] for e in common),6))
    old_graph={edge:set() for edge in edges}
    for c in catalog["network"]["connections"]:
        if c["eligible_for_policy_path"] and not c["explicit_uturn"]: old_graph[c["from"]].add(c["to"])
    od_reachability=[]
    for d in catalog["demand_ods"]:
        before=reachable(old_graph,d["baseline_edge_ids"][0])
        after=reachable(graph,mapping[d["baseline_edge_ids"][0]][0])
        for goal in catalog["demand_ods"]:
            end=goal["baseline_edge_ids"][-1]
            same=(end in before)==(mapping[end][-1] in after)
            od_reachability.append({"start_od":d["id"],"end_od":goal["id"],"before":end in before,"after":mapping[end][-1] in after,"preserved":same})
    if not all(x["preserved"] for x in od_reachability):
        raise ValueError("Normalization changes terminal OD reachability")
    # Broader 2m scan is diagnostic only; these candidates are NOT merged.
    wide=find_equivalences(catalog,2.0)
    unmerged=[x for x in wide if x["old_edge_id"] not in tentative]
    record={"schema_version":1,"source_catalog":str(SOURCE.relative_to(ROOT)),"source_sha256":b.sha256(SOURCE),
            "normalized_catalog":str(TARGET.relative_to(ROOT)),"builder_sha256":b.sha256(Path(__file__)),
            "thresholds":{"maximum_cross_track_m":.5,"maximum_length_difference_m":.5,"same_named_road_required":True,
                          "same_endpoint_nodes_required":True,"monotone_directed_progress_required":True,"unique_alternative_required":True},
            "old_edge_to_canonical_chain":mapping,"merged_edges":[x for x in replacements if x["old_edge_id"] in tentative],
            "unmerged_suspected_overlaps_2m":unmerged,"route_continuity":continuity,"terminal_reachability_36_pairs":od_reachability,
            "all_terminal_reachability_preserved":True,"all_canonical_turns_have_mapped_source_evidence":True,
            "interior_turn_source_note":"A from=to old edge in normalization_sources denotes interior continuation supported by traversal of one overlapping long observed segment, not an observed U-turn.",
            "old_edge_count":len(edges),"canonical_edge_count":len(canonical),
            "old_allowed_connection_count":sum(c["eligible_for_policy_path"] for c in catalog["network"]["connections"]),
            "canonical_allowed_connection_count":len(allowed),
            "limitation":"Geometry equivalence is inferred from route traces; lane topology and real signal control remain unverified."}
    return result,record


def main():
    if TARGET.exists() or MAPPING.exists():
        raise ValueError("v2 catalog already exists; do not overwrite a frozen input")
    result,record=normalize(b.read_json(SOURCE))
    b.write_json(TARGET,result)
    record["normalized_catalog_sha256"]=b.sha256(TARGET)
    b.write_json(MAPPING,record)
    print(json.dumps({"old_edges":record["old_edge_count"],"canonical_edges":record["canonical_edge_count"],
                      "canonical_connections":record["canonical_allowed_connection_count"],
                      "merged":record["merged_edges"],"unmerged_suspects":len(record["unmerged_suspected_overlaps_2m"])},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
