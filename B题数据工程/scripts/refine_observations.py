"""Estimate 15-minute flow with aligned lanes, uncertainty and masking checks.

All uncertainty intervals are conditional on observed samples. They do not
establish that unavailable periods have the same distribution.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from download_sources import ROOT

OUT = ROOT / "refined"
SEED = 20261017


def bootstrap_rate(times, volumes, seconds=30, repetitions=300):
    # Resample observed one-minute blocks jointly across lanes.
    block = pd.DatetimeIndex(times).floor("1min")
    codes, labels = pd.factorize(block)
    sums = np.bincount(codes, weights=volumes)
    sizes = np.bincount(codes)
    rng = np.random.default_rng(SEED + len(volumes))
    indices = rng.integers(0, len(labels), size=(repetitions, len(labels)))
    sample = sums[indices].sum(axis=1) / (seconds * sizes[indices].sum(axis=1)) * 3600
    return np.percentile(sample, [2.5, 97.5])


def main():
    OUT.mkdir(exist_ok=True)
    data = pd.read_csv(ROOT / "processed/traffic_lane_30s.csv.gz")
    data["time"] = pd.to_datetime(data.period_from)
    data["valid"] = ((data.valid_flag == "Y") & data.vehicle_count.ge(0)
        & data.speed_kmh.ge(0) & data.occupancy_pct.between(0, 100) & data.interval_seconds.eq(30))
    rows, station_records, masking = [], [], []
    rng = np.random.default_rng(SEED)
    for did, group in data.groupby("detector_id"):
        lanes = sorted(group.lane_id.unique())
        count = group.pivot(index="time", columns="lane_id", values="vehicle_count").reindex(columns=lanes)
        flags = group.pivot(index="time", columns="lane_id", values="valid").reindex(columns=lanes)
        speed = group.pivot(index="time", columns="lane_id", values="speed_kmh").reindex(columns=lanes)
        occ = group.pivot(index="time", columns="lane_id", values="occupancy_pct").reindex(columns=lanes)
        complete = flags.fillna(False).all(axis=1) & count.notna().all(axis=1)
        # All lanes must be available simultaneously; no lane-count scaling.
        s = pd.DataFrame({"time": count.index, "complete": complete,
            "volume": count.sum(axis=1, min_count=len(lanes)),
            "weighted_speed_numerator": (speed * count).sum(axis=1, min_count=len(lanes)),
            "occupancy_pct": occ.mean(axis=1)}).reset_index(drop=True)
        s["detector_id"] = did
        s["window"] = s.time.dt.floor("15min")
        station_records.append(s)
        start = pd.Timestamp("2026-10-15")
        stop = pd.Timestamp("2026-10-17")
        for window in pd.date_range(start, stop, freq="15min", inclusive="left"):
            g = s[(s.time >= window) & (s.time < window + pd.Timedelta(minutes=15))]
            v = g[g.complete]
            n = len(v)
            coverage = n / 30
            observed = float(v.volume.sum()) if n else np.nan
            q = observed * 120 / n if n else np.nan
            speed_value = float(v.weighted_speed_numerator.sum() / observed) if observed > 0 else np.nan
            lo, hi = bootstrap_rate(v.time, v.volume) if n >= 10 else (np.nan, np.nan)
            observed_slots = set(v.time)
            missing_run, max_missing = 0, 0
            for t in pd.date_range(window, periods=30, freq="30s"):
                missing_run = 0 if t in observed_slots else missing_run + 1
                max_missing = max(max_missing, missing_run)
            rows.append({"detector_id": did, "window": window.isoformat(), "date": window.strftime("%Y-%m-%d"),
                "lane_labels": len(lanes), "complete_intervals": n, "expected_intervals": 30,
                "coverage": coverage, "max_missing_run_seconds": max_missing * 30,
                "observed_vehicles": observed, "flow_vph_estimated": q,
                "estimated_15min_vehicles": q / 4 if n else np.nan,
                "speed_kmh_observed": speed_value,
                "occupancy_pct_observed": float(v.occupancy_pct.mean()) if n else np.nan,
                "conditional_flow_ci95_low": lo, "conditional_flow_ci95_high": hi,
                "eligible": coverage >= .6 and max_missing <= 180,
                "split": "calibration" if window.day == 15 else "validation",
                "provenance": "observed_samples_plus_time_expansion_NOT_complete_count"})
            # Pseudo-missingness experiment, performed ONLY on calibration day.
            # Reference is all available samples, never claimed to be true full flow.
            if window.day == 15 and coverage >= .8 and observed > 0:
                full = v.volume.to_numpy()
                for mode in ["random", "contiguous"]:
                    for repeat in range(20):
                        keep = max(2, round(.6 * n))
                        if mode == "random":
                            use = rng.choice(n, keep, replace=False)
                        else:
                            deleted = n - keep
                            begin = int(rng.integers(0, n - deleted + 1))
                            use = np.r_[np.arange(begin), np.arange(begin + deleted, n)]
                        estimate = float(full[use].mean() * 120)
                        masking.append({"detector_id": did, "window": window.isoformat(), "mode": mode,
                            "repeat": repeat, "reference_observed_rate": q, "estimated_rate": estimate,
                            "absolute_error": abs(estimate - q)})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "traffic_15min_estimates.csv", index=False, encoding="utf-8-sig")
    pd.concat(station_records, ignore_index=True).to_csv(OUT / "station_30s_aligned.csv.gz", index=False, compression="gzip")
    masking = pd.DataFrame(masking)
    masking.to_csv(OUT / "missingness_masking_checks.csv.gz", index=False, compression="gzip")
    checks = []
    for mode, g in masking.groupby("mode"):
        relative = g.absolute_error / g.reference_observed_rate
        checks.append({"mode": mode, "cases": len(g),
            "wmape_pct": float(100 * g.absolute_error.sum() / g.reference_observed_rate.sum()),
            "median_absolute_percentage_error_pct": float(100 * relative.median()),
            "p95_absolute_percentage_error_pct": float(100 * relative.quantile(.95))})
    train = table[table.date == "2026-10-15"].copy()
    valid = table[table.date == "2026-10-16"].copy()
    train["clock"] = train.window.str[11:]
    valid["clock"] = valid.window.str[11:]
    pairs = train.merge(valid, on=["detector_id", "clock"], suffixes=("_train", "_validation"))
    pairs = pairs[pairs.eligible_train & pairs.eligible_validation]
    pairs.to_csv(OUT / "next_day_reference_check.csv", index=False, encoding="utf-8-sig")
    temporal = {
        "pairs": len(pairs),
        "flow_wmape_pct": float(100 * abs(pairs.flow_vph_estimated_train - pairs.flow_vph_estimated_validation).sum()
                                 / pairs.flow_vph_estimated_validation.sum()),
        "speed_mae_kmh": float(abs(pairs.speed_kmh_observed_train - pairs.speed_kmh_observed_validation).mean()),
        "meaning": "Same-clock next-day persistence reference, NOT SUMO validation or proof of missingness unbiasedness"}
    report = {"fifteen_minute_rows": len(table), "eligible_rows": int(table.eligible.sum()),
        "eligible_by_day": table.groupby("date").eligible.sum().astype(int).to_dict(),
        "conditional_ci_definition": "Observed one-minute block bootstrap, 300 replicates; excludes missingness mechanism bias",
        "masking_checks_calibration_day_only": checks, "next_day_reference": temporal,
        "limitations": ["Observed traffic is sampled irregularly; unobserved periods may be systematically different.",
            "The 60%/180-second criteria are engineering screens, not contest criteria or statistical guarantees.",
            "Bootstrap interval quantifies sample variability, not total real-world uncertainty.",
            "15-minute station flow is not a complete OD matrix or an independent boundary inflow."]}
    (OUT / "estimation_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
