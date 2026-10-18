"""Spatial, causal, paired-state, and censoring protections for governance V2."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from scripts import run_governance_v2 as gov


class GovernanceV2Tests(unittest.TestCase):
    def setUp(self):
        self.context = {"ods": {"od": {"default_route_id": "default"}},
                        "candidates": {
                            "default": {"id": "default", "demand_od_id": "od", "edge_ids": ["s", "a", "e"]},
                            "alternate": {"id": "alternate", "demand_od_id": "od", "edge_ids": ["s", "b", "e"]}},
                        "network": {"edges": [{"id": e, "length_m": length} for e, length in
                                                (("s", 10), ("a", 100), ("b", 150), ("e", 10))]}}
        self.expanded = {"edge_effective_speed_m_s": {"s": 1, "a": 1, "b": 10, "e": 1}}
        self.event = {"enabled": True, "published_at_s": 1800, "begin_s": 1800, "end_s": 2400,
                      "speed_multiplier": .8, "edge_ids": ["a"], "target_od_id": "od"}
        self.vehicle = {"id": "car", "role": "ordinary", "release_s": 1900, "od_id": "od"}

    def test_a_uses_fitted_heterogeneous_speed_not_uniform_base(self):
        result = gov.choose_route(self.vehicle, 1900, self.context, self.expanded, self.event, "A")
        self.assertEqual(result["route"], "alternate")
        self.assertAlmostEqual(result["candidate_estimated_cost_s"]["default"], 145.)
        self.assertAlmostEqual(result["candidate_estimated_cost_s"]["alternate"], 35.)

    def test_no_pre_event_or_hidden_intervention(self):
        before = {**self.vehicle, "release_s": 1799}
        self.assertEqual(gov.choose_route(before, 1799, self.context, self.expanded, self.event, "A")["route"], "default")
        hidden = {**self.event, "published_at_s": 1901}
        self.assertEqual(gov.choose_route(self.vehicle, 1900, self.context, self.expanded, hidden, "A")["route"], "default")
        with self.assertRaises(ValueError):
            gov.choose_route(self.vehicle, 1899, self.context, self.expanded, self.event, "A")

    def test_a_does_not_predict_future_recovery(self):
        short = gov.choose_route(self.vehicle, 1900, self.context, self.expanded, {**self.event, "end_s": 1901}, "A")
        long = gov.choose_route(self.vehicle, 1900, self.context, self.expanded, {**self.event, "end_s": 4000}, "A")
        self.assertEqual(short, long)

    def test_event_does_not_overwrite_calibrated_absolute_speeds(self):
        lengths = {"a": 100, "b": 100}
        speeds = {"a": 2., "b": 5.}
        self.assertEqual(gov.route_cost(["a", "b"], lengths, speeds, self.event, 1900), 82.5)
        self.assertEqual(gov.route_cost(["a", "b"], lengths, speeds, self.event, 1799), 70.)

    def test_alias_expands_complete_observed_geometry(self):
        catalog = {"network": {"edges": [{"id": "a", "from": "n0", "to": "n1"},
                                            {"id": "b", "from": "n1", "to": "n2"},
                                            {"id": "c", "from": "n2", "to": "n3"}]},
                   "old_edge_to_canonical_chain": {"old": ["a", "b"]}}
        self.assertEqual(gov.resolve_event_edges(["old", "c"], catalog), ["a", "b", "c"])
        with self.assertRaises(ValueError):
            gov.resolve_event_edges(["c", "old"], catalog)
        with self.assertRaises(ValueError):
            gov.resolve_event_edges(["unknown"], catalog)

    def test_staggered_background_and_single_task_preserve_demand(self):
        context = {"ods": {"x": {}, "y": {}}, "config": {"hospital_task": {"id": "task", "od_id": "x", "release_s": 1920}}}
        expanded = {"od_headways_s": {"x": 60, "y": 60}, "background_start_offsets_s": {"x": 0, "y": 3}}
        ordinary = gov.make_demand(context, expanded, False)
        with_task = gov.make_demand(context, expanded, True)
        self.assertEqual(len(ordinary), 100)
        self.assertEqual(len(with_task), 101)
        self.assertEqual(ordinary, [r for r in with_task if r["role"] == "ordinary"])
        self.assertEqual([r["release_s"] for r in ordinary[:2]], [0, 3])
        self.assertTrue(all(r["release_s"] < 3000 for r in ordinary))

    def test_b_is_task_causal_and_bounded(self):
        row = {**self.vehicle, "release_s": 1920}
        task = {"active": True, "release_s": 1920, "remaining_edges": ["a"]}
        self.assertFalse(gov.original.should_hold(row, 1919, task, gov.CONTROL, ["a"]))
        self.assertTrue(gov.original.should_hold(row, 1964, task, gov.CONTROL, ["a"]))
        self.assertFalse(gov.original.should_hold(row, 1965, task, gov.CONTROL, ["a"]))
        self.assertFalse(gov.original.should_hold({**row, "release_s": 1919}, 1920, task, gov.CONTROL, ["a"]))
        self.assertFalse(gov.original.should_hold({**row, "release_s": 2100}, 2100, task, gov.CONTROL, ["a"]))

    def pair(self):
        fixed = {"profile": "p", "preset": "q", "scenario_family": "S3", "policy": "fixed",
                 "demand_sha256": "d", "event_sha256": "e", "physics_sha256": "p",
                 "trajectory_prefixes": {"before_event": "same", "before_task": "same"},
                 "profile_passes_training_screen": True,
                 "comparison_eligible": True, "ordinary_system_time_s": 100.,
                 "ordinary_post_event_system_time_s": 60., "task_completed": True, "task_release_to_arrival_s": 50.}
        guided = {**fixed, "policy": "A", "ordinary_system_time_s": 90., "task_release_to_arrival_s": 40.}
        return fixed, guided

    def test_invalid_or_censored_pair_has_no_claimed_gain(self):
        fixed, guided = self.pair()
        guided["comparison_eligible"] = False
        row = gov.comparisons([fixed, guided])[0]
        self.assertFalse(row["comparison_valid"])
        self.assertIsNone(row["ordinary_system_time_delta_s"])
        self.assertIsNone(row["task_time_delta_s"])

    def test_paired_inputs_and_preintervention_state_must_match(self):
        fixed, guided = self.pair()
        self.assertEqual(gov.comparisons([fixed, guided])[0]["ordinary_system_time_delta_s"], -10.)
        with self.assertRaises(ValueError):
            gov.comparisons([fixed, {**guided, "demand_sha256": "changed"}])
        with self.assertRaises(ValueError):
            gov.comparisons([fixed, {**guided, "trajectory_prefixes": {"before_event": "changed", "before_task": "same"}}])
        b = {**guided, "policy": "B", "trajectory_prefixes": {"before_event": "same", "before_task": "changed"}}
        with self.assertRaises(ValueError):
            gov.comparisons([fixed, guided, b])

    def test_physically_complete_but_unfitted_profile_is_only_diagnostic(self):
        fixed, guided = self.pair()
        for row in (fixed, guided):
            row["profile_passes_training_screen"] = False
        comparison = gov.comparisons([fixed, guided])[0]
        self.assertTrue(comparison["comparison_valid"])
        self.assertFalse(comparison["eligible_for_fitted_scenario_conclusion"])
        self.assertEqual(comparison["ordinary_system_time_delta_s"], -10.)

    def test_waiting_and_unfinished_outcomes_stay_in_accounting(self):
        assignments = [{"id": "a", "route": "r", "depart": 1800, "release_s": 1800, "role": "ordinary"},
                       {"id": "b", "route": "r", "depart": 1810, "release_s": 1810, "role": "ordinary"}]
        trips = [{"id": "a", "depart": "1840", "arrival": "2000"}]
        metrics = gov.baseline.compute_metrics(trips, assignments, 6000)
        self.assertEqual(metrics["total_system_vehicle_time_s"], 200 + 4190)
        self.assertEqual(metrics["unfinished_total"], 1)
        outcomes = gov.engine.trip_records(trips, assignments, 6000)
        self.assertEqual(outcomes[0]["insertion_delay_s"], 40)
        self.assertTrue(outcomes[1]["observed_delay_is_lower_bound"])

    def test_individual_distribution_does_not_hide_losers_or_censoring(self):
        base = [dict(id=identity, role="ordinary", completed=True, release_s=0, od_id="od", release_to_arrival_s=100.) for identity in ("a", "b", "c")]
        changed = [{**r, "release_to_arrival_s": duration} for r, duration in zip(base, (80., 110., 100.))]
        effect = gov.individual_effect(base, changed)
        self.assertEqual((effect["benefited"], effect["harmed"], effect["unchanged"]), (1, 1, 1))
        self.assertEqual(effect["total_system_time_delta_s"], -10.)
        self.assertEqual(effect["maximum_extra_time_s"], 10.)
        changed[0]["completed"] = False
        self.assertFalse(gov.individual_effect(base, changed)["comparison_valid"])


if __name__ == "__main__":
    unittest.main()
