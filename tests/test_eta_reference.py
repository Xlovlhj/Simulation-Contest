"""Synthetic fixtures test freezing and temporal separation, never real accuracy.

AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date was
not provided; implementation and research interpretation await team review.
"""
import contextlib
from copy import deepcopy
import csv
from datetime import datetime, timedelta
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fit_eta_reference", ROOT / "scripts/fit_eta_reference.py")
eta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eta)


class EtaReferenceTests(unittest.TestCase):
    def setUp(self):
        start = datetime.fromisoformat("2026-10-17T15:00:00+08:00")
        self.rows = []
        for identity, durations in (("a", [10, 30, 30, 50]), ("b", [100, 120, 90, 130])):
            for index, duration in enumerate(durations):
                requested = start + timedelta(minutes=index * 5, seconds=1)
                phase = "calibration" if index < 2 else "validation"
                self.rows.append({"sample_id": f"fixture/{identity}/{index}", "route_pair_id": identity,
                                  "requested_at": requested.isoformat(), "received_at": (requested + timedelta(seconds=1)).isoformat(),
                                  "bucket_start_at": (start + timedelta(minutes=index * 5)).isoformat(), "phase": phase,
                                  "fit_eligible": phase == "calibration", "validation_eligible": phase == "validation",
                                  "api_success": True, "od_matches_current": True, "selected_in_bucket": True,
                                  "baseline_comparison_eligible": True, "whether_matches_baseline": True,
                                  "outside_model_bounds": False, "duplicate_of": "", "estimated_duration_s": duration,
                                  "geometry_signature": identity * 64, "baseline_geometry_signature": identity * 64})
        part = {"required_samples": 2, "available_samples": 2, "missing_samples": 0, "ready": True}
        self.report = {"schema_version": 1, "calibration_ready": True, "ready": True,
                       "study_area_id": "synthetic_fixture", "coordinate_system": "bd09ll",
                       "thresholds": {"bucket_minutes": 5, "calibration_minutes": 10, "validation_minutes": 10,
                                      "required_fixed_od_count": 2, "required_calibration_samples_per_od": 2,
                                      "required_validation_samples_per_od": 2},
                       "windows": {"start_at": start.isoformat(), "calibration_end_at": (start + timedelta(minutes=10)).isoformat(),
                                   "validation_end_at": (start + timedelta(minutes=20)).isoformat()},
                       "per_od": {identity: {"calibration": deepcopy(part), "validation": deepcopy(part)} for identity in ("a", "b")}}
        self.provenance = {"samples_sha256": "synthetic_fixture"}

    def fit(self):
        return eta.fit_reference(self.rows, self.report, self.provenance, "fixture_model")

    def write_snapshot(self, folder, rows=None, report=None):
        rows, report = rows or self.rows, deepcopy(report or self.report)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(self.rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        payload = buffer.getvalue().encode()
        report["samples_sha256"] = eta.sha(payload)
        samples, readiness = folder / "samples.csv", folder / "readiness.json"
        samples.write_bytes(payload)
        readiness.write_text(json.dumps(report))
        return samples, readiness

    def test_median_uses_only_training_and_ignores_validation_values(self):
        model = self.fit()
        self.assertEqual(model["predictions_by_od"]["a"]["predicted_duration_s"], 20)
        self.assertEqual(model["predictions_by_od"]["b"]["predicted_duration_s"], 110)
        for row in self.rows:
            if row["phase"] == "validation":
                row["estimated_duration_s"] = 1000000
        other = self.fit()
        self.assertEqual(model["predictions_by_od"], other["predictions_by_od"])
        self.assertEqual(model["training_samples_sha256"], other["training_samples_sha256"])
        self.assertEqual(len(model["training_sample_ids"]), 4)
        self.assertFalse(model["validation_used_for_fit"])

    def test_evaluation_mae_and_counts_use_frozen_predictions(self):
        model = self.fit()
        before = deepcopy(model)
        result = eta.evaluate_reference(model, self.rows, self.report, self.provenance)
        self.assertEqual(result["overall"]["sample_count"], 4)
        self.assertEqual(result["overall"]["mae_s"], 20)
        self.assertEqual(result["per_od"]["a"]["sample_count"], 2)
        self.assertEqual(result["per_od"]["b"]["mae_s"], 20)
        self.assertEqual(model, before)
        self.assertFalse(result["traffic_model_validated"])

    def test_calibration_gate_blocks_without_fitting_or_output_creation(self):
        self.report["calibration_ready"] = False
        self.report["per_od"]["a"]["calibration"].update(ready=False, missing_samples=1, available_samples=1)
        with tempfile.TemporaryDirectory(prefix="eta_reference_test_", dir=ROOT / "tests") as temporary:
            folder = Path(temporary)
            samples, readiness = self.write_snapshot(folder)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = eta.main(["--fit", "--samples", str(samples), "--readiness", str(readiness),
                                   "--output-root", str(folder / "output")])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(output.getvalue())["missing_by_od"]["a"]["missing"], 1)
            self.assertFalse((folder / "output").exists())

    def test_validation_gate_blocks_even_if_training_is_ready(self):
        model = self.fit()
        self.report["ready"] = False
        with self.assertRaises(eta.InsufficientData) as caught:
            eta.evaluate_reference(model, self.rows, self.report, self.provenance)
        self.assertEqual(caught.exception.details["action"], "evaluate")

    def test_validation_row_cannot_be_marked_as_training(self):
        self.rows[2]["fit_eligible"] = True
        with self.assertRaisesRegex(ValueError, "leak"):
            self.fit()

    def test_changed_training_snapshot_is_rejected_during_evaluation(self):
        model = self.fit()
        self.rows[0]["estimated_duration_s"] = 11
        with self.assertRaisesRegex(ValueError, "Training samples changed"):
            eta.evaluate_reference(model, self.rows, self.report, self.provenance)

    def test_changed_path_and_shifted_window_are_rejected(self):
        model = self.fit()
        self.rows[2]["geometry_signature"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed or missing path"):
            eta.evaluate_reference(model, self.rows, self.report, self.provenance)
        self.rows[2]["geometry_signature"] = self.rows[2]["baseline_geometry_signature"]
        self.report["windows"]["start_at"] = "2026-10-17T14:55:00+08:00"
        with self.assertRaisesRegex(ValueError, "duration differs"):
            eta.evaluate_reference(model, self.rows, self.report, self.provenance)

    def test_missing_duration_and_duplicate_bucket_are_rejected(self):
        self.rows[0]["estimated_duration_s"] = ""
        with self.assertRaises(ValueError):
            self.fit()
        self.rows[0]["estimated_duration_s"] = 10
        self.rows[1].update(requested_at=self.rows[0]["requested_at"], received_at=self.rows[0]["received_at"],
                            bucket_start_at=self.rows[0]["bucket_start_at"])
        with self.assertRaisesRegex(ValueError, "same OD/time bucket"):
            self.fit()

    def test_csv_report_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="eta_reference_test_", dir=ROOT / "tests") as temporary:
            samples, readiness = self.write_snapshot(Path(temporary))
            samples.write_text(samples.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "matching snapshot"):
                eta.read_snapshot(samples, readiness)

    def test_artifacts_are_numbered_readonly_on_evaluation_and_hash_checked(self):
        with tempfile.TemporaryDirectory(prefix="eta_reference_test_", dir=ROOT / "tests") as temporary:
            folder = Path(temporary)
            samples, readiness = self.write_snapshot(folder)
            common = ["--samples", str(samples), "--readiness", str(readiness), "--output-root", str(folder / "outputs")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(eta.main(["--fit", "--run-id", "model_one", *common]), 0)
            model_path = folder / "outputs/models/model_one/model.json"
            before = model_path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(eta.main(["--evaluate", "--model", str(model_path), "--run-id", "evaluation_one", *common]), 0)
            self.assertEqual(model_path.read_bytes(), before)
            result = json.loads((folder / "outputs/evaluations/evaluation_one/evaluation.json").read_text())
            self.assertEqual(result["frozen_model_sha256"], eta.sha(before))
            self.assertEqual(result["overall"]["mae_s"], 20)
            with self.assertRaises(FileExistsError):
                eta.save_artifact(model_path.parent, "model.json", self.fit(), "fit")
            model_path.write_bytes(before + b"\n")
            with self.assertRaisesRegex(ValueError, "manifest hash"):
                eta.load_model(model_path)

    def test_no_action_shows_help_without_reading_input_files(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = eta.main(["--samples", "config/nonexistent.csv", "--readiness", "config/nonexistent.json"])
        self.assertEqual(status, 0)
        self.assertIn("--fit", output.getvalue())


if __name__ == "__main__":
    unittest.main()
