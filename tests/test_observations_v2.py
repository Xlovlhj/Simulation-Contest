"""Meaningful safeguards for the spatial/temporal interpretation of observations."""
from pathlib import Path
import csv
import hashlib
import io
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_observations_v2 as obs


class ObservationUnitTests(unittest.TestCase):
    def test_window_uses_receipt_and_half_open_end(self):
        windows = {"training": {"start": "2026-10-17T14:55:00+08:00", "end": "2026-10-17T15:55:00+08:00"}}
        self.assertEqual(obs.partition("2026-10-17T15:54:59+08:00", "2026-10-17T15:54:59.9+08:00", windows), "training")
        self.assertEqual(obs.partition("2026-10-17T15:54:59+08:00", "2026-10-17T15:55:00+08:00", windows), "outside_declared_windows")
        with self.assertRaises(ValueError):
            obs.partition("2026-10-17T15:54:59+08:00", "2026-10-17T15:54:58+08:00", windows)

    def test_causal_join_rejects_future_and_other_phase(self):
        snapshots = [dict(sample_id="ok", phase="training", received_at="2026-10-17T15:00:00+08:00"),
                     dict(sample_id="future", phase="training", received_at="2026-10-17T15:00:02+08:00"),
                     dict(sample_id="other", phase="holdout", received_at="2026-10-17T15:00:00.9+08:00")]
        match = obs.preceding_traffic("2026-10-17T15:00:01+08:00", "training", snapshots)
        self.assertEqual(match, dict(preceding_traffic_sample_id="ok", preceding_traffic_age_s=1.0))

    def test_geometry_match_is_directional_not_nearest_road(self):
        lookup = {((121.0, 31.0), (121.001, 31.0)): dict(id="forward", length_m=100)}
        self.assertTrue(obs.match_step("121,31;121.001,31", lookup)["exact_directed_geometry_match"])
        self.assertFalse(obs.match_step("121.001,31;121,31", lookup)["exact_directed_geometry_match"])
        self.assertFalse(obs.match_step("121,31;121.001,31.0001", lookup)["exact_directed_geometry_match"])

    def test_range_fitting_excludes_holdout_and_duplicate(self):
        base = dict(route_pair_id="OD", phase="training", fit_eligible=True, sample_id="a", route_duration_s=10., route_distance_m=100., route_mean_speed_kmh=20., requested_at="2026-10-17T15:00:00+08:00")
        rows = [base, {**base, "sample_id": "held", "phase": "cross_day_holdout", "route_duration_s": 10000.},
                {**base, "sample_id": "duplicate", "fit_eligible": False, "route_duration_s": 9999.}]
        result = obs.training_summaries(rows, [], [])
        self.assertEqual(result["route_eta"]["OD"]["eta_s"]["count"], 1)
        self.assertEqual(result["route_eta"]["OD"]["eta_s"]["median"], 10.)

    def test_numeric_measurement_and_quantile(self):
        for value in (None, True, float("nan"), float("inf"), -1, 0):
            with self.assertRaises((ValueError, TypeError)):
                obs.number(value)
        self.assertEqual(obs.quantile([1., 3.], .25), 1.5)


class ObservationArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / "data/processed/identification_v2"
        cls.audit = json.loads((cls.folder / "audit.json").read_text())
        cls.routes = list(csv.DictReader(io.StringIO((cls.folder / "routes.csv").read_text())))
        cls.steps = list(csv.DictReader(io.StringIO((cls.folder / "route_steps.csv").read_text())))

    def test_actual_counts_and_partitions(self):
        self.assertEqual(self.audit["counts"]["traffic_snapshots"], 100)
        self.assertEqual(self.audit["counts"]["raw_direction_responses"], 164)
        self.assertEqual(self.audit["counts"]["raw_route_steps"], 615)
        self.assertEqual([self.audit["phases"][p]["eligible_direction_responses"] for p in
                          ("training", "same_day_exploratory", "cross_day_holdout")], [48, 16, 96])
        self.assertEqual(sum(obs.flag(s["fit_eligible"]) for s in self.steps), 180)

    def test_route_steps_are_exact_and_preserve_residual(self):
        self.assertTrue(all(obs.flag(s["exact_directed_geometry_match"]) for s in self.steps))
        self.assertTrue(all(float(r["route_minus_step_distance_m"]) == 0 for r in self.routes))
        self.assertEqual(max(float(r["route_minus_step_duration_s"]) for r in self.routes), 4.)
        self.assertTrue(any(float(s["step_distance_m"]) <= 5 and float(s["step_duration_s"]) >= 17 for s in self.steps))

    def test_traffic_speed_not_misrepresented_as_local_limit(self):
        sections = list(csv.DictReader(io.StringIO((self.folder / "traffic_sections.csv").read_text())))
        self.assertEqual(len(sections), 364)
        self.assertTrue(all(not obs.flag(s["exact_model_edge_mapping_available"]) for s in sections))
        ruijin = self.audit["ruijin_road_speed_mapping"]
        self.assertEqual(ruijin["snapshots_with_speed"], 42)
        self.assertFalse(ruijin["exact_spatial_match"])
        self.assertGreater(len(ruijin["descriptions_and_lengths"]), 1)

    def test_manifest_source_and_artifact_hashes(self):
        manifest = json.loads((self.folder / "manifest.json").read_text())
        for source in manifest["source_files"]:
            self.assertFalse(source["path"].endswith(".local.json"))
            self.assertEqual(hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(), source["sha256"])
        for name, entry in manifest["artifacts"].items():
            self.assertEqual(hashlib.sha256((self.folder / name).read_bytes()).hexdigest(), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
