# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Meaningful leakage, selection, identifiable-fit and metric tests; offline."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import finalize_data as data
import prepare_calibration as prep
import validate_twin as twin


def protocol():
    return {"bucket_minutes": 5, "windows": {
        "training": {"start": "2026-10-17T14:55:00+08:00", "end": "2026-10-17T15:05:00+08:00", "expected_samples_per_od": 2},
        "same_day_exploratory": {"start": "2026-10-17T15:05:00+08:00", "end": "2026-10-17T15:10:00+08:00", "expected_samples_per_od": 1},
        "cross_day_holdout": {"start": "2026-10-18T11:36:00+08:00", "end": "2026-10-18T11:46:00+08:00", "expected_samples_per_od": 2}}}


def sample(identity, at, duration=100, *, received=None, success=True, path="path", od="od"):
    result = {k: None for k in prep.CSV_FIELDS}
    result.update(sample_id=identity, route_pair_id=od, requested_at=at,
        received_at=received or at, _requested_ts=data.timestamp(at),
        _received_ts=data.timestamp(received or at), api_success=success,
        od_matches_current=True, estimated_duration_s=duration, geometry_signature=path,
        baseline_geometry_signature="path", whether_matches_baseline=path == "path",
        outside_model_bounds=False, _baseline_selected=True, _reasons=[],
        response_sha256="abc")
    return result


def manifest_for(rows):
    config = protocol()
    audit = data.partition_rows(rows, config, ["od"])
    return {"protocol": config, "od_ids": ["od"], "data_audit": audit,
        "baseline_geometry_signatures": {"od": "path"}}


