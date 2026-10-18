# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date
# was not provided; the team must review this code and complete AI disclosure.
"""Offline meaningful invariants for the uncalibrated technical baseline."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_baseline", ROOT / "scripts" / "run_baseline.py")
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config" / "baseline.json").read_text())
        self.data = {"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll",
                     "collection_bounds": {"west": 120, "east": 122, "south": 30, "north": 32}, "routes": []}

    def add_route(self, identity, points, accepted=True):
        self.data["routes"].append({"id": identity, "points": points, "accepted_surface": accepted})

    def test_projection_distance(self):
        a = baseline.project_point((121, 31), (121, 31))
        b = baseline.project_point((121, 31.001), (121, 31))
        self.assertAlmostEqual(a[0], 0)
        self.assertAlmostEqual(b[1], 111.19508, places=4)

    def test_direction_sharing_and_zero_length(self):
        a, b, c = [121, 31], [121.001, 31], [121.002, 31]
        self.add_route("out", [a, a, b, c])
        self.add_route("return", [c, b, a])
        self.add_route("shared", [a, b])
        network = baseline.prepare_network(self.data, self.config)
        self.assertEqual(len(network["edges"]), 4)
        self.assertEqual(network["routes"][0]["edge_ids"][0], network["routes"][2]["edge_ids"][0])
        self.assertNotEqual(network["routes"][0]["edge_ids"][0], network["routes"][1]["edge_ids"][-1])
        self.assertEqual(len(network["connections"]), 2)

    def test_plane_crossing_does_not_add_node_or_connection(self):
        self.add_route("horizontal", [[121, 31.001], [121.002, 31.001]])
        self.add_route("vertical", [[121.001, 31], [121.001, 31.002]])
        network = baseline.prepare_network(self.data, self.config)
        self.assertEqual(len(network["nodes"]), 4)
        self.assertEqual(network["connections"], [])

    def test_accepted_outside_model_bounds_fails(self):
        self.data["model_bounds"] = {"west": 121, "east": 121.01, "south": 31, "north": 31.01}
        self.add_route("outside", [[121, 31], [121.1, 31]])
        with self.assertRaisesRegex(ValueError, "outside model bounds"):
            baseline.prepare_network(self.data, self.config)

    def test_unaccepted_route_is_not_simulated(self):
        self.add_route("accepted", [[121, 31], [121.001, 31]])
        self.add_route("elevated", [[121, 31], [121.2, 31]], accepted=False)
        network = baseline.prepare_network(self.data, self.config)
        self.assertEqual(len(network["routes"]), 1)
        departures = baseline.create_departures(network, self.config)
        self.assertEqual(len(departures), 20)
        self.assertEqual(departures[-1]["depart"], 1140)

    def test_forced_waypoint_candidate_can_be_excluded_from_baseline(self):
        self.add_route("natural", [[121, 31], [121.001, 31]])
        self.add_route("detour", [[121, 31], [121.002, 31]])
        self.data["routes"][-1]["use_for_baseline"] = False
        network = baseline.prepare_network(self.data, self.config)
        self.assertEqual([route["source_id"] for route in network["routes"]], ["natural"])
        self.assertEqual(network["excluded_routes"][0]["reason"], "not_selected_for_baseline")

    def test_unfinished_and_departure_wait_are_retained(self):
        departures = [{"id": "a", "route": "r", "depart": 0},
                      {"id": "b", "route": "r", "depart": 20},
                      {"id": "c", "route": "r", "depart": 40}]
        trips = [{"id": "a", "depart": "5", "arrival": "35"},
                 {"id": "b", "depart": "30", "arrival": "-1"},
                 {"id": "c", "depart": "-1", "arrival": "-1"}]
        metrics = baseline.compute_metrics(trips, departures, 100,
                                           {"inserted": "2", "arrived": "1", "running": "1"})
        self.assertEqual((metrics["planned"], metrics["departed"], metrics["arrived"]), (3, 2, 1))
        self.assertEqual(metrics["unfinished_total"], 2)
        self.assertEqual(metrics["total_in_network_vehicle_time_s"], 100)
        self.assertEqual(metrics["total_departure_wait_time_s"], 75)
        self.assertEqual(metrics["total_system_vehicle_time_s"], 175)
        self.assertEqual(metrics["completed_travel_time_mean_s"], 30)
        self.assertTrue(metrics["count_conservation"])

    def test_summary_disagreement_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "disagree"):
            baseline.compute_metrics([], [{"id": "a", "route": "r", "depart": 0}], 100,
                                     {"inserted": "1", "arrived": "0", "running": "1"})

    def test_collision_teleport_or_discard_invalidates_comparison(self):
        good = {"collisions": "0", "teleports": "0", "discarded": "0"}
        self.assertTrue(baseline.assess_physics(good, 0)["physics_valid"])
        for field in good:
            with self.subTest(field=field):
                result = baseline.assess_physics({**good, field: "1"}, 0)
                self.assertFalse(result["physics_valid"])
                self.assertIn(field + "_nonzero", result["quality_reasons"])
        # The independent detailed output cannot be overridden by a zero summary.
        self.assertFalse(baseline.assess_physics(good, 1)["physics_valid"])
        self.assertFalse(baseline.assess_physics(None, 0)["physics_valid"])
        self.assertFalse(baseline.assess_physics({**good, "collisions": "nan"}, 0)["physics_valid"])

    @unittest.skipUnless((ROOT / ".venv/bin/sumo").is_file() and (ROOT / ".venv/bin/netconvert").is_file(),
                         "Workspace SUMO installation is required for this integration test")
    def test_real_merge_safety_and_collision_quality_gate(self):
        """Equal-distance arrivals must yield; deliberately unsafe rules fail.

        Both runs finish every trip, showing that conservation/completion alone
        does not make a colliding simulation usable. Detection remains enabled.
        """
        self.add_route("west", [[120.999, 31], [121, 31], [121, 31.001]])
        self.add_route("east", [[121.001, 31], [121, 31], [121, 31.001]])
        output_root = ROOT / "outputs" / "baseline"
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="test_merge_", dir=output_root) as temporary:
            fixture = Path(temporary)
            input_path, config_path = fixture / "routes.json", fixture / "config.json"
            input_path.write_text(json.dumps(self.data))
            self.config.update(simulation_duration_s=120, departure_duration_s=30,
                               departure_interval_s=10, input_routes=str(input_path))
            config_path.write_text(json.dumps(self.config))
            original_write = baseline.write_inputs

            def unsafe_inputs(folder, *args):
                original_write(folder, *args)
                tree = ET.parse(folder / "network.nod.xml")
                for node in tree.getroot():
                    node.set("type", "unregulated")
                tree.write(folder / "network.nod.xml")

            for unsafe in (False, True):
                run_id = fixture.name + ("_unsafe" if unsafe else "_priority")
                run_folder = output_root / run_id
                try:
                    with mock.patch.object(baseline, "write_inputs", unsafe_inputs if unsafe else original_write):
                        with contextlib.redirect_stdout(io.StringIO()):
                            code = baseline.main(["--config", str(config_path), "--run-id", run_id])
                    metrics = json.loads((run_folder / "metrics.json").read_text())
                    manifest = json.loads((run_folder / "run_manifest.json").read_text())
                    self.assertEqual(baseline.sha256(run_folder / manifest["script_snapshot"]),
                                     manifest["script_sha256"])
                    self.assertEqual(manifest["artifacts"][manifest["script_snapshot"]], manifest["script_sha256"])
                    self.assertEqual(metrics["arrived"], metrics["planned"])
                    self.assertTrue(metrics["count_conservation"])
                    self.assertEqual(metrics["physics_counters"]["teleports"], 0)
                    self.assertTrue(manifest["network"]["observed_connection_set_preserved"])
                    self.assertTrue(manifest["network"]["internal_movements_preserved"])
                    if unsafe:
                        self.assertEqual(code, 2)
                        self.assertEqual(manifest["status"], "physics_invalid")
                        self.assertFalse(metrics["physics_valid"])
                        self.assertFalse(manifest["eligible_for_governance"])
                        self.assertGreater(metrics["physics_counters"]["collision_events"], 0)
                    else:
                        self.assertEqual(code, 0)
                        self.assertTrue(metrics["physics_valid"])
                        self.assertEqual(metrics["physics_counters"]["collision_events"], 0)
                finally:
                    shutil.rmtree(run_folder, ignore_errors=True)

    @unittest.skipUnless((ROOT / ".venv/bin/sumo").is_file() and (ROOT / ".venv/bin/netconvert").is_file(),
                         "Workspace SUMO installation is required for this integration test")
    def test_real_sumo_retains_driving_and_undeparted_vehicles(self):
        """Two identical one-lane departures, one second: one enters, one waits."""
        self.add_route("first", [[121, 31], [121.01, 31]])
        self.add_route("second", [[121, 31], [121.01, 31]])
        output_root = ROOT / "outputs" / "baseline"
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="test_inputs_", dir=output_root) as temporary:
            fixture = Path(temporary)
            input_path, config_path = fixture / "routes.json", fixture / "config.json"
            input_path.write_text(json.dumps(self.data))
            self.config.update(simulation_duration_s=1, departure_duration_s=1, input_routes=str(input_path))
            config_path.write_text(json.dumps(self.config))
            run_id = fixture.name + "_run"
            run_folder = output_root / run_id
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = baseline.main(["--config", str(config_path), "--run-id", run_id])
                self.assertEqual(result, 0)
                metrics = json.loads((run_folder / "metrics.json").read_text())
                self.assertEqual(metrics["planned"], 2)
                self.assertEqual(metrics["departed"], 1)
                self.assertEqual(metrics["arrived"], 0)
                self.assertEqual(metrics["unfinished_on_network"], 1)
                self.assertEqual(metrics["not_departed"], 1)
                self.assertEqual(metrics["total_in_network_vehicle_time_s"], 1)
                self.assertEqual(metrics["total_departure_wait_time_s"], 1)
                self.assertTrue(metrics["count_conservation"])
                self.assertTrue(metrics["final_summary_consistent"])
            finally:
                shutil.rmtree(run_folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
