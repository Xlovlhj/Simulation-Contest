#!/usr/bin/env python3
"""Run an explicitly requested, at most three-call Baidu capability probe.

AI-assisted implementation: Codex (OpenAI), prepared 2026-10-17.
Exact model version and release date were not provided to this program;
the team must complete that metadata in its AI-use disclosure before submission.

The default action and --self-test never read credentials or access the network.
Signing reference: https://lbsyun.baidu.com/faq/api?title=webapi/appendix
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent.parent
HOST = "https://api.map.baidu.com"
TIMEOUT_SECONDS = 15
MAX_REQUESTS = 3
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
BEIJING_TZ = timezone(timedelta(hours=8))
SENSITIVE_KEYS = {
    "ak", "sk", "sn", "key", "api_key", "apikey", "secret", "secret_key",
    "secretkey", "security_key", "securitykey", "signature", "token",
    "access_token", "accesskey", "authorization", "password",
}
DATA_TIME_KEYS = {"data_time", "update_time", "updated_at", "timestamp", "traffic_time"}


def now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="milliseconds")


def signed_query(path: str, parameters: list[tuple[str, str]], sk: str) -> tuple[str, str]:
    """Apply the official GET example's ordered quote -> quote_plus -> MD5 steps.

    Keep query order unchanged between signing and sending. The final sn is not
    included in the signed input. Values here are our own controlled parameters.
    """
    raw_query = path + "?" + "&".join(f"{key}={value}" for key, value in parameters)
    encoded_query = quote(raw_query, safe="/:=&?#+!$,;'@()*[]")
    signature = hashlib.md5(quote_plus(encoded_query + sk).encode("utf-8")).hexdigest()
    return encoded_query + "&sn=" + signature, signature


class Redactor:
    def __init__(self, *secrets: str) -> None:
        self.secrets: set[str] = set()
        for secret in secrets:
            self.add(secret)

    def add(self, secret: str) -> None:
        if not secret:
            return
        encoded = secret
        for _ in range(3):
            self.secrets.update((encoded, quote_plus(encoded)))
            encoded = quote(encoded, safe="")

    def text(self, value: str) -> str:
        # Full URLs are never retained, even if an upstream error echoes one.
        value = re.sub(r"https?://[^\s<>\"']+", "[REDACTED_URL]", value, flags=re.I)
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return re.sub(
            r"(?i)\b(ak|sk|sn|api[_-]?key|security[_-]?key|secret|signature|token)"
            r"\s*[:=]\s*[^\s,;}&\"']+",
            r"\1=[REDACTED]",
            value,
        )

    def clean(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                self.text(str(key)): (
                    "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else self.clean(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def summarize(payload: Any) -> dict[str, Any]:
    fields: set[str] = set()
    counts: dict[str, int] = {}
    data_times: dict[str, Any] = {}

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                fields.add(child)
                if key in DATA_TIME_KEYS and isinstance(item, (str, int, float)):
                    data_times[child] = item
                visit(item, child)
        elif isinstance(value, list):
            # Repeated nested arrays share a schema path: count all their items.
            counts[path] = counts.get(path, 0) + len(value)
            # Inspect each item because optional fields can occur late in a list.
            for item in value:
                visit(item, path + "[]")

    visit(payload)
    api_status = payload.get("status") if isinstance(payload, dict) else None
    # A status must not become an upstream error-message disclosure channel.
    if not isinstance(api_status, (int, float)) and not (
        isinstance(api_status, str) and api_status.isdigit()
    ):
        api_status = None
    return {
        "api_status": api_status,
        "field_names": sorted(fields),
        "array_counts": counts,
        "response_time_fields": data_times,
    }


def write_json(path: Path, payload: Any, redactor: Redactor) -> None:
    # The final serialization is redacted again to cover unusual object keys.
    body = json.dumps(redactor.clean(payload), ensure_ascii=False, indent=2)
    path.write_text(redactor.text(body) + "\n", encoding="utf-8")


def print_status(payload: dict[str, Any], redactor: Redactor | None = None) -> None:
    if redactor is not None:
        payload = redactor.clean(payload)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def query_once(path: str, params: list[tuple[str, str]], ak: str, sk: str,
               output_dir: Path, request_number: int, redactor: Redactor) -> tuple[Any, dict[str, Any]]:
    if request_number > MAX_REQUESTS:
        raise ValueError("request_limit")
    if path not in {"/geocoding/v3/", "/traffic/v1/bound", "/directionlite/v1/driving"}:
        raise ValueError("endpoint_not_allowed")
    signed_path, signature = signed_query(path, params + [("ak", ak)], sk)
    redactor.add(signature)
    record: dict[str, Any] = {
        "request_number": request_number,
        "endpoint": path,
        "params": dict(params),
        "coordinate_system": "bd09ll",
        "requested_at": now(),
        "http_status": None,
        "transport_status": "not_sent",
    }
    payload: Any = None
    body: bytes | None = None
    # Disable every redirect; requests can only target the fixed HTTPS host.
    opener = build_opener(NoRedirects())
    request = Request(HOST + signed_path, headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            record["http_status"] = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
            record["transport_status"] = "received"
    except HTTPError as exc:
        record["http_status"] = exc.code
        record["transport_status"] = "redirect_rejected" if 300 <= exc.code < 400 else "http_error"
        try:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, ValueError):
            record["transport_status"] = "http_error_body_unavailable"
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError):
        # Do not expose exception messages: they can contain signed URLs.
        record["transport_status"] = "network_error"
    record["received_at"] = now()
    if body is not None:
        if len(body) > MAX_RESPONSE_BYTES:
            record["response_format"] = "size_limit_exceeded"
            payload = {"_response_omitted": "size_limit_exceeded"}
        else:
            try:
                payload = json.loads(body)
                record["response_format"] = "json"
            except (json.JSONDecodeError, UnicodeDecodeError):
                record["response_format"] = "non_json"
                payload = {"_non_json_body": body.decode("utf-8", errors="replace")}
        payload = redactor.clean(payload)
        filename = f"{request_number:02d}_{path.strip('/').replace('/', '_')}.json"
        write_json(output_dir / filename, payload, redactor)
        record["response_file"] = filename
    record.update(summarize(payload))
    print_status({
        "request_number": request_number,
        "transport_status": record["transport_status"],
        "http_status": record["http_status"],
        "api_status": record["api_status"],
        "field_names": record["field_names"],
        "array_counts": record["array_counts"],
        "requested_at": record["requested_at"],
        "received_at": record["received_at"],
        "response_time_fields": record["response_time_fields"],
    }, redactor)
    return payload, record


def load_study_area() -> dict[str, Any]:
    """Read the non-secret study-area configuration inside this workspace."""
    area = json.loads((ROOT / "config" / "study_area.json").read_text(encoding="utf-8"))
    for key in ("id", "city", "geocode_address"):
        if not isinstance(area[key], str) or not area[key].strip():
            raise ValueError("invalid_study_area")
    if area["coordinate_system"] != "bd09ll":
        raise ValueError("unsupported_coordinate_system")
    levels = area.get("expected_geocode_levels")
    if levels is not None and (not isinstance(levels, list) or not levels
                               or not all(isinstance(value, str) and value for value in levels)):
        raise ValueError("invalid_expected_geocode_levels")
    bounds = area["validation_bounds"]
    south, north, west, east = (float(bounds[k]) for k in ("south", "north", "west", "east"))
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("invalid_validation_bounds")
    for key in ("latitude", "longitude"):
        if not 0 < float(area["probe_half_extent_degrees"][key]) <= 0.01:
            raise ValueError("invalid_probe_extent")
    return area


def valid_location(payload: Any, area: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(payload, dict) or payload.get("status") not in (0, "0"):
        return None
    try:
        location = payload["result"]["location"]
        lat, lng = float(location["lat"]), float(location["lng"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    expected_levels = area.get("expected_geocode_levels")
    if expected_levels and payload["result"].get("level") not in expected_levels:
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    # Reject a same-name result outside the configured city envelope. This broad
    # envelope is only a sanity check; it is not the final simulation boundary.
    bounds = area["validation_bounds"]
    if not (float(bounds["south"]) <= lat <= float(bounds["north"])
            and float(bounds["west"]) <= lng <= float(bounds["east"])):
        return None
    return lat, lng


def diagonal_metres(south: float, west: float, north: float, east: float) -> float:
    lat1, lat2 = math.radians(south), math.radians(north)
    dlat, dlng = lat2 - lat1, math.radians(east - west)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1.0, math.sqrt(a)))


def probe() -> int:
    try:
        area = load_study_area()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print_status({"status": "study_area_unavailable_or_invalid"})
        return 1
    try:
        config = json.loads((ROOT / "config" / "baidu.local.json").read_text(encoding="utf-8"))
        ak, sk = config["ak"], config["sk"]
        if not isinstance(ak, str) or not isinstance(sk, str) or not ak.strip() or not sk.strip():
            raise ValueError("invalid_credentials")
    except (OSError, ValueError, TypeError, KeyError):
        print_status({"status": "config_unavailable_or_invalid"})
        return 1
    redactor = Redactor(ak, sk)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output_dir = ROOT / "data" / "raw" / "baidu" / f"probe_{stamp}"
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "purpose": "capability_probe",
        "formal_experiment": False,
        "billing_status": "unverified",
        "coordinate_system": "bd09ll",
        "study_area": area,
        "started_at": now(),
        "max_requests": MAX_REQUESTS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "retries": 0,
        "requests": [],
        "source_data_time_note": "Response time fields are reported verbatim; request time is not observation time.",
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
        write_json(output_dir / "manifest.json", manifest, redactor)
        payload, record = query_once(
            "/geocoding/v3/",
            [("address", area["geocode_address"]), ("city", area["city"]),
             ("output", "json"), ("ret_coordtype", "bd09ll")],
            ak, sk, output_dir, 1, redactor,
        )
        manifest["requests"].append(record)
        write_json(output_dir / "manifest.json", manifest, redactor)
        location = valid_location(payload, area) if record["http_status"] == 200 else None
        if location is None:
            manifest["completion_status"] = "stopped_geocoding_not_usable"
            return 1
        lat, lng = location
        half_lat = float(area["probe_half_extent_degrees"]["latitude"])
        half_lng = float(area["probe_half_extent_degrees"]["longitude"])
        south, north, west, east = lat - half_lat, lat + half_lat, lng - half_lng, lng + half_lng
        diagonal = diagonal_metres(south, west, north, east)
        if diagonal >= 2000:
            manifest["completion_status"] = "stopped_bounds_too_large"
            return 1
        southwest = f"{south:.6f},{west:.6f}"
        northeast = f"{north:.6f},{east:.6f}"
        manifest["bounds_diagonal_metres"] = round(diagonal, 2)
        manifest["probe_bounds"] = {"south": south, "north": north, "west": west, "east": east}
        _, record = query_once(
            "/traffic/v1/bound",
            [("bounds", southwest + ";" + northeast), ("coord_type_input", "bd09ll"), ("coord_type_output", "bd09ll")],
            ak, sk, output_dir, 2, redactor,
        )
        manifest["requests"].append(record)
        write_json(output_dir / "manifest.json", manifest, redactor)
        _, record = query_once(
            "/directionlite/v1/driving",
            [("origin", southwest), ("destination", northeast), ("coord_type", "bd09ll"),
             ("ret_coordtype", "bd09ll"), ("steps_info", "1"), ("timestamp", str(int(time.time())))],
            ak, sk, output_dir, 3, redactor,
        )
        manifest["requests"].append(record)
        success = all(
            item["http_status"] == 200 and item["api_status"] in (0, "0")
            for item in manifest["requests"]
        )
        manifest["completion_status"] = "completed" if success else "completed_with_api_or_transport_errors"
        return 0 if success else 1
    except Exception:
        # Never print a traceback involving a credential-bearing request.
        manifest["completion_status"] = "local_processing_error"
        print_status({"status": "local_processing_error"})
        return 1
    finally:
        manifest["finished_at"] = now()
        try:
            write_json(output_dir / "manifest.json", manifest, redactor)
        except Exception:
            print_status({"status": "manifest_write_failed"})


def self_test() -> int:
    query, signature = signed_query(
        "/geocoder/v2/", [("address", "百度大厦"), ("output", "json"), ("ak", "yourak")], "yoursk",
    )
    expected = "7de5a22212ffaa9e326444c75a58f9a0"
    redactor = Redactor("test-ak-secret", "test-sk-secret", signature)
    sample = {"ak": "different-ak", "nested": [{"sn": signature, "text": "test-ak-secret test-sk-secret"}],
              "url": "https://api.map.baidu.com" + query}
    serialized = json.dumps(redactor.clean(sample))
    area = {"validation_bounds": {"south": 30.5, "north": 32, "west": 120.8, "east": 122.2}}
    shanghai = {"status": 0, "result": {"location": {"lat": 31.21, "lng": 121.47}}}
    beijing = {"status": 0, "result": {"location": {"lat": 39.98, "lng": 116.32}}}
    invalid = {"status": 0, "result": {"location": {"lat": float("nan"), "lng": 121.47}}}
    wrong_poi = {"status": 0, "result": {"location": {"lat": 31.21, "lng": 121.47},
                                           "level": "餐饮", "confidence": 80}}
    location_checks = (
        valid_location(shanghai, area) == (31.21, 121.47)
        and valid_location(beijing, area) is None
        and valid_location(invalid, area) is None
        and valid_location(wrong_poi, dict(area, expected_geocode_levels=["医疗", "门址"])) is None
    )
    passed = signature == expected and location_checks and all(
        value not in serialized
        for value in ("different-ak", "test-ak-secret", "test-sk-secret", signature, "https://api.map.baidu.com")
    )
    print_status({"status": "self_test_passed" if passed else "self_test_failed"})
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--self-test", action="store_true", help="Check a public signing vector and redaction, offline.")
    actions.add_argument("--probe", action="store_true", help="Explicitly authorize at most 3 HTTPS calls using local config; billing unverified.")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.probe:
        return probe()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
