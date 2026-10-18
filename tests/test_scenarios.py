# AI-assisted: OpenAI Codex, 2026-10-17. Team review is required.
"""Offline causal, demand-conservation, timing and comparability invariants."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from scripts import run_scenarios as scenarios

ROOT = scenarios.ROOT


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.protocol, cls.physics, cls.network, cls.demands,
         cls.candidates, cls.hashes) = scenarios.load_inputs(ROOT / "config/scenarios.json")

    def setUp(self):
        self.event = copy.deepcopy(self.protocol["event"])
        self.task = copy.deepcopy(self.protocol["hospital_task"])

    def assign(self, demand, guided=True, event=None, candidates=None):
        return scenarios.assign_routes(demand, self.demands, candidates or self.candidates,
                                       event or self.event, guided, self.network["edges"],
                                       self.physics["speed_limit_kmh"] / 3.6)

    def make_item(self, time, od=None, identity=None):
        return {"id": identity or "v" + str(time), "od_id": od or self.event["target_od_id"],
                "release_s": time, "role": "ordinary", "vehicle_type": "assumed_car"}

    def test_fixed_six_od_demand_does_not_grow_with_candidates(self):
        demand = scenarios.fixed_demand(self.demands, self.physics, self.task, False)
        self.assertEqual(len(demand), 240)
        self.assertEqual(len({d["od_id"] for d in demand}), 6)
        self.assertGreater(len(self.candidates), len(self.demands))
        self.assertEqual(len(scenarios.fixed_demand(self.demands, self.physics, self.task, True)), 241)
        self.assertEqual(len({d["id"] for d in demand}), len(demand))
        self.assertEqual({d["vehicle_type"] for d in demand}, {"assumed_car"})

    def test_primary_cost_keeps_default_and_records_complete_alternative(self):
        item = self.make_item(720)
        assigned = self.assign([item])[0]
        self.assertEqual(assigned["route"], self.demands[item["od_id"]]["default_route_id"])
        self.assertEqual(assigned["reason"], "default_retained_by_current_cost")
        self.assertGreaterEqual(len(assigned["candidate_estimated_cost_s"]), 2)
        self.assertEqual(min(assigned["candidate_estimated_cost_s"].values()),
                         assigned["candidate_estimated_cost_s"][assigned["route"]])
        # A tempting platform ETA from a later collection is not used by policy.
        candidates = copy.deepcopy(self.candidates)
        for c in candidates.values():
            c["platform_eta_samples"] = [{"duration_s": 0.01, "requested_at": "2099-01-01"}]
        self.assertEqual(self.assign([item], candidates=candidates), self.assign([item]))

    def test_severe_case_only_changes_target_during_known_active_interval(self):
        self.event["speed_m_s"] = 0.5
        demand = scenarios.fixed_demand(self.demands, self.physics, self.task, False)
        fixed, guided = self.assign(demand, False), self.assign(demand, True)
        changes = [b for a, b in zip(fixed, guided) if a["route"] != b["route"]]
        self.assertEqual(len(changes), 20)
        self.assertTrue(all(c["od_id"] == self.event["target_od_id"] and 600 <= c["depart"] < 1200 for c in changes))
        for a, b in zip(fixed, guided):
            self.assertEqual({k: a[k] for k in ("id", "od_id", "depart", "role", "vehicle_type")},
                             {k: b[k] for k in ("id", "od_id", "depart", "role", "vehicle_type")})

    def test_no_foreknowledge_of_publication_or_recovery(self):
        self.event.update(speed_m_s=0.5, published_at_s=650)
        items = [self.make_item(t) for t in (599, 600, 649, 650, 1199, 1200)]
        assigned = self.assign(items)
        default = self.demands[self.event["target_od_id"]]["default_route_id"]
        self.assertEqual([r["route"] != default for r in assigned], [False, False, False, True, True, False])
        near_end = self.make_item(1199)
        # The event will soon end, but the route estimate may not exploit that.
        later_end = {**self.event, "end_s": 1700}
        self.assertEqual(self.assign([near_end]), self.assign([near_end], event=later_end))

    def test_future_hospital_task_does_not_change_earlier_background_decisions(self):
        self.event["speed_m_s"] = 0.5
        ordinary = scenarios.fixed_demand(self.demands, self.physics, self.task, False)
        with_task = scenarios.fixed_demand(self.demands, self.physics, self.task, True)
        a = [x for x in self.assign(ordinary) if x["release_s"] < self.task["release_s"]]
        b = [x for x in self.assign(with_task) if x["release_s"] < self.task["release_s"]]
        self.assertEqual(a, b)

    def test_other_departure_edge_is_not_a_free_routing_option(self):
        item = self.make_item(720)
        candidates = copy.deepcopy(self.candidates)
        default = copy.deepcopy(candidates[self.demands[item["od_id"]]["default_route_id"]])
        default["id"] = "unreachable_shortcut"
        default["edge_ids"] = default["edge_ids"][-2:]
        candidates[default["id"]] = default
        self.assertEqual(self.assign([item], candidates=candidates), self.assign([item]))

    def test_equal_cost_keeps_default(self):
        item = self.make_item(720)
        candidates = copy.deepcopy(self.candidates)
        default = copy.deepcopy(candidates[self.demands[item["od_id"]]["default_route_id"]])
        default["id"] = "aaaa_equal_cost_candidate"
        candidates[default["id"]] = default
        chosen = self.assign([item], candidates=candidates)[0]
        self.assertEqual(chosen["route"], self.demands[item["od_id"]]["default_route_id"])

    def test_task_release_time_includes_insertion_wait_and_censoring(self):
        task = {"id": "rescue", "role": "hospital_task", "route": "r", "release_s": 100}
        completed = scenarios.task_metrics([{"id": "rescue", "depart": "150", "arrival": "250"}], [task], 400)[0]
        self.assertEqual(completed["release_to_arrival_s"], 150)
        self.assertEqual(completed["insertion_wait_s"], 50)
        absent = scenarios.task_metrics([], [task], 400)[0]
        self.assertFalse(absent["completed"])
        self.assertIsNone(absent["release_to_arrival_s"])
        self.assertEqual(absent["observed_release_to_end_s"], 300)
        self.assertEqual(absent["insertion_wait_s"], 300)
        driving = scenarios.task_metrics([{"id": "rescue", "depart": "150", "arrival": "-1"}], [task], 400)[0]
        self.assertTrue(driving["censored_at_horizon"])
        self.assertEqual(driving["in_network_time_s"], 250)

    def test_invalid_physics_cannot_produce_comparison_gain(self):
        fixed = {"name": "fixed", "manifest": {"demand_sha256": "same", "event_sha256": "same", "config": {}},
                 "metrics": {"physics_valid": True, "hospital_tasks": [],
                             "ordinary": {"total_system_vehicle_time_s": 100, "unfinished_total": 0}}}
        guided = copy.deepcopy(fixed)
        guided["name"] = "guided"
        guided["metrics"]["physics_valid"] = False
        guided["metrics"]["ordinary"]["total_system_vehicle_time_s"] = 10
        result = scenarios.paired_comparison(fixed, guided)
        self.assertFalse(result["engineering_comparison_valid"])
        self.assertIsNone(result["ordinary_system_time_delta_s"])
        guided["manifest"]["demand_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "Paired demand"):
            scenarios.paired_comparison(fixed, guided)

    def test_source_hash_change_is_detected(self):
        with tempfile.TemporaryDirectory(prefix="test_scenario_hash_", dir=ROOT / "outputs") as temporary:
            path = Path(temporary) / "source.json"
            path.write_text("{}")
            hashes = {str(path.relative_to(ROOT)): scenarios.baseline.sha256(path)}
            scenarios.verify_inputs(hashes)
            path.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, "changed"):
                scenarios.verify_inputs(hashes)

    def test_xml_event_does_not_change_vehicle_type_or_collision_detection(self):
        with tempfile.TemporaryDirectory(prefix="test_scenario_xml_", dir=ROOT / "outputs") as temporary:
            folder = Path(temporary)
            demand = scenarios.fixed_demand(self.demands, self.physics, self.task, True)
            scenarios.write_scenario_inputs(folder, self.network, self.physics, self.assign(demand), self.event)
            routes = ET.parse(folder / "demand.rou.xml").getroot()
            self.assertEqual(len(routes.findall("vType")), 1)
            self.assertEqual({v.get("type") for v in routes.findall("vehicle")}, {"assumed_car"})
            additional = ET.parse(folder / "scenario.add.xml").getroot()
            sign = additional.find("variableSpeedSign")
            self.assertEqual(sign.get("lanes").split(), [e + "_0" for e in self.event["edge_ids"]])
            self.assertEqual([(float(s.get("time")), float(s.get("speed"))) for s in sign.findall("step")],
                             [(0, 30 / 3.6), (600, 1), (1200, 30 / 3.6)])
            cfg = ET.parse(folder / "baseline.sumocfg").getroot()
            self.assertEqual(cfg.find("processing/collision.action").get("value"), "warn")
            self.assertEqual(cfg.find("processing/collision.check-junctions").get("value"), "true")
            self.assertEqual(cfg.find("processing/time-to-teleport").get("value"), "-1")

    def test_default_help_does_not_load_or_run(self):
        with mock.patch.object(scenarios, "load_inputs", side_effect=AssertionError("must not load")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scenarios.main([]), 0)


if __name__ == "__main__":
    unittest.main()
