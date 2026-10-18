# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；测试设计与研究使用待队员复核。
import json
from pathlib import Path
import tempfile
import unittest

from scripts import audit_network as audit


class NetworkAuditTests(unittest.TestCase):
    def test_same_route_ids_with_changed_survey_geometry_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix=".network-audit-test-", dir=audit.ROOT) as directory:
            root = Path(directory)
            survey_path = root / "survey.json"
            survey = {"routes": [{"id": "route", "origin_id": "O", "destination_id": "D",
                "use_for_baseline": True, "accepted_surface": True,
                "points": [[121.47, 31.21], [121.48, 31.22]]}]}
            survey_path.write_text(json.dumps(survey), encoding="utf-8")
            (root / "network.net.xml").write_text('<net><edge id="e" from="o" to="d"><lane id="e_0" length="100" /></edge><junction id="o" x="0" y="0"/><junction id="d" x="100" y="0"/></net>', encoding="utf-8")
            (root / "route_manifest.json").write_text(json.dumps({"connections": [], "routes": [
                {"id": "r0", "source_id": "route", "origin_id": "O", "destination_id": "D", "edge_ids": ["e"]}]}), encoding="utf-8")
            (root / "run_manifest.json").write_text(json.dumps({"status": "completed",
                "input_sha256": audit.sha256(survey_path),
                "artifacts": {name: audit.sha256(root / name) for name in ("network.net.xml", "route_manifest.json")}}), encoding="utf-8")
            self.assertEqual(len(audit.read_network(root, survey_path)[2]), 1)
            survey["routes"][0]["points"][1][0] += 0.01
            survey_path.write_text(json.dumps(survey), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input_sha256"):
                audit.read_network(root, survey_path)

    def test_no_invented_reverse_or_turn(self):
        graph = {"a": {"b"}, "b": set(), "c": set()}
        self.assertEqual(audit.classify_paths(graph, {"b"}, {"a"})["classification"], "zero")
        self.assertEqual(audit.classify_paths(graph, {"a"}, {"c"})["classification"], "zero")
        result = audit.classify_paths(graph, {"a"}, {"b"}, ["a", "b"])
        self.assertEqual(result["classification"], "one")
        self.assertTrue(result["count_complete"])

    def test_real_diamond_has_alternative_and_exact_cuts(self):
        graph = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": {"e"}, "e": set()}
        result = audit.classify_paths(graph, {"a"}, {"e"}, ["a", "b", "d", "e"])
        self.assertEqual(result["classification"], "multiple")
        self.assertFalse(result["count_complete"])
        self.assertEqual(result["paths"][1], ["a", "c", "d", "e"])
        self.assertEqual(audit.mandatory_edges(graph, {"a"}, {"e"}, result["paths"][0]), ["a", "d", "e"])

    def test_departure_anchor_choice_is_separate_from_in_trip_reroute(self):
        graph = {"a": {"c"}, "b": {"d"}, "c": {"z"}, "d": {"z"}, "z": set()}
        self.assertEqual(audit.classify_paths(graph, {"a"}, {"z"})["classification"], "one")
        grouped = audit.classify_paths(graph, {"a", "b"}, {"z"}, ["a", "c", "z"])
        self.assertEqual(grouped["classification"], "multiple")
        self.assertEqual(grouped["paths"][1], ["b", "d", "z"])
        self.assertEqual(audit.mandatory_edges(graph, {"a", "b"}, {"z"}, grouped["paths"][0]), ["z"])

    def test_loop_is_not_a_meaningful_alternative(self):
        graph = {"a": {"b"}, "b": {"a", "z"}, "z": set()}
        result = audit.classify_paths(graph, {"a"}, {"z"}, ["a", "b", "z"])
        self.assertEqual(result["classification"], "one")
        self.assertFalse(audit.valid_path(graph, ["a", "b", "a", "b", "z"], {"a"}, {"z"}))

    def test_two_examples_do_not_claim_total_or_approximate_mandatory_edges(self):
        graph = {"a": {"b", "c", "d"}, "b": {"x"}, "c": {"x"}, "d": {"z"}, "x": {"z"}, "z": set()}
        result = audit.classify_paths(graph, {"a"}, {"z"}, ["a", "b", "x", "z"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["candidate_limit"], 2)
        self.assertFalse(result["count_complete"])
        self.assertEqual(audit.mandatory_edges(graph, {"a"}, {"z"}, result["paths"][0]), ["a", "z"])

    def test_end_anchor_choice_and_single_edge_path(self):
        graph = {"a": {"b"}, "b": set()}
        result = audit.classify_paths(graph, {"a"}, {"a", "b"}, ["a"])
        self.assertEqual(result["classification"], "multiple")
        self.assertEqual(result["paths"][1], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
