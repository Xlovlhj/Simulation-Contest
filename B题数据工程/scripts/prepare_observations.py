"""Extract regional observations from official archive ZIPs without expanding them.

Counts refer to 30-second lane intervals, NOT vehicles/hour. Missing/invalid
observations remain missing; repeated archive copies are deduplicated.
"""
from __future__ import annotations

import gzip
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "processed"
BBOX = {"south": 22.300, "west": 114.164, "north": 22.326, "east": 114.189}


def main() -> None:
    OUT.mkdir(exist_ok=True, parents=True)
    locations = pd.read_csv(ROOT / "raw/hk_transport/detector_locations.csv")
    sites = locations[
        locations.Latitude.between(BBOX["south"], BBOX["north"])
        & locations.Longitude.between(BBOX["west"], BBOX["east"])
    ].copy()
    sites.to_csv(OUT / "detector_locations.csv", index=False, encoding="utf-8-sig")
    ids = set(sites.AID_ID_Number)
    detector_pattern = re.compile(
        r"<detector><detector_id>(" + "|".join(sorted(ids)) + r")</detector_id>.*?</detector>",
        re.DOTALL,
    )
    records = {}
    files = []
    duplicate_rows = 0
    conflicting_rows = 0
    for archive in sorted((ROOT / "raw/history").glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in sorted(z.namelist()):
                if not name.endswith(".xml"):
                    continue
                text = z.read(name).decode("utf-8-sig")
                capture = re.search(r"(\d{8}-\d{4})-raw", name).group(1)
                date = re.search(r"<date>(.*?)</date>", text).group(1)
                observation_periods = []
                for period in re.findall(r"<period>(.*?)</period>", text, re.DOTALL):
                    start = re.search(r"<period_from>(.*?)</period_from>", period).group(1)
                    stop = re.search(r"<period_to>(.*?)</period_to>", period).group(1)
                    t0 = datetime.fromisoformat(date + "T" + start)
                    t1 = datetime.fromisoformat(date + "T" + stop)
                    if t1 <= t0:
                        t1 += timedelta(days=1)
                    observation_periods.append(t0.isoformat())
                    for match in detector_pattern.finditer(period):
                        node = ET.fromstring(match.group(0))
                        detector = node.findtext("detector_id")
                        for lane in node.findall("./lanes/lane"):
                            def number(tag):
                                try:
                                    return float(lane.findtext(tag))
                                except (ValueError, TypeError):
                                    return None
                            row = {
                                "period_from": t0.isoformat(), "period_to": t1.isoformat(),
                                "interval_seconds": (t1 - t0).total_seconds(),
                                "detector_id": detector, "lane_id": lane.findtext("lane_id"),
                                "speed_kmh": number("speed"), "occupancy_pct": number("occupancy"),
                                "vehicle_count": number("volume"), "speed_std_kmh": number("s.d."),
                                "valid_flag": lane.findtext("valid"),
                                "archive_capture_time": datetime.strptime(capture, "%Y%m%d-%H%M").isoformat(),
                                "source_archive": archive.name,
                            }
                            key = (row["period_from"], detector, row["lane_id"])
                            if key in records:
                                duplicate_rows += 1
                                if any(records[key][k] != row[k] for k in
                                       ["speed_kmh", "occupancy_pct", "vehicle_count", "valid_flag"]):
                                    conflicting_rows += 1
                            records[key] = row
                files.append({"archive": archive.name, "member": name.rsplit("/", 1)[-1],
                              "capture_time": capture, "observed_periods": observation_periods})
        print(f"Processed {archive.name}: {len(records)} unique regional lane records", flush=True)
    df = pd.DataFrame(records.values()).sort_values(["period_from", "detector_id", "lane_id"])
    df.to_csv(OUT / "traffic_lane_30s.csv.gz", index=False, compression="gzip")
    df["time"] = pd.to_datetime(df.period_from)
    df["date"] = df.time.dt.strftime("%Y-%m-%d")
    df["bin_5min"] = df.time.dt.floor("5min")
    df["usable"] = ((df.valid_flag == "Y") & (df.interval_seconds == 30)
                    & df.vehicle_count.ge(0) & df.speed_kmh.ge(0)
                    & df.occupancy_pct.between(0, 100))
    expected_lanes = df.groupby("detector_id").lane_id.nunique()
    df["count_valid"] = df.vehicle_count.where(df.usable)
    df["speed_times_count"] = (df.speed_kmh * df.vehicle_count).where(df.usable)
    df["occupancy_valid"] = df.occupancy_pct.where(df.usable)
    agg = df.groupby(["date", "detector_id", "bin_5min"]).agg(
        observed_lane_intervals=("lane_id", "size"),
        valid_lane_intervals=("usable", "sum"),
        observed_periods=("time", "nunique"),
        vehicle_count_observed=("count_valid", lambda x: x.sum(min_count=1)),
        speed_times_count=("speed_times_count", lambda x: x.sum(min_count=1)),
        mean_occupancy_pct=("occupancy_valid", "mean"),
    ).reset_index()
    agg["expected_lanes"] = agg.detector_id.map(expected_lanes)
    agg["coverage"] = agg.valid_lane_intervals / (10 * agg.expected_lanes)
    agg["speed_kmh_weighted"] = agg.speed_times_count / agg.vehicle_count_observed.replace(0, np.nan)
    agg["volume_vph_complete_only"] = (agg.vehicle_count_observed * 12).where(agg.coverage == 1)
    agg["volume_vph_sample_estimate"] = (
        agg.vehicle_count_observed * 120 * agg.expected_lanes / agg.valid_lane_intervals.replace(0, np.nan)
    ).where(agg.coverage >= 0.6)
    agg = agg.drop(columns="speed_times_count")
    agg["split"] = agg.date.map({"2026-10-15": "calibration", "2026-10-16": "validation"}).fillna("exclude_partial_day")
    agg.to_csv(OUT / "traffic_station_5min.csv", index=False, encoding="utf-8-sig")
    per_site = df.groupby(["date", "detector_id"]).agg(
        first_observation=("time", "min"), last_observation=("time", "max"),
        periods=("time", "nunique"), lane_records=("lane_id", "size"),
        valid_records=("usable", "sum"),
    ).reset_index()
    per_site["expected_lanes"] = per_site.detector_id.map(expected_lanes)
    per_site["day_coverage"] = per_site.valid_records / (2880 * per_site.expected_lanes)
    per_site.to_csv(OUT / "coverage_by_detector_day.csv", index=False, encoding="utf-8-sig")
    hospitals = json.loads((ROOT / "raw/hospitals.json").read_text(encoding="utf-8-sig"))
    pd.DataFrame(hospitals).to_csv(OUT / "hospitals_all.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([x for x in hospitals if
                  BBOX["south"] <= x["latitude"] <= BBOX["north"] and
                  BBOX["west"] <= x["longitude"] <= BBOX["east"]]).to_csv(
        OUT / "hospitals_region.csv", index=False, encoding="utf-8-sig")
    report = {
        "bbox_wgs84": BBOX, "timezone": "Asia/Hong_Kong (+08:00)",
        "detectors": len(sites), "history_xml_files": len(files),
        "unique_lane_records": len(df), "deduplicated_rows": duplicate_rows,
        "duplicate_conflicts_latest_archive_capture_used": conflicting_rows,
        "first_observation": df.time.min().isoformat(), "last_observation": df.time.max().isoformat(),
        "valid_lane_record_fraction": float(df.usable.mean()),
        "complete_5min_rows": int((agg.coverage == 1).sum()), "total_5min_rows": len(agg),
        "hospitals_all": len(hospitals),
        "coverage_day_min": float(per_site.day_coverage.min()),
        "coverage_day_max": float(per_site.day_coverage.max()),
        "notes": ["No zero imputation for missing or invalid records.",
                  "Missing entire 5-minute bins are absent from CSV; reindex before plotting.",
                  "Speed is count-weighted; no traffic => undefined aggregate speed.",
                  "Expected lane count is inferred from distinct lane labels, not a surveyed lane inventory.",
                  "Coverage is measured against ten 30-second intervals per five minutes.",
                  "Different-day validation is a planned split, not completed model validation."]}
    report["inferred_lane_labels_total"] = int(expected_lanes.sum())
    report["full_day_lane_coverage"] = {
        day: float(g.usable.sum() / (2880 * expected_lanes.sum()))
        for day, g in df.groupby("date") if day in {"2026-10-15", "2026-10-16"}
    }
    (OUT / "observation_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with gzip.open(OUT / "archive_observation_index.json.gz", "wt", encoding="utf-8") as h:
        json.dump(files, h, ensure_ascii=False)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
