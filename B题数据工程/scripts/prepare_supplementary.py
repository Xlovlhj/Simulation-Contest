"""Normalize observed class proportions and long-term validation references."""
from __future__ import annotations

from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

from prepare_gis import BBOX, TO_GEO, intersects, objects
from download_sources import ROOT


def main():
    out = ROOT / "processed"
    loc = pd.read_csv(ROOT / "raw/vehicle_classes/detector_locations.csv")
    loc = loc[loc.Latitude.between(BBOX["south"], BBOX["north"]) &
              loc.Longitude.between(BBOX["west"], BBOX["east"])]
    loc.to_csv(out / "vehicle_class_locations.csv", index=False, encoding="utf-8-sig")
    ids = set(loc.Device_ID)
    inputs = [("latest.xml", (ROOT / "raw/vehicle_classes/latest.xml").read_bytes())]
    for archive in sorted((ROOT / "raw/vehicle_classes").glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            inputs += [(archive.name + ":" + n.rsplit("/", 1)[-1], z.read(n))
                       for n in z.namelist() if n.endswith(".xml")]
    records = {}
    for source, xml in inputs:
        root = ET.fromstring(xml)
        date = root.findtext("date")
        for period in root.findall("./periods/period"):
            begin, end = period.findtext("period_from"), period.findtext("period_to")
            def as_time(value):
                if value == "24:00:00":
                    return (datetime.fromisoformat(date) + timedelta(days=1)).isoformat()
                return date + "T" + value
            duration = (datetime.fromisoformat(as_time(end)) - datetime.fromisoformat(as_time(begin))).total_seconds()
            if duration <= 0:
                duration += 86400
            for detector in period.findall("./detectors/detector"):
                did = detector.findtext("detector_id")
                if did not in ids:
                    continue
                for cls in detector.findall("./vehicle_class/class"):
                    name = cls.findtext("class_name")
                    row = {"period_from": as_time(begin), "period_to": as_time(end),
                           "interval_seconds": duration, "detector_id": did,
                           "direction": detector.findtext("direction").strip(),
                           "valid_flag": detector.findtext("valid"), "class_name": name,
                           "proportion_pct": float(cls.findtext("proportion")),
                           "exclusive_category": name != "COMMERCIAL VEHICLE", "source": source}
                    records[(row["period_from"], row["period_to"], did, name)] = row
    data = pd.DataFrame(records.values())
    data.to_csv(out / "vehicle_class_proportions.csv", index=False, encoding="utf-8-sig")
    exclusive = data[data.exclusive_category]
    checks = exclusive.groupby(["period_from", "period_to", "detector_id", "valid_flag"]).proportion_pct.sum().reset_index(name="sum_exclusive_pct")
    checks["usable"] = (checks.valid_flag == "Y") & checks.sum_exclusive_pct.between(98, 102)
    checks.to_csv(out / "vehicle_class_quality.csv", index=False, encoding="utf-8-sig")
    census_rows = []
    for attrs, pairs in objects(ROOT / "raw/census/station_points.gml"):
        if intersects(pairs):
            lon, lat = TO_GEO.transform(*pairs[0])
            census_rows.append(dict(attrs, longitude=lon, latitude=lat))
    census = pd.DataFrame(census_rows)
    census.to_csv(out / "census_station_locations.csv", index=False, encoding="utf-8-sig")
    # Retain only the regional station sheets from the latest available year.
    with zipfile.ZipFile(ROOT / "raw/census/traffic_data.zip") as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read("ATC_TRAFFIC_DATA/2024.zip"))) as inner:
            available = inner.namelist()
            # Match official station identifiers; retain all columns in CSV for audit.
            station_ids = set()
            for row in census_rows:
                for key, value in row.items():
                    if "STATION" in key.upper() or key.upper() in {"STA_ID", "STN_NO"}:
                        if value:
                            station_ids.add(str(value).lstrip("S"))
            chosen = [n for n in available if n.rsplit("/", 1)[-1].removesuffix(".xls").lstrip("S") in station_ids]
            directory = out / "census_2024_region"
            directory.mkdir(exist_ok=True)
            for name in chosen:
                (directory / name.rsplit("/", 1)[-1]).write_bytes(inner.read(name))
    journeys = []
    for period in ["am", "pm"]:
        p = ROOT / f"raw/journey_time/{period}_kowloon_2025.csv"
        d = pd.read_csv(p, skiprows=4)
        d.columns = [c.replace("\r", "").replace("\n", " ") for c in d.columns]
        d["year"] = 2025
        d["period"] = period
        d["use"] = "long_term_reference_only_routes_may_extend_beyond_study_area"
        journeys.append(d)
    pd.concat(journeys).to_csv(out / "journey_time_reference_2025.csv", index=False, encoding="utf-8-sig")
    report = {"regional_vehicle_class_stations": len(loc), "class_observation_rows": len(data),
        "first_class_observation": data.period_from.min(), "last_class_observation": data.period_to.max(),
        "usable_station_periods": int(checks.usable.sum()), "total_station_periods": len(checks),
        "regional_census_stations": len(census_rows), "regional_census_2024_files": len(chosen),
        "census_columns": census.columns.tolist(),
        "notes": ["COMMERCIAL VEHICLE is an aggregate of categories 7-10; never add it to the ten exclusive classes.",
                  "Class feed is previous-day data, not live 30-second observations.",
                  "16-hour/24-hour aggregate proportions are separate from hourly proportions; do not pool them.",
                  "An hourly sample cannot supply unobserved class-specific absolute counts.",
                  "2024 census and 2025 journey times provide historical context, not same-day validation."]}
    (out / "supplementary_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
