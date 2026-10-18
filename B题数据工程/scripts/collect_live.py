"""Collect a bounded set of official live traffic snapshots; no keys required."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
URL = "https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=60)
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 30:
        parser.error("samples must be positive; interval must be at least 30 seconds")
    directory = ROOT / "raw" / "live"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(args.samples):
        started = time.monotonic()
        now = datetime.now(TZ)
        record = {"index": index, "collected_at": now.isoformat(timespec="seconds"), "url": URL}
        try:
            response = requests.get(URL, timeout=(10, 25))
            response.raise_for_status()
            tree = ET.fromstring(response.content)
            if tree.tag != "raw_speed_volume_list":
                raise ValueError("Unexpected XML root")
            name = now.strftime("%Y%m%d_%H%M%S") + ".xml.gz"
            with gzip.open(directory / name, "wb") as handle:
                handle.write(response.content)
            record.update(
                success=True,
                file=name,
                sha256_uncompressed=hashlib.sha256(response.content).hexdigest(),
                data_date=tree.findtext("date"),
                periods=[
                    [period.findtext("period_from"), period.findtext("period_to")]
                    for period in tree.findall("./periods/period")
                ],
            )
        except (requests.RequestException, ET.ParseError, ValueError) as error:
            record.update(success=False, error_type=type(error).__name__)
        with (directory / "collection.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if index + 1 < args.samples:
            time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
