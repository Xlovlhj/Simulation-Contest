# AI-assisted: OpenAI Codex, 2026-10-18; team review required.
"""Causal-control, original-release accounting and reproducibility tests."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from scripts import run_final_experiments as final


class FinalExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = final.load_protocol(final.ROOT / "config/final_experiments.json")

    def setUp(self):
        self.control = copy.deepcopy(self.context["config"]["coordination"])
        self.row = {"id": "ordinary", "role": "ordinary", "release_s": 720}
        self.task = {"active": True, "release_s": 720, "remaining_edges": ["shared", "end"]}

    def test_control_requires_published_task_and_current_overlap(self):
        self.assertFalse(final.should_hold(self.row, 719, self.task, self.control, ["shared"]))
        self.assertFalse(final.should_hold(self.row, 720, None, self.control, ["shared"]))
        self.assertTrue(final.should_hold(self.row, 720, self.task, self.control, ["shared"]))
        self.assertFalse(final.should_hold(self.row, 721, self.task, self.control, ["other"]))
        passed = {**self.task, "remaining_edges": ["end"]}
        self.assertFalse(final.should_hold(self.row, 721, passed, self.control, ["shared"]))
        arrived = {**self.task, "active": False}
        self.assertFalse(final.should_hold(self.row, 721, arrived, self.control, ["shared"]))

    def test_no_retroactive_hold_and_no_special_task_performance(self):
        old_request = {**self.row, "release_s": 700}
        self.assertFalse(final.should_hold(old_request, 720, self.task, self.control, ["shared"]))
        emergency = {**self.row, "role": "hospital_task"}
        self.assertFalse(final.should_hold(emergency, 720, self.task, self.control, ["shared"]))
        demand = final.prior.fixed_demand(self.context["ods"], self.context["physics"],
                                          self.context["config"]["hospital_task"], True)
        self.assertEqual({row["vehicle_type"] for row in demand}, {"assumed_car"})

    def test_individual_and_episode_control_limits(self):
        self.assertTrue(final.should_hold(self.row, 764, self.task, self.control, ["shared"]))
        self.assertFalse(final.should_hold(self.row, 765, self.task, self.control, ["shared"]))
        late_request = {**self.row, "release_s": 900}
        self.assertFalse(final.should_hold(late_request, 900, self.task, self.control, ["shared"]))

    def test_route_guidance_does_not_use_future_event_recovery(self):
        config = self.context["config"]
        profile = next(p for p in config["profiles"] if p["id"] == "severe_event")
        event = final.profile_event(config, profile)
        row = {"id": "ordinary", "role": "ordinary", "vehicle_type": "assumed_car",
               "release_s": 1199, "od_id": event["target_od_id"]}
        a = final.choose_route(row, 1199, self.context, event, "A", True)
        b = final.choose_route(row, 1199, self.context, {**event, "end_s": 1700}, "A", True)
        self.assertEqual(a, b)
        with self.assertRaisesRegex(ValueError, "before release"):
            final.choose_route(row, 1198, self.context, event, "A", True)
        hidden = {**event, "published_at_s": 1200}
        c = final.choose_route(row, 1199, self.context, hidden, "A", True)
        self.assertEqual(c["route"], self.context["ods"][row["od_id"]]["default_route_id"])

    def test_all_source_and_insertion_wait_is_charged_from_original_release(self):
        od = "west_to_hospital_road"
        route = self.context["ods"][od]["default_route_id"]
        assignments = [{"id": "ordinary", "role": "ordinary", "od_id": od, "release_s": 0,
                        "depart": 0, "route": route, "source_control_wait_s": 30},
                       {"id": "task", "role": "hospital_task", "od_id": od, "release_s": 20,
                        "depart": 20, "route": route, "source_control_wait_s": 0}]
        trips = [{"id": "ordinary", "depart": "40", "arrival": "100"},
                 {"id": "task", "depart": "25", "arrival": "70"}]
        physics = {**self.context["physics"], "simulation_duration_s": 200}
        combined = final.baseline.compute_metrics(trips, assignments, 200)
        combined.update(physics_valid=True, quality_reasons=[])
        result = {"assignments": assignments, "admissions": {"ordinary": 30, "task": 20}, "submitted_count": 2}
        with tempfile.TemporaryDirectory(prefix="test_accounting_", dir=final.OUT) as temporary:
            folder = Path(temporary)
            root = ET.Element("tripinfos")
            for trip in trips:
                ET.SubElement(root, "tripinfo", trip)
            final.baseline.write_xml(folder / "tripinfo.xml", root)
            with mock.patch.object(final.baseline, "load_metrics", return_value=combined):
                metrics = final.collect_metrics(folder, self.context, physics, result)
        self.assertEqual(metrics["ordinary_system_time_s"] if "ordinary_system_time_s" in metrics else
                         metrics["ordinary"]["total_system_vehicle_time_s"], 100)
        self.assertEqual(metrics["ordinary_source_control_wait_s"], 30)
        self.assertEqual(metrics["ordinary_sumo_insertion_wait_s"], 10)
        self.assertEqual(metrics["ordinary"]["total_departure_wait_time_s"], 40)
        self.assertEqual(metrics["hospital_tasks"][0]["release_to_arrival_s"], 50)

    def test_unfinished_vehicles_and_uninserted_tasks_are_not_removed(self):
        items = [{"id": "a", "route": "r", "depart": 0}, {"id": "b", "route": "r", "depart": 10}]
        metrics = final.baseline.compute_metrics([{"id": "a", "depart": "20", "arrival": "-1"}], items, 100)
        self.assertEqual(metrics["unfinished_total"], 2)
        self.assertEqual(metrics["total_system_vehicle_time_s"], 190)
        task = {"id": "b", "route": "r", "release_s": 10, "role": "hospital_task"}
        report = final.prior.task_metrics([], [task], 100)[0]
        self.assertIsNone(report["release_to_arrival_s"])
        self.assertEqual(report["observed_release_to_end_s"], 90)

    def test_clock_mapping_is_declared_counterfactual(self):
        self.assertEqual(final.wall_time(self.context["config"], 720), "2026-10-18T12:12:00+08:00")
        self.assertIn("counterfactual", self.context["config"]["anchor_status"])

    def test_invalid_physics_has_no_claimed_gain(self):
        fixed = {"case": "fixed", "policy": "fixed", "profile": "p", "scenario_family": "S2",
                 "demand_sha256": "d", "event_sha256": "e", "physics_sha256": "p", "physics_valid": True,
                 "ordinary_system_time_s": 100, "task_completed": None, "task_release_to_arrival_s": None}
        guided = {**fixed, "case": "A", "policy": "A", "ordinary_system_time_s": 1, "physics_valid": False}
        result = final.comparisons([fixed, guided])[0]
        self.assertFalse(result["comparison_valid"])
        self.assertIsNone(result["ordinary_system_time_delta_s"])
        guided["physics_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "inputs differ"):
            final.comparisons([fixed, guided])

    def test_default_help_does_not_start_sumo(self):
        with mock.patch.object(final, "load_protocol", side_effect=AssertionError("must not load")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(final.main([]), 0)


if __name__ == "__main__":
    unittest.main()
