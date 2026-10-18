"""Download public Hong Kong Transport Department sources with provenance.

No API credentials are needed. Run from any directory with Python + requests.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
BASE = "https://static.data.gov.hk/td/road-network-v2/"
RAW_URL = "https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml"


def download(relative_path: str, url: str, params: dict | None = None) -> dict:
    target = ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "file": relative_path,
        "url": url,
        "params": params,
        "collected_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }
    for attempt in range(2):
        try:
            with requests.get(url, params=params, stream=True, timeout=(12, 80)) as response:
                record.update(http_status=response.status_code, resolved_url=response.url)
                response.raise_for_status()
                part = target.with_name(target.name + ".part")
                digest = hashlib.sha256()
                size = 0
                with part.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        limit = 512 if relative_path.endswith("centerline.gml") else 160
                        if size > limit * 1024 * 1024:
                            raise ValueError(f"Source exceeds the {limit} MiB per-file download limit")
                        handle.write(chunk)
                        digest.update(chunk)
                part.replace(target)
                record.update(bytes=size, sha256=digest.hexdigest(), success=True)
                print(json.dumps({"downloaded": relative_path, "bytes": size}, ensure_ascii=False), flush=True)
                return record
        except (requests.RequestException, OSError, ValueError) as error:
            record.update(success=False, error_type=type(error).__name__)
            if attempt == 0:
                time.sleep(1)
    print(json.dumps({"failed": relative_path, "error": record.get("error_type")}), flush=True)
    return record


def main() -> None:
    jobs = [
        ("raw/official_gis/centerline.gml", BASE + "CENTERLINE.gml", None),
        ("raw/official_gis/intersection.gml", BASE + "INTERSECTION.gml", None),
        ("raw/official_gis/speed_limit.gml", BASE + "SPEED_LIMIT.gml", None),
        ("raw/official_gis/traffic_features.gml", BASE + "TRAFFIC_FEATURES.gml", None),
        ("raw/official_gis/turn.gml", BASE + "TURN.gml", None),
        ("raw/official_gis/data_specification.zip", BASE + "dataspec/rdnet_dataspec.zip", None),
        ("raw/official_gis/file_list.csv", BASE + "dataspec/file_list.csv", None),
        ("raw/hospitals.json", "https://www.ha.org.hk/opendata/facility-hosp.json", None),
        (
            "raw/history/20261015.zip",
            "https://app.data.gov.hk/v1/historical-archive/get-file",
            {"url": RAW_URL, "time": "20261015"},
        ),
        (
            "raw/history/20261016.zip",
            "https://app.data.gov.hk/v1/historical-archive/get-file",
            {"url": RAW_URL, "time": "20261016"},
        ),
        (
            "raw/history/archive_index.json",
            "https://app.data.gov.hk/v1/historical-archive/list-file-versions",
            {"url": RAW_URL, "start": "20261015", "end": "20261016"},
        ),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda item: download(*item), jobs))
    (ROOT / "sources_downloads.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": sum(bool(item.get("success")) for item in results), "total": len(results)}), flush=True)


if __name__ == "__main__":
    main()
