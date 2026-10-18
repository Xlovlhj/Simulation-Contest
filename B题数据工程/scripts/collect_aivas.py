"""Collect four officially listed regional AIVAS endpoints without filling gaps.

AI-assisted: OpenAI Codex. Exact model information and human review pending.
Camera locations/API URLs were retrieved from the official CSDI dataset.
130-second samples are intermittent; they are not continuous junction counts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import requests

TZ = timezone(timedelta(hours=8))
CAMERAS = ("K106F", "K108F", "K202F", "K205F")
LOCATION_SOURCE = "https://portal.csdi.gov.hk/csdi-webpage/file-api?dataset_id=td_rcd_1671693527354_28926&format=geojson&layer_name=Traffic_Data_from_AI_Video_Analytics_System_of_CCTVs"
SPECIFICATION = "https://www.td.gov.hk/datagovhk_td/traffic-data-aivas/resources/traffic_data_from_ai_video_analytics_system_of_cctvs_dataspec.pdf"
COLUMNS = ["camera", "segment", "observation_start", "observation_end", "available_at",
    "source_age_seconds", "observed_seconds", "observed_count", "sample_rate_vph",
    "space_mean_speed_kmh", "speed_for_evaluation_kmh", "quality", "timezone_basis",
    "source_file", "source_sha256"]


def normalized(payload, camera, received, source_file, sha):
    if payload.get("CCTV_id") != camera:
        raise ValueError("Camera ID mismatch")
    end = datetime.fromisoformat(payload["generated_timestamp"])
    zone_basis = "explicit_source_timezone"
    if end.tzinfo is None:
        end = end.replace(tzinfo=TZ)
        zone_basis = "assumed_Hong_Kong_UTC+8_source_timestamp_has_no_offset"
    age = (received - end).total_seconds()
    entries = payload.get("traffic_data", [])
    labels = [str(e.get("segment")) for e in entries]
    rows = []
    for entry in entries:
        segment = str(entry["segment"])
        count, speed = float(entry["flow"]), float(entry["speed"])
        quality = "valid_sample"
        if payload.get("Is_valid") != "Y":
            quality = "source_invalid"
        elif labels.count(segment) != 1:
            quality = "duplicate_segment_in_response"
        elif not all(math.isfinite(v) and v >= 0 for v in (count, speed)) or count % 1:
            quality = "invalid_numeric_value"
        elif age < 0:
            quality = "future_timestamp_excluded"
        elif age > 3600:
            quality = "stale_for_current_operation"
        elif count == 0:
            quality = "valid_zero_count_speed_undefined"
        eligible = quality.startswith("valid_")
        rows.append(dict(camera=camera, segment=segment,
            observation_start=(end - timedelta(seconds=130)).isoformat(),
            observation_end=end.isoformat(), available_at=received.isoformat(),
            source_age_seconds=age, observed_seconds=130, observed_count=count,
            sample_rate_vph=count * 3600 / 130 if eligible else None,
            space_mean_speed_kmh=speed,
            speed_for_evaluation_kmh=speed if eligible and count > 0 else None,
            quality=quality, timezone_basis=zone_basis,
            source_file=source_file, source_sha256=sha))
    return rows


def fetch(camera, sequence, output):
    url = "https://td2132022opendata.td.gov.hk/" + camera
    record = {"camera": camera, "sequence": sequence, "url": url,
        "requested_at": datetime.now(TZ).isoformat()}
    rows = []
    try:
        response = requests.get(url, timeout=(5, 20))
        received = datetime.now(TZ)
        raw = response.content
        name = f"raw/{sequence:04d}_{camera}.json"
        (output / name).write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        record.update(status=response.status_code, received_at=received.isoformat(),
            source_file=name, source_sha256=sha)
        response.raise_for_status()
        rows = normalized(response.json(), camera, received, name, sha)
        record.update(success=True, rows=len(rows),
            eligible_rows=sum(r["quality"].startswith("valid_") for r in rows))
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        record.update(success=False, error_type=type(exc).__name__,
            failed_at=datetime.now(TZ).isoformat())
    return record, rows


def write_table(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Official intermittent 130s samples; no gap filling")
    parser.add_argument("--output", required=True)
    parser.add_argument("--minutes", type=float, default=0, help="0: one batch; maximum 24 hours")
    parser.add_argument("--interval", type=float, default=60)
    args = parser.parse_args()
    if not math.isfinite(args.minutes) or not 0 <= args.minutes <= 1440:
        raise ValueError("minutes must be 0..1440")
    if not math.isfinite(args.interval) or args.interval < 60:
        raise ValueError("interval must be at least 60 seconds")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "raw").mkdir()
    protocol = {"status": "running", "started_at": datetime.now(TZ).isoformat(),
        "duration_minutes": args.minutes, "interval_seconds": args.interval,
        "cameras": CAMERAS, "location_source": LOCATION_SOURCE, "specification": SPECIFICATION,
        "sample_seconds": 130, "missing_periods_filled": False,
        "source_timestamp_timezone": "UTC+8 assumed when source omits offset; preserve assumption",
        "source_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": "Rotating cameras; not whole-junction, turn counts, continuous counts, signal timing or complete OD. Hourly rate is exposure conversion, not a measured hourly total."}
    started, sequence, records, observations = time.perf_counter(), 0, [], []
    try:
        (output / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=4) as pool, (output / "collection.jsonl").open("w", encoding="utf-8") as log:
            while True:
                batch_started = time.perf_counter()
                for record, rows in pool.map(lambda camera: fetch(camera, sequence, output), CAMERAS):
                    records.append(record)
                    observations.extend(rows)
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                write_table(output / "samples_all_receipts.csv", observations)
                # Deduplicate by camera, segment, end time. Keep the earliest
                # eligible receipt; a future-dated first receipt is not usable.
                unique = {}
                for row in observations:
                    key = row["camera"], row["segment"], row["observation_end"]
                    old = unique.get(key)
                    if old is None or (not old["quality"].startswith("valid_") and row["quality"].startswith("valid_")):
                        unique[key] = row
                write_table(output / "unique_observations.csv", unique.values())
                status = {"batches": sequence + 1, "requests": len(records),
                    "successful_responses": sum(r["success"] for r in records),
                    "unique_segment_periods": len(unique),
                    "eligible_unique_segment_periods": sum(r["quality"].startswith("valid_") for r in unique.values()),
                    "elapsed_seconds": time.perf_counter() - started,
                    "last_updated_at": datetime.now(TZ).isoformat()}
                (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(status), flush=True)
                sequence += 1
                if not args.minutes or time.perf_counter() - started + args.interval >= args.minutes * 60:
                    break
                time.sleep(max(0, args.interval - (time.perf_counter() - batch_started)))
        protocol["status"] = "completed"
    except BaseException as exc:
        protocol.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error_type=type(exc).__name__)
        raise
    finally:
        protocol["closed_at"] = datetime.now(TZ).isoformat()
        (output / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
