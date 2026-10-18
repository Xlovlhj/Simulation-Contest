"""Reject survivor bias and verify holdout evaluation never changes fitted state."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_inverse_v2 as calibration


class CalibrationV2Tests(unittest.TestCase):
    def metric(self):
        return {"physics_valid": True, "all_vehicles_arrived": True, "eta_usable": True,
                "per_od": {"a": {"probe": {"comparable_eta_s": 110}},
                           "b": {"probe": {"comparable_eta_s": 180}}}}

    def test_invalid_or_censored_run_cannot_win_objective(self):
        for flag in ("physics_valid", "all_vehicles_arrived", "eta_usable"):
            with self.subTest(flag=flag):
                data = self.metric(); data[flag] = False
                self.assertIsNone(calibration.objective(data, {"a": 100, "b": 200}))

    def test_missing_or_nonfinite_probe_is_rejected(self):
        for value in (None, float("nan"), float("inf")):
            data = self.metric(); data["per_od"]["a"]["probe"]["comparable_eta_s"] = value
            self.assertIsNone(calibration.objective(data, {"a": 100, "b": 200}))

    def test_objective_is_relative_route_error_not_total_demand(self):
        data = self.metric()
        self.assertAlmostEqual(calibration.objective(data, {"a": 100, "b": 200}), .01)

    def test_evaluation_uses_actual_observation_schema(self):
        path = ROOT / "data/processed/identification_v2/routes.csv"
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
        training = [r for r in rows if r["fit_eligible"].lower() == "true"]
        predictions = {od: 500 for od in {r["route_pair_id"] for r in training}}
        result = calibration.evaluate_predictions(training, predictions)
        independently = sum(abs(500 - float(r["route_duration_s"])) for r in training) / len(training)
        self.assertEqual(result["n"], 48)
        self.assertAlmostEqual(result["mae_s"], independently)

    def test_holdout_evaluation_never_refits_or_changes_saved_fit(self):
        observation = ROOT / "data/processed/identification_v2/routes.csv"
        with observation.open() as handle:
            rows = list(csv.DictReader(handle))
        ods = sorted({r["route_pair_id"] for r in rows})
        fixtures = ROOT / "outputs/identification_v2/engine_smoke"
        with tempfile.TemporaryDirectory(prefix="evaluation_test_", dir=fixtures) as tmp:
            root = Path(tmp); out = root / "outputs"
            csv_path = root / "data/processed/identification_v2/routes.csv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_bytes(observation.read_bytes())
            config_path = root / "config.json"
            config = {"holdout_policy": "evaluate only", "target_median_eta_s": {od: 500 for od in ods},
                      "source_sha256": {"data/processed/identification_v2/routes.csv": calibration.sha(csv_path)}}
            calibration.write(config_path, config)
            fitted = {"config_sha256": calibration.sha(config_path),
                      "profiles": [{"id": "fixed", "best": {"objective": .1,
                                    "predictions_s": {od: 600 for od in ods}}}]}
            calibration.write(out / "fit.json", fitted)
            calibration.write(out / "fit_manifest.json", {"artifacts": {"fit.json": calibration.sha(out / "fit.json")}})
            before = (out / "fit.json").read_bytes()
            with mock.patch.object(calibration, "ROOT", root), mock.patch.object(calibration, "OUT", out), \
                 mock.patch.object(calibration, "CONFIG", config_path), \
                 mock.patch.object(calibration.engine, "run_case", side_effect=AssertionError("Evaluation must not simulate/refit")):
                with contextlib.redirect_stdout(io.StringIO()):
                    calibration.evaluate()
                first = calibration.read(out / "evaluation.json")
                # Tampering with holdout inputs must be rejected before a new
                # evaluation. It must never trigger a fit update.
                for row in rows:
                    if row["evaluation_eligible"].lower() == "true":
                        row["route_duration_s"] = str(float(row["route_duration_s"]) + 1000)
                with csv_path.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
                with self.assertRaisesRegex(ValueError, "Frozen evaluation source changed"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        calibration.evaluate()
                second = calibration.read(out / "evaluation.json")
            self.assertEqual((out / "fit.json").read_bytes(), before)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
