# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；测试设计与研究使用待队员复核。
import json
from pathlib import Path
import tempfile
import unittest

from scripts import build_route_catalog as catalog


class RouteCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".route-catalog-test-", dir=catalog.ROOT)
        self.root = Path(self.temporary.name)
        self.a, self.b, self.c, self.d = [121.47, 31.21], [121.471, 31.21], [121.472, 31.21], [121.471, 31.211]

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, name, pp, baseline=False):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"status": 0, "result": {"routes": [{"steps": [{"path": ";".join(",".join(map(str, p)) for p in pp), "instruction": "沿测试路行驶100米", "turn": 0}]}]}}), encoding="utf-8")
        return {"id": name, "origin_id": "O", "destination_id": "D", "points": pp,
            "accepted_surface": True, "use_for_baseline": baseline, "source_response": str(path.relative_to(catalog.ROOT)), "duration_s": 100}

    def survey(self, rows):
        path = self.root / "survey.json"
        path.write_text(json.dumps({"study_area_id": "test", "coordinate_system": "bd09ll", "collection_bounds":
            {"west": 121.46, "east": 121.48, "south": 31.20, "north": 31.22}, "routes": rows}), encoding="utf-8")
        return path

    def test_cycle_is_evidence_but_never_adds_demand_or_complete_candidate(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        loop = self.row("loop", [self.a, self.b, self.d, self.b, self.c])
        result = catalog.build_catalog(self.survey([base, loop]))
        self.assertEqual(len(result["demand_ods"]), 1)
        self.assertEqual(len(result["topology_evidence_routes"]), 2)
        self.assertFalse(result["topology_evidence_routes"][1]["complete_route_eligible"])
        self.assertTrue(all(c["adds_demand"] is False for c in result["route_candidates"]))
        self.assertFalse(any(c.get("evidence_route_ids") == ["loop"] for c in result["route_candidates"]))

    def test_extra_candidate_does_not_add_demand_and_keeps_eta_source(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        extra = self.row("extra", [self.a, self.d, self.c])
        candidate_path = self.root / "candidates.json"
        candidate_path.write_text(json.dumps({"routes": [extra]}), encoding="utf-8")
        result = catalog.build_catalog(self.survey([base]), [candidate_path])
        self.assertEqual(len(result["demand_ods"]), 1)
        self.assertEqual(len(result["route_candidates"]), 2)
        self.assertEqual(result["route_candidates"][1]["platform_eta_samples"][0]["evidence_route_id"], "extra")
        self.assertFalse(result["route_candidates"][1]["same_baseline_start_end_edges"])

    def test_geometry_drift_from_raw_response_is_rejected(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        base["points"] = [self.a, self.d, self.c]
        with self.assertRaisesRegex(ValueError, "原始路线"):
            catalog.build_catalog(self.survey([base]))

    def test_nearby_vertices_do_not_create_reverse_or_cross_connections(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        shifted = [self.b[0] + .00001, self.b[1]]
        other = self.row("other", [shifted, self.d])
        result = catalog.build_catalog(self.survey([base, other]))
        self.assertEqual(len(result["network"]["connections"]), 1)
        self.assertEqual(result["path_audit"][0]["fixed_baseline_edges"]["classification"], "one")

    def test_invalid_coords_and_adjacent_duplicate_normalization(self):
        self.assertEqual(catalog.points([self.a, self.a, self.b]), [tuple(self.a), tuple(self.b)])
        with self.assertRaises(ValueError):
            catalog.points([self.a, [float("nan"), 31.2]])

    def test_explicit_uturn_connection_is_preserved_as_evidence_but_not_policy(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        turn = self.row("turn", [self.a, self.b, self.d, self.c])
        raw = catalog.ROOT / turn["source_response"]
        def step(pp, instruction, code):
            return {"path": ";".join(",".join(map(str, p)) for p in pp), "instruction": instruction, "turn": code}
        raw.write_text(json.dumps({"status": 0, "result": {"routes": [{"steps": [
            step([self.a, self.b], "沿测试路行驶100米，调头", 5),
            step([self.b, self.d, self.c], "沿测试路行驶100米", 0)]}]}}), encoding="utf-8")
        result = catalog.build_catalog(self.survey([base, turn]))
        self.assertFalse(result["topology_evidence_routes"][1]["complete_route_eligible"])
        turns = [c for c in result["network"]["connections"] if c["explicit_uturn"]]
        self.assertEqual(len(turns), 1)
        self.assertFalse(turns[0]["eligible_for_policy_path"])
        self.assertEqual(turns[0]["source_routes"], ["turn"])

    def test_candidate_cannot_silently_bind_to_changed_base_or_response(self):
        base = self.row("base", [self.a, self.b, self.c], True)
        extra = self.row("extra", [self.a, self.d, self.c])
        source = self.survey([base])
        candidate_path = self.root / "candidates.json"
        candidate_path.write_text(json.dumps({"routes": [extra], "base_survey_sha256": "wrong"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "原调查 SHA-256"):
            catalog.build_catalog(source, [candidate_path])
        extra["source_sha256"] = "wrong"
        candidate_path.write_text(json.dumps({"routes": [extra], "base_survey_sha256": catalog.sha256(source)}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            catalog.build_catalog(source, [candidate_path])


if __name__ == "__main__":
    unittest.main()
