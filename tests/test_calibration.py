"""Offline checks of data eligibility, path identity and temporal holdout.

AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date was
not provided; implementation and research interpretation await team review.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_calibration", ROOT / "scripts/prepare_calibration.py")
calibration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calibration)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.fromisoformat("2026-10-17T15:00:00+08:00")
        self.config = {"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll",
                       "route_pairs": [{"id": "od", "origin": [31, 121], "destination": [31, 121.01]}]}
        self.survey = {"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll",
                       "model_bounds": {"west": 120, "east": 122, "south": 30, "north": 32},
                       "routes": [{"id": "od", "origin": [31, 121], "destination": [31, 121.01],
                                   "accepted_surface": True, "use_for_baseline": True,
                                   "points": [[121, 31], [121.01, 31]]}]}
        self.reference = calibration.prepare_reference(self.config, self.survey)

    def sample(self, minute, identity=None, duration=60, status=0, path="121,31;121.01,31", receive_delay=1):
        requested = self.start + timedelta(minutes=minute)
        record = {"route_pair_id": "od", "study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll",
                  "params": {"origin": "31,121", "destination": "31,121.01"},
                  "http_status": 200, "api_status": status, "requested_at": requested.isoformat(),
                  "received_at": (requested + timedelta(seconds=receive_delay)).isoformat()}
        route = {"distance": 100, "steps": [{"path": path}]}
        if duration is not None:
            route["duration"] = duration
        payload = {"status": status, "result": {"routes": [route]}}
        return calibration.parse_sample(record, payload, self.reference, identity or f"collection_test/{minute}")

    def test_rounding_and_consecutive_duplicate_geometry_identity(self):
        points = calibration.normalized_points([[121, 31], [121.00000001, 31], [121.01, 31]])
        self.assertEqual(len(points), 2)
        self.assertEqual(calibration.geometry_signature(points), self.reference["pairs"]["od"]["signature"])
        self.assertNotEqual(calibration.geometry_signature(list(reversed(points))), self.reference["pairs"]["od"]["signature"])

    def test_duplicates_across_runs_select_only_first_success_in_bucket(self):
        rows = [self.sample(2, "collection_later/1"), self.sample(0, "collection_earlier/1"),
                self.sample(1, "collection_failure/1", status=302)]
        report = calibration.assess(rows, self.reference)
        self.assertEqual(report["selected_success_count"], 1)
        self.assertEqual(report["duplicate_count"], 1)
        selected = [row for row in rows if row["selected_in_bucket"]]
        self.assertEqual(selected[0]["sample_id"], "collection_earlier/1")
        self.assertEqual(next(row for row in rows if row["sample_id"] == "collection_later/1")["duplicate_of"], "collection_earlier/1")
        self.assertFalse(report["ready"])

    def test_failed_request_does_not_reserve_success_bucket(self):
        rows = [self.sample(0, status=302), self.sample(1)]
        calibration.assess(rows, self.reference)
        self.assertFalse(rows[0]["fit_eligible"])
        self.assertTrue(rows[1]["fit_eligible"])

    def test_missing_duration_success_is_not_replaced_or_zero_filled(self):
        rows = [self.sample(0, duration=None), self.sample(1, duration=20)]
        calibration.assess(rows, self.reference)
        self.assertIsNone(rows[0]["estimated_duration_s"])
        self.assertTrue(rows[0]["selected_in_bucket"])
        self.assertFalse(any(row["fit_eligible"] for row in rows))
        self.assertEqual(rows[1]["duplicate_of"], rows[0]["sample_id"])

    def test_exact_split_boundary_cannot_leak_validation_into_fit(self):
        rows = [self.sample(0), self.sample(59), self.sample(60), self.sample(89), self.sample(90)]
        calibration.assess(rows, self.reference)
        self.assertEqual([row["phase"] for row in rows], ["calibration", "calibration", "validation", "validation", "later_holdout"])
        fitting = {row["sample_id"] for row in rows if row["fit_eligible"]}
        validation = {row["sample_id"] for row in rows if row["validation_eligible"]}
        self.assertEqual(len(fitting), 2)
        self.assertEqual(len(validation), 2)
        self.assertFalse(fitting & validation)
        self.assertFalse(rows[-1]["fit_eligible"])
        self.assertFalse(rows[-1]["validation_eligible"])

    def test_changed_path_forms_group_but_is_never_compared_to_old_model(self):
        rows = [self.sample(0), self.sample(5, path="121,31;121.005,31.001;121.01,31")]
        report = calibration.assess(rows, self.reference)
        self.assertTrue(rows[1]["observation_usable"])
        self.assertFalse(rows[1]["whether_matches_baseline"])
        self.assertFalse(rows[1]["fit_eligible"])
        self.assertEqual(report["per_od"]["od"]["path_group_count"], 2)
        self.assertEqual(report["per_od"]["od"]["baseline_compatible_sample_count"], 1)

    def test_response_arriving_after_split_boundary_is_purged(self):
        rows = [self.sample(0), self.sample(59, receive_delay=61), self.sample(60)]
        report = calibration.assess(rows, self.reference)
        self.assertFalse(rows[1]["fit_eligible"])
        self.assertFalse(rows[1]["validation_eligible"])
        self.assertIn("response_crosses_partition_boundary", rows[1]["exclusion_reasons"])
        self.assertEqual(report["per_od"]["od"]["calibration"]["available_samples"], 1)
        self.assertTrue(rows[2]["validation_eligible"])

    def test_outside_path_and_failed_response_do_not_enter_fitting(self):
        rows = [self.sample(0, path="121,31;122.1,31"), self.sample(5, status=302)]
        report = calibration.assess(rows, self.reference)
        self.assertTrue(rows[0]["outside_model_bounds"])
        self.assertFalse(any(row["fit_eligible"] for row in rows))
        self.assertEqual(report["per_od"]["od"]["outside_model_bounds_record_count"], 1)

    def test_empty_records_are_insufficient_not_zero_measurements(self):
        report = calibration.assess([], self.reference)
        self.assertFalse(report["ready"])
        self.assertIsNone(report["windows"]["start_at"])
        self.assertEqual(report["missing_samples"], {"calibration": 12, "validation": 6})
        self.assertIsNone(report["per_od"]["od"]["valid_sample_span_minutes"])

    def test_gate_requires_every_bucket_and_full_elapsed_window(self):
        rows = [self.sample(minute) for minute in range(0, 90, 5)]
        first = calibration.assess(deepcopy(rows), self.reference)
        self.assertFalse(first["ready"])
        self.assertEqual(first["missing_samples"], {"calibration": 0, "validation": 0})
        rows.append(self.sample(90))
        completed = calibration.assess(rows, self.reference)
        self.assertTrue(completed["ready"])
        self.assertFalse(completed["model_fitted"])
        self.assertFalse(completed["model_validated"])

    def test_missing_geometry_or_receive_timestamp_does_not_enter_fit(self):
        rows = [self.sample(0, path=""), self.sample(5)]
        rows[1]["_received_ts"] = None
        report = calibration.assess(rows, self.reference)
        self.assertFalse(any(row["fit_eligible"] for row in rows))
        self.assertEqual(report["per_od"]["od"]["valid_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
