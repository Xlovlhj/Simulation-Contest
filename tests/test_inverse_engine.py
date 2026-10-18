"""Meaningful invariants for physical inverse candidates and censoring."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sumo_inverse_engine as engine


class InverseEngineTests(unittest.TestCase):
    def setUp(self):
        self.protocol = engine.normalize_protocol(None)
        self.network, self.demands, _, self.physics = engine.load_inputs(self.protocol)

    def test_fixed_probe_releases_do_not_depend_on_demand(self):
        schedules = []
        for headway in (15, 60, None):
            expanded = engine.expand_parameters({"default_headway_s": headway}, self.protocol,
                                                self.network, self.demands, self.physics)
            items = engine.create_assignments(self.demands, expanded, self.protocol)
            schedules.append([(x["id"], x["depart"], x["route"]) for x in items if x["role"] == "probe"])
        self.assertEqual(schedules[0], schedules[1])
        self.assertEqual(schedules[1], schedules[2])
        self.assertEqual(len(schedules[0]), 18)

    def test_grouped_demand_does_not_add_od_or_modify_probe_count(self):
        ods = sorted(self.demands)
        self.protocol["demand_groups"] = {"group": ods[:2]}
        expanded = engine.expand_parameters({"default_headway_s": None, "group_headways_s": {"group": 30},
                                             "od_headways_s": {ods[0]: 60}}, self.protocol,
                                            self.network, self.demands, self.physics)
        items = engine.create_assignments(self.demands, expanded, self.protocol)
        self.assertEqual(sum(x["role"] == "probe" for x in items), 18)
        self.assertEqual(sum(x["role"] == "background" and x["od_id"] == ods[0] for x in items), 20)
        self.assertEqual(sum(x["role"] == "background" and x["od_id"] == ods[1] for x in items), 40)
        self.assertEqual(len(items), 78)

    def test_unknown_or_overlapping_groups_fail(self):
        edge = self.network["edges"][0]["id"]
        self.protocol["edge_speed_groups"] = {"a": [edge], "b": [edge]}
        with self.assertRaisesRegex(ValueError, "overlap"):
            engine.expand_parameters({}, self.protocol, self.network, self.demands, self.physics)
        self.protocol["edge_speed_groups"] = {"a": [edge]}
        with self.assertRaisesRegex(ValueError, "Unknown speed"):
            engine.expand_parameters({"group_speed_factors": {"missing": .5}}, self.protocol,
                                     self.network, self.demands, self.physics)

    def test_speed_factors_affect_physical_network_inputs(self):
        edge, other = [e["id"] for e in self.network["edges"][:2]]
        self.protocol["edge_speed_groups"] = {"slow": [edge]}
        expanded = engine.expand_parameters({"speed_factor": .8, "group_speed_factors": {"slow": .5}},
                                            self.protocol, self.network, self.demands, self.physics)
        self.assertAlmostEqual(expanded["edge_effective_speed_m_s"][edge], 30 / 3.6 * .4)
        self.assertAlmostEqual(expanded["edge_effective_speed_m_s"][other], 30 / 3.6 * .8)
        with self.assertRaisesRegex(ValueError, "Unknown inverse"):
            engine.expand_parameters({"affine_delay": 400}, self.protocol, self.network, self.demands, self.physics)

    def test_unfinished_and_undeparted_probes_cannot_disappear(self):
        assignments = [{"id": name, "role": "probe", "route": "r", "od_id": "od", "depart": release,
                        "release_s": release} for name, release in (("a", 0), ("b", 20), ("c", 40))]
        trips = [{"id": "a", "depart": "5", "arrival": "35"},
                 {"id": "b", "depart": "30", "arrival": "-1"},
                 {"id": "c", "depart": "-1", "arrival": "-1"}]
        records = engine.trip_records(trips, assignments, 100)
        result = engine.summarize_records(records, 5)
        self.assertEqual((result["planned"], result["arrived"], result["unfinished"], result["not_departed"]), (3, 1, 2, 1))
        self.assertEqual(result["observed_in_network_total_s"], 100)
        self.assertEqual(result["observed_system_total_s"], 175)
        self.assertFalse(result["eta_usable"])
        self.assertIsNone(result["comparable_eta_s"])
        self.assertTrue(result["completed_only_means_are_censored"])

    def test_insertion_wait_is_visible_and_rejects_biased_eta(self):
        assignment = [{"id": "probe", "role": "probe", "route": "r", "od_id": "od", "depart": 50, "release_s": 50}]
        records = engine.trip_records([{"id": "probe", "depart": "70", "arrival": "170"}], assignment, 200)
        result = engine.summarize_records(records, 5)
        self.assertEqual(result["in_network_mean_s"], 100)
        self.assertEqual(result["release_to_arrival_mean_s"], 120)
        self.assertEqual(result["insertion_delay_mean_s"], 20)
        self.assertFalse(result["eta_usable"])
        self.assertEqual(result["source_control_wait_s"], 0)

    def test_invalid_probe_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "remain active"):
            engine.normalize_protocol({"warmup_s": 1200})
        with self.assertRaisesRegex(ValueError, "unique"):
            engine.normalize_protocol({"probe_offsets_s": [0, 0]})

    def test_predeclared_od_probe_offsets_are_preserved(self):
        od = sorted(self.demands)[1]
        self.protocol["probe_od_offsets_s"] = {od: 7}
        expanded = engine.expand_parameters({}, self.protocol, self.network, self.demands, self.physics)
        rows = engine.create_assignments(self.demands, expanded, self.protocol)
        times = [r["release_s"] for r in rows if r["od_id"] == od and r["role"] == "probe"]
        self.assertEqual(times, [607, 667, 727])
        self.protocol["probe_od_offsets_s"] = {od: 1000}
        with self.assertRaisesRegex(ValueError, "precede"):
            engine.expand_parameters({}, self.protocol, self.network, self.demands, self.physics)


if __name__ == "__main__":
    unittest.main()
