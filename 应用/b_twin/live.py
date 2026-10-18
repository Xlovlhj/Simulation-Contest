"""Collect official observations into a timestamped, idempotent state store.

Observed station state is separate from simulated edge/vehicle state. This
module does not claim that traffic demand has been assimilated into SUMO.
AI-assisted: OpenAI Codex; exact model/release date unavailable in this runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .paths import APP, DATA, REFINED

TZ = timezone(timedelta(hours=8))
URL = "https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml"
LIVE = APP / "runtime" / "live"


def parse_snapshot(payload, expected_lanes, received_at):
    root = ET.fromstring(payload)
    if root.tag != "raw_speed_volume_list":
        raise ValueError("Unexpected official feed root")
    date = root.findtext("date")
    rows = []
    for period in root.findall("./periods/period"):
        start = datetime.fromisoformat(f"{date}T{period.findtext('period_from')}").replace(tzinfo=TZ)
        stop = datetime.fromisoformat(f"{date}T{period.findtext('period_to')}").replace(tzinfo=TZ)
        if stop < start:
            stop += timedelta(days=1)
        seconds = (stop - start).total_seconds()
        for detector in period.findall("./detectors/detector"):
            did = detector.findtext("detector_id")
            if did not in expected_lanes:
                continue
            lanes = {}
            for lane in detector.findall("./lanes/lane"):
                lid = lane.findtext("lane_id")
                volume, speed, occupancy = (float(lane.findtext(k)) for k in ["volume", "speed", "occupancy"])
                lanes[lid] = {"valid": lane.findtext("valid") == "Y" and volume >= 0 and speed >= 0 and 0 <= occupancy <= 100,
                    "count": volume, "speed_kmh": speed, "occupancy_pct": occupancy}
            labels = set(lanes)
            expected = set(expected_lanes[did])
            complete = labels == expected and all(l["valid"] for l in lanes.values()) and seconds == 30
            age = (received_at - stop).total_seconds()
            quality = "valid" if complete and age >= 0 else "future_timestamp" if age < 0 else "invalid_or_incomplete"
            count = sum(l["count"] for l in lanes.values()) if quality == "valid" else None
            rows.append({"detector_id": did, "observation_start": start.isoformat(), "observation_end": stop.isoformat(),
                "received_at": received_at.isoformat(), "age_seconds_at_receipt": age,
                "quality": quality, "expected_lane_labels": sorted(expected), "received_lane_labels": sorted(labels),
                "observed_count_30s": count,
                "sample_rate_vph": count * 120 if count is not None else None,
                "speed_kmh": sum(l["speed_kmh"] * l["count"] for l in lanes.values()) / count if count else None,
                "occupancy_pct": sum(l["occupancy_pct"] for l in lanes.values()) / len(lanes) if quality == "valid" else None,
                "provenance": "official_30s_station_observation_NOT_15min_or_complete_OD"})
    return rows


def merge_state(previous, observations):
    state = dict(previous)
    updated = 0
    rejected = 0
    for row in observations:
        did = row["detector_id"]
        old = state.get(did)
        old_end = old.get("observation_end") if old else None
        if row["quality"] != "valid":
            rejected += 1
            continue
        if old_end and row["observation_end"] <= old_end:
            rejected += 1
            continue
        state[did] = row
        updated += 1
    return state, updated, rejected


def collect_once():
    LIVE.mkdir(parents=True, exist_ok=True)
    expected = pd.read_csv(DATA / "processed/traffic_lane_30s.csv.gz", usecols=["detector_id", "lane_id"])
    expected = expected.groupby("detector_id").lane_id.unique().to_dict()
    state_file = LIVE / "state.json"
    previous = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"stations": {}}
    record = {"requested_at": datetime.now(TZ).isoformat(), "url": URL}
    try:
        response = requests.get(URL, timeout=(10, 30))
        response.raise_for_status()
        received = datetime.now(TZ)
        payload = response.content
        digest = hashlib.sha256(payload).hexdigest()
        parsed = parse_snapshot(payload, expected, received)
        if not parsed:
            raise ValueError("No regional detector observations in official response")
        filename = received.strftime("%Y%m%d_%H%M%S_%f") + ".xml.gz"
        (LIVE / filename).write_bytes(gzip.compress(payload, mtime=0))
        stations, updated, rejected = merge_state(previous["stations"], parsed)
        mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str}).set_index("detector_id")
        for did, row in stations.items():
            row["data_age_seconds"] = (received - datetime.fromisoformat(row["observation_end"])).total_seconds()
            row["stale_over_10min"] = row["data_age_seconds"] > 600
            row["sumo_edge"] = mapping.loc[did, "sumo_edge"]
            row["mapping_requires_review"] = bool(mapping.loc[did, "requires_review"])
        current = {"checked_at": received.isoformat(), "last_payload_sha256": digest,
            "stations": stations, "mode": "official_observation_state_only_NOT_simulation_assimilation"}
        state_file.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        valid_ages = [r["data_age_seconds"] for r in stations.values()]
        record.update(success=True, received_at=received.isoformat(), file=filename,
            sha256_uncompressed=digest, duplicate_payload=digest == previous.get("last_payload_sha256"),
            response_station_rows=len(parsed), updated_station_rows=updated, rejected_or_unchanged_rows=rejected,
            stored_stations=len(stations), min_age_seconds=min(valid_ages) if valid_ages else None,
            max_age_seconds=max(valid_ages) if valid_ages else None,
            state_file="runtime/live/state.json", stale_threshold_seconds=600,
            note="10-minute stale threshold is a display/engineering rule, not a competition criterion")
    except (requests.RequestException, ET.ParseError, ValueError) as exc:
        record.update(success=False, error_type=type(exc).__name__)
    with (LIVE / "collection.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