class SelectionTests(unittest.TestCase):
    def test_first_success_selected_and_repeats_retained(self):
        rows = [sample("late", "2026-10-17T14:57:00+08:00"),
                sample("fail", "2026-10-17T14:55:10+08:00", success=False),
                sample("first", "2026-10-17T14:56:00+08:00")]
        audit = manifest_for(rows)["data_audit"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(audit["failure_count"], 1)
        self.assertEqual(audit["duplicate_count"], 1)
        self.assertEqual([r["sample_id"] for r in rows if r["fit_eligible"]], ["first"])
        self.assertEqual(rows[-1]["duplicate_of"], "first")

    def test_missing_duration_first_success_is_not_repaired_by_duplicate(self):
        rows = [sample("missing", "2026-10-17T14:56:00+08:00", None),
                sample("later", "2026-10-17T14:57:00+08:00", 120)]
        manifest_for(rows)
        self.assertFalse(any(r["fit_eligible"] for r in rows))
        self.assertEqual(rows[1]["duplicate_of"], "missing")

    def test_changed_path_is_retained_but_not_fit(self):
        rows = [sample("changed", "2026-10-17T14:56:00+08:00", path="new")]
        audit = manifest_for(rows)["data_audit"]
        self.assertEqual(audit["changed_path_count"], 1)
        self.assertTrue(rows[0]["observation_usable"])
        self.assertFalse(rows[0]["fit_eligible"])

    def test_receipt_crossing_boundary_is_excluded(self):
        rows = [sample("crossing", "2026-10-17T15:04:59+08:00", received="2026-10-17T15:05:01+08:00")]
        manifest_for(rows)
        self.assertFalse(rows[0]["fit_eligible"])
        self.assertIn("response_crosses_partition_boundary", rows[0]["exclusion_reasons"])

    def test_next_day_never_repairs_missing_same_day_slot(self):
        rows = [sample("train", "2026-10-17T14:56:00+08:00"),
                sample("crossday", "2026-10-18T11:36:10+08:00")]
        audit = manifest_for(rows)["data_audit"]
        self.assertEqual(audit["phases"]["training"]["per_od"]["od"]["missing_count"], 1)
        self.assertFalse(audit["phases"]["same_day_exploratory"]["ready"])
        self.assertEqual(rows[1]["phase"], "cross_day_holdout")
        self.assertTrue(rows[1]["evaluation_eligible"])
        self.assertFalse(rows[1]["fit_eligible"])

    def test_cross_day_off_grid_has_two_cadence_slots(self):
        rows = [sample("one", "2026-10-18T11:36:20+08:00"),
                sample("two", "2026-10-18T11:41:20+08:00")]
        audit = manifest_for(rows)["data_audit"]
        self.assertTrue(audit["phases"]["cross_day_holdout"]["ready"])
        self.assertEqual(rows[0]["bucket_start_at"], "2026-10-18T11:35:00+08:00")

    def test_eligible_row_path_and_time_are_rechecked(self):
        rows = [sample("valid", "2026-10-17T14:56:00+08:00")]
        manifest = manifest_for(rows)
        self.assertEqual(len(twin.select_rows(rows, manifest, "training")), 1)
        rows[0]["geometry_signature"] = "tampered"
        with self.assertRaises(ValueError):
            twin.select_rows(rows, manifest, "training")

    def test_overlapping_partition_rejected(self):
        config = protocol()
        config["windows"]["same_day_exploratory"]["start"] = "2026-10-17T15:04:00+08:00"
        with self.assertRaises(ValueError):
            data.partition_rows([], config, ["od"])


class FitTests(unittest.TestCase):
    def test_identifiable_interior_recovers_known_parameters(self):
        result = twin.fit_nonnegative_affine([(10, 35), (20, 65), (30, 95), (40, 125)])
        self.assertAlmostEqual(result["alpha"], 3)
        self.assertAlmostEqual(result["beta_s"], 5)
        self.assertAlmostEqual(result["training_sse_s2"], 0)

    def test_negative_unconstrained_slope_hits_boundary(self):
        result = twin.fit_nonnegative_affine([(10, 90), (20, 80), (30, 70)])
        self.assertEqual(result["alpha"], 0)
        self.assertEqual(result["beta_s"], 80)
        self.assertEqual(result["active_constraint"], "alpha_zero")

    def test_negative_intercept_boundary(self):
        result = twin.fit_nonnegative_affine([(10, 5), (20, 25), (30, 45)])
        self.assertEqual(result["beta_s"], 0)
        self.assertEqual(result["active_constraint"], "beta_zero")

    def test_nonidentifiable_design_is_rejected(self):
        with self.assertRaises(ValueError):
            twin.fit_nonnegative_affine([(10, 25), (10, 35)])

    def test_holdout_cannot_enter_fit_and_model_is_deterministic(self):
        training = [{"phase": "training", "route_pair_id": "a", "estimated_duration_s": 40},
                    {"phase": "training", "route_pair_id": "b", "estimated_duration_s": 70}]
        physical = {"a": 10, "b": 20}
        first = twin.fit_models(training, physical)
        self.assertEqual(data.canonical(first), data.canonical(twin.fit_models(copy.deepcopy(training), physical)))
        holdout = {"phase": "cross_day_holdout", "route_pair_id": "a", "estimated_duration_s": 9999}
        with self.assertRaises(ValueError):
            twin.fit_models(training+[holdout], physical)

    def test_mae_is_pooled_sample_weighted_and_worse_result_retained(self):
        predictions = []
        for od, observed in (("a", 10), ("a", 20), ("b", 60)):
            for method, predicted in (("shared_affine_eta", 100), ("per_od_training_median", 20)):
                predictions.append({"phase": "cross_day_holdout", "route_pair_id": od,
                    "method": method, "observed_platform_eta_s": observed, "predicted_duration_s": predicted})
        metrics = twin.metric_rows(predictions)
        totals = {r["method"]: r for r in metrics if r["route_pair_id"] == "ALL"}
        self.assertAlmostEqual(totals["shared_affine_eta"]["mae_s"], 70)
        self.assertAlmostEqual(totals["per_od_training_median"]["mae_s"], 50/3)
        self.assertGreater(totals["shared_affine_eta"]["mae_s"], totals["per_od_training_median"]["mae_s"])


if __name__ == "__main__":
    unittest.main()
