"""Geometry equivalence must preserve provenance, direction and observed routes."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import normalize_network_v2 as normalizer


class NormalizationTests(unittest.TestCase):
    def test_known_overlaps_are_canonicalised_with_submetre_residual(self):
        source = json.loads((ROOT / "data/processed/baidu/route_catalog_v1.json").read_text())
        result, report = normalizer.normalize(source)
        self.assertEqual(report["old_edge_to_canonical_chain"]["e000182"], ["e000116", "e000000"])
        self.assertEqual(report["old_edge_to_canonical_chain"]["e000133"], ["e000280", "e000281"])
        self.assertEqual(len(result["network"]["edges"]), 305)
        self.assertEqual(report["canonical_allowed_connection_count"], 308)
        self.assertTrue(all(row["maximum_cross_track_m"] < .5 for row in report["merged_edges"]))
        self.assertTrue(all(row["directed_continuity"] for row in report["route_continuity"]))
        self.assertTrue(all(row["preserved"] for row in report["terminal_reachability_36_pairs"]))
        self.assertEqual(len(source["network"]["edges"]), 307)

    def test_opposite_direction_or_parallel_road_is_not_merged(self):
        def example(endpoint_y, road="same", reverse=False):
            return {"network": {"nodes": [{"id":"a","xy_m":[0,0]}, {"id":"b","xy_m":[10,0]},
                                              {"id":"mid","xy_m":[5,endpoint_y]}],
                    "edges": [{"id":"long","from":"a","to":"b","length_m":10,"road_names":["same"]},
                              {"id":"one","from":"mid" if reverse else "a","to":"a" if reverse else "mid",
                               "length_m":5,"road_names":[road]},
                              {"id":"two","from":"b" if reverse else "mid","to":"mid" if reverse else "b",
                               "length_m":5,"road_names":[road]}], "connections":[]}}
        self.assertEqual(normalizer.find_equivalences(example(0))[0]["old_edge_id"], "long")
        self.assertEqual(normalizer.find_equivalences(example(2)), [])
        self.assertEqual(normalizer.find_equivalences(example(0, road="other")), [])
        self.assertEqual(normalizer.find_equivalences(example(0, reverse=True)), [])


if __name__ == "__main__": unittest.main()
