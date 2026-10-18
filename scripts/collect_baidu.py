#!/usr/bin/env python3
"""Controlled Baidu collection; no action means help and no network access.

AI-assisted by Codex/OpenAI, 2026-10-17; exact model/release metadata is not
available here and must be completed in the team's AI-use disclosure.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
import uuid

sys.dont_write_bytecode = True
from baidu_probe import NoRedirects, Redactor, diagonal_metres, signed_query, summarize

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve()
TZ = timezone(timedelta(hours=8))
HOST = "https://api.map.baidu.com"
ENDPOINTS = {"traffic": "/traffic/v1/bound", "direction": "/directionlite/v1/driving"}
MAX_BODY = 4 * 1024 * 1024
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
TOKEN_PATTERN = re.compile(r"[a-f0-9]{32}\Z")
STOP_API_CODES = {2, 4, 9, 101, 200, 201, 202, 203, 210, 211, 220, 230, 240,
                  250, 251, 252, 260, 261, 301, 302, 401, 402, 1001}


class CollectionError(Exception):
    """Only fixed, non-sensitive reason codes may be supplied."""


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TZ).isoformat(timespec="milliseconds")


def contained(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise CollectionError("path_outside_workspace")
    return path


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def atomic_json(path: Path, value: Any, redactor: Redactor | None = None) -> None:
    if redactor is not None:
        value = redactor.clean(value)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def new_json(path: Path, value: Any, redactor: Redactor) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(redactor.clean(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def validate_config(value: Any) -> dict[str, Any]:
    try:
        if not isinstance(value, dict) or not ID_PATTERN.fullmatch(value["study_area_id"]):
            raise ValueError
        if value["coordinate_system"] != "bd09ll":
            raise ValueError
        bounds = {name: float(value["bounds"][name]) for name in ("south", "north", "west", "east")}
        if not (-90 <= bounds["south"] < bounds["north"] <= 90 and
                -180 <= bounds["west"] < bounds["east"] <= 180):
            raise ValueError
        if diagonal_metres(**bounds) >= 2000:
            raise ValueError
        stop = datetime.fromisoformat(value["stop_at"].replace("Z", "+00:00"))
        if stop.utcoffset() is None:
            raise ValueError
        numbers: dict[str, float | int] = {}
        for field in ("traffic_interval_seconds", "routes_interval_seconds", "minimum_request_gap_seconds"):
            number = value[field]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 1:
                raise ValueError
            numbers[field] = number
        failures = value["maximum_consecutive_failures"]
        if isinstance(failures, bool) or not isinstance(failures, int) or not 1 <= failures <= 20:
            raise ValueError
        limits = {kind: value["daily_limits"][kind] for kind in ENDPOINTS}
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in limits.values()):
            raise ValueError
        routes, ids = [], set()
        if not isinstance(value["route_pairs"], list) or not value["route_pairs"]:
            raise ValueError
        for route in value["route_pairs"]:
            identifier = route["id"]
            if not ID_PATTERN.fullmatch(identifier) or identifier in ids:
                raise ValueError
            ids.add(identifier)
            clean = {"id": identifier}
            for field in ("origin", "destination"):
                pair = route[field]
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError
                lat, lng = (float(item) for item in pair)
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError
                clean[field] = [lat, lng]
            if clean["origin"] == clean["destination"]:
                raise ValueError
            routes.append(clean)
        return {"study_area_id": value["study_area_id"], "bounds": bounds,
                "coordinate_system": "bd09ll", **numbers, "route_pairs": routes,
                "stop_at": stop.astimezone(TZ).isoformat(), "daily_limits": limits,
                "maximum_consecutive_failures": failures}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise CollectionError("collection_config_invalid") from None


@contextmanager
def exclusive_lock(root: Path):
    directory = contained(root, "logs", "baidu_collector")
    directory.mkdir(parents=True, exist_ok=True)
    path = contained(root, "logs", "baidu_collector", "collector.lock")
    with path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectionError("collector_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class BudgetLedger:
    """Workspace-wide budget, protected by the collector's lifetime flock."""
    def __init__(self, root: Path):
        self.path = contained(root, "logs", "baidu_collector", "budget.json")
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if self.data["schema_version"] != 1 or not isinstance(self.data["days"], dict):
                    raise ValueError
                last = self.data["last_request_epoch"]
                if not isinstance(last, (int, float)) or not math.isfinite(last) or last < 0:
                    raise ValueError
                for day, counts in self.data["days"].items():
                    datetime.strptime(day, "%Y-%m-%d")
                    for kind in ENDPOINTS:
                        n = counts[kind]
                        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                            raise ValueError
            except (OSError, ValueError, KeyError, TypeError):
                raise CollectionError("budget_ledger_invalid_fail_closed") from None
        else:
            self.data = {"schema_version": 1, "days": {}, "last_request_epoch": 0,
                         "scope": "This collector/workspace only; probes and other clients are not counted."}

    def counts(self, epoch: float) -> dict[str, int]:
        day = datetime.fromtimestamp(epoch, TZ).date().isoformat()
        return dict(self.data["days"].get(day, {"traffic": 0, "direction": 0}))

    def reserve(self, kind: str, limits: dict[str, int], epoch: float) -> bool:
        counts = self.counts(epoch)
        if counts[kind] >= limits[kind]:
            return False
        counts[kind] += 1
        self.data["days"][datetime.fromtimestamp(epoch, TZ).date().isoformat()] = counts
        self.data["last_request_epoch"] = epoch
        # Charge before dispatch, including transport failures and interrupted calls.
        atomic_json(self.path, self.data)
        return True

    def completed(self, epoch: float) -> None:
        # Conservatively separate dispatches by at least one second AFTER the
        # previous response/transport failure, including across normal restarts.
        self.data["last_request_epoch"] = max(self.data["last_request_epoch"], epoch)
        atomic_json(self.path, self.data)


def next_midnight(epoch: float) -> float:
    date = datetime.fromtimestamp(epoch, TZ).date() + timedelta(days=1)
    return datetime.combine(date, datetime.min.time(), TZ).timestamp()


def process_start_ticks(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None


def matching_process(state: dict[str, Any]) -> bool:
    try:
        pid, token = state["pid"], state["process_token"]
        if not isinstance(pid, int) or not TOKEN_PATTERN.fullmatch(token):
            return False
        if not state.get("pid_start_ticks") or process_start_ticks(pid) != state["pid_start_ticks"]:
            return False
        args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        index = args.index("--worker-token")
        return str(SCRIPT) in args and args[index + 1] == token and ("--run" in args or "--once" in args)
    except (OSError, ValueError, IndexError, KeyError, TypeError, UnicodeDecodeError):
        return False


def lock_is_held(root: Path) -> bool | None:
    """Observe flock without creating/writing files or disturbing its owner."""
    path = contained(root, "logs", "baidu_collector", "collector.lock")
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def fetch(path: str, params: list[tuple[str, str]], ak: str, sk: str,
          redactor: Redactor) -> tuple[Any, dict[str, Any]]:
    if path not in ENDPOINTS.values():
        raise CollectionError("endpoint_not_allowed")
    signed, signature = signed_query(path, params + [("ak", ak)], sk)
    redactor.add(signature)
    request = Request(HOST + signed, headers={"Accept": "application/json"})
    info: dict[str, Any] = {"http_status": None, "transport_status": "network_error"}
    body = None
    try:
        with build_opener(NoRedirects()).open(request, timeout=15) as response:
            info.update(http_status=response.status, transport_status="received")
            body = response.read(MAX_BODY + 1)
    except HTTPError as exc:
        info.update(http_status=exc.code, transport_status="redirect_rejected" if 300 <= exc.code < 400 else "http_error")
        try:
            body = exc.read(MAX_BODY + 1)
        except (OSError, ValueError):
            pass
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError):
        pass
    payload: Any = None
    if body is not None:
        if len(body) > MAX_BODY:
            info["response_format"] = "size_limit_exceeded"
            payload = {"_body_omitted": "size_limit_exceeded"}
        else:
            try:
                payload = json.loads(body)
                info["response_format"] = "json"
            except (ValueError, UnicodeDecodeError):
                info["response_format"] = "non_json"
                payload = {"_non_json_body": body.decode("utf-8", errors="replace")}
    payload = redactor.clean(payload)
    info.update(summarize(payload))
    return payload, info


def failure_kind(info: dict[str, Any]) -> str | None:
    http, status = info.get("http_status"), info.get("api_status")
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    if http == 200 and status == 0:
        return None
    if http == 429:
        return "http_rate_limit"
    if http is not None and (300 <= http < 500):
        return "http_permission_or_request_error"
    if status in STOP_API_CODES:
        return "api_permission_quota_or_parameter_error"
    return "transient_error"


class Collector:
    """Call under exclusive_lock; dependencies are injectable for offline tests."""
    def __init__(self, root: Path, config: dict[str, Any], ak: str, sk: str,
                 token: str, *, clock: Any = time, transport: Any = fetch,
                 config_path: str = "config/collection.json"):
        self.root, self.config, self.clock, self.transport = root, config, clock, transport
        self.ak, self.sk, self.redactor = ak, sk, Redactor(ak, sk)
        self.token = token
        self.stop_epoch = datetime.fromisoformat(config["stop_at"]).timestamp()
        self.log_dir = contained(root, "logs", "baidu_collector")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = contained(root, "logs", "baidu_collector", "state.json")
        self.stop_path = contained(root, "logs", "baidu_collector", f"stop_{token}.json")
        self.ledger = BudgetLedger(root)
        self.sequence = 0
        self.started_epoch = clock.time()
        self.tasks = [{"kind": "traffic", "id": "traffic", "next": clock.time()}]
        self.tasks.extend({"kind": "direction", "id": pair["id"], "pair": pair,
                           "next": clock.time()} for pair in config["route_pairs"])
        stamp = datetime.fromtimestamp(clock.time(), timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.output = contained(root, "data", "raw", "baidu", f"collection_{stamp}_{token[:8]}")
        self.state: dict[str, Any] = {
            "schema_version": 1, "status": "running", "stop_reason": None,
            "pid": os.getpid(), "pid_start_ticks": process_start_ticks(os.getpid()),
            "process_token": token, "config_path": config_path,
            "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            "study_area_id": config["study_area_id"], "started_at": iso(clock.time()),
            "stop_at": config["stop_at"], "session_counts": {"traffic": 0, "direction": 0},
            "success_counts": {"traffic": 0, "direction": 0}, "consecutive_failures": 0,
            "error_count": 0,
            "last_success_at": None, "last_result": None,
            "output_directory": str(self.output.relative_to(root)),
            "billing_status": "unverified", "daily_limits": config["daily_limits"],
            "budget_scope": "This collector/workspace only; probes and other clients are not counted.",
        }

    def save_state(self) -> None:
        current = self.clock.time()
        counts = self.ledger.counts(current)
        self.state.update(updated_at=iso(current), budget_day=datetime.fromtimestamp(current, TZ).date().isoformat(),
                          daily_counts=counts,
                          remaining_budget={k: max(0, self.config["daily_limits"][k] - counts[k]) for k in ENDPOINTS},
                          next_requests={t["kind"] + ":" + t["id"]: iso(t["next"]) for t in self.tasks})
        atomic_json(self.state_path, self.state, self.redactor)

    def should_stop(self) -> str | None:
        if self.clock.time() >= self.stop_epoch:
            return "configured_deadline_reached"
        if self.stop_path.exists():
            try:
                request = json.loads(self.stop_path.read_text(encoding="utf-8"))
                if request.get("process_token") == self.token and request.get("pid") == os.getpid():
                    return "stop_requested"
            except (OSError, ValueError, TypeError):
                pass
        return None

    def wait_until(self, target: float) -> str | None:
        heartbeat = self.clock.time()
        while self.clock.time() < target:
            reason = self.should_stop()
            if reason:
                return reason
            self.clock.sleep(min(1.0, target - self.clock.time(), self.stop_epoch - self.clock.time()))
            if self.clock.time() - heartbeat >= 10:
                self.save_state()
                heartbeat = self.clock.time()
        return self.should_stop()

    def parameters(self, task: dict[str, Any]) -> list[tuple[str, str]]:
        point = lambda pair: f"{pair[0]:.6f},{pair[1]:.6f}"
        if task["kind"] == "traffic":
            b = self.config["bounds"]
            return [("bounds", point([b["south"], b["west"]]) + ";" + point([b["north"], b["east"]])),
                    ("coord_type_input", "bd09ll"), ("coord_type_output", "bd09ll")]
        pair = task["pair"]
        return [("origin", point(pair["origin"])), ("destination", point(pair["destination"])),
                ("coord_type", "bd09ll"), ("ret_coordtype", "bd09ll"),
                ("steps_info", "1"), ("tactics", "1"), ("origin_road_type", "0"),
                ("timestamp", str(int(self.clock.time())))]

    def collect_one(self, task: dict[str, Any]) -> str | None:
        gap = max(1, self.config["minimum_request_gap_seconds"])
        reason = self.wait_until(self.ledger.data["last_request_epoch"] + gap)
        if reason:
            return reason
        kind = task["kind"]
        sent = self.clock.time()
        if not self.ledger.reserve(kind, self.config["daily_limits"], sent):
            task["next"] = next_midnight(sent)
            self.save_state()
            return "daily_budget_wait"
        self.sequence += 1
        self.state["session_counts"][kind] += 1
        params = self.parameters(task)
        stem = f"{self.sequence:06d}_{kind}_{task['id']}"
        metadata: dict[str, Any] = {
            "request_number": self.sequence, "endpoint": ENDPOINTS[kind], "kind": kind,
            "route_pair_id": task["id"] if kind == "direction" else None,
            "study_area_id": self.config["study_area_id"], "coordinate_system": "bd09ll",
            "params": dict(params), "requested_at": iso(sent),
            "response_file": stem + ".json", "source_observation_time": None,
            "source_time_note": "Request/receive time is not the platform's independent observation time.",
        }
        cycle = max(0, int((sent - self.started_epoch) // self.config["traffic_interval_seconds"]))
        metadata.update(cycle_id=f"cycle_{cycle:06d}",
                        cycle_start_at=iso(self.started_epoch + cycle * self.config["traffic_interval_seconds"]))
        new_json(self.output / (stem + ".request.json"), metadata, self.redactor)
        self.save_state()
        payload, info = self.transport(ENDPOINTS[kind], params, self.ak, self.sk, self.redactor)
        self.ledger.completed(self.clock.time())
        metadata.update(info, received_at=iso(self.clock.time()))
        new_json(self.output / (stem + ".json"), payload, self.redactor)
        new_json(self.output / (stem + ".meta.json"), metadata, self.redactor)
        with (self.output / "requests.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.redactor.clean(metadata), ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        interval = self.config["traffic_interval_seconds" if kind == "traffic" else "routes_interval_seconds"]
        task["next"] = max(sent + interval, self.clock.time())
        failure = failure_kind(info)
        self.state["last_result"] = {k: metadata.get(k) for k in ("request_number", "kind", "route_pair_id", "http_status", "api_status", "transport_status", "received_at")}
        if failure is None:
            self.state["consecutive_failures"] = 0
            self.state["last_success_at"] = metadata["received_at"]
            self.state["success_counts"][kind] += 1
        else:
            self.state["error_count"] += 1
            self.state["consecutive_failures"] += 1
            if failure != "transient_error":
                return failure
            if self.state["consecutive_failures"] >= self.config["maximum_consecutive_failures"]:
                return "maximum_consecutive_failures_reached"
            # No immediate retry of this request. Defer ALL tasks on transient failure.
            resume = self.clock.time() + min(300, 5 * 2 ** (self.state["consecutive_failures"] - 1))
            for other in self.tasks:
                other["next"] = max(other["next"], resume)
        self.save_state()
        return None

    def run(self, once: bool = False) -> int:
        reason = "local_processing_error"
        try:
            self.output.mkdir(parents=True, exist_ok=False)
            new_json(self.output / "manifest.json", {
                "schema_version": 1, "study_area_id": self.config["study_area_id"],
                "purpose": "observational_collection", "source": "Baidu Web API",
                "coordinate_system": "bd09ll", "config": self.config,
                "bounds": self.config["bounds"],
                "sampling_cycle_seconds": self.config["traffic_interval_seconds"],
                "cycle_note": "Time buckets for replay; requests in a bucket are not simultaneous observations.",
                "started_at": self.state["started_at"], "requests_index": "requests.jsonl",
                "billing_status": "unverified", "config_sha256": self.state["config_sha256"],
                "budget_scope": self.state["budget_scope"],
            }, self.redactor)
            self.save_state()
            pending = list(self.tasks)
            while True:
                reason = self.should_stop()
                if reason:
                    break
                if once and not pending:
                    reason = "once_complete_with_errors" if self.state["error_count"] else "once_complete"
                    break
                task = min(pending if once else self.tasks, key=lambda item: item["next"])
                reason = self.wait_until(task["next"])
                if reason:
                    break
                reason = self.collect_one(task)
                if reason == "daily_budget_wait":
                    if once:
                        reason = "once_incomplete_daily_budget_exhausted"
                        break
                    continue
                if reason:
                    break
                if once:
                    pending.remove(task)
        except KeyboardInterrupt:
            reason = "interrupted"
        except Exception:
            reason = "local_processing_error"
        finally:
            self.state.update(status="stopped", stop_reason=reason, finished_at=iso(self.clock.time()))
            try:
                self.save_state()
                new_json(self.output / "run_summary.json", self.state, self.redactor)
            except Exception:
                emit({"status": "state_or_summary_write_failed"})
            emit({"status": "stopped", "stop_reason": reason, "session_counts": self.state["session_counts"]})
        return 0 if reason in {"once_complete", "configured_deadline_reached", "stop_requested", "interrupted"} else 1


def read_status(root: Path = ROOT) -> dict[str, Any]:
    path = contained(root, "logs", "baidu_collector", "state.json")
    if not path.exists():
        return {"status": "not_started", "alive": False}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        verified = matching_process(state)
        held = lock_is_held(root)
        try:
            heartbeat = datetime.fromisoformat(state["updated_at"])
            age = time.time() - heartbeat.timestamp() if heartbeat.utcoffset() is not None else None
        except (KeyError, TypeError, ValueError):
            age = None
        recent = age is not None and 0 <= age <= 45
        state.update(pid_verified=verified, lock_held=held,
                     heartbeat_age_seconds=round(age, 3) if age is not None else None,
                     heartbeat_recent=recent)
        # /proc may be isolated across tool/process namespaces. A missing PID
        # cannot prove exit while another process still owns the workspace lock.
        if verified:
            state.update(alive=True, liveness_evidence="pid_start_and_token_verified")
        elif held:
            state.update(alive=None, liveness_evidence=(
                "lock_held_recent_heartbeat_pid_unverified" if recent
                else "lock_held_heartbeat_stale_or_missing_pid_unverified"))
        elif held is None:
            state.update(alive=None, liveness_evidence="lock_unreadable_pid_unverified")
        else:
            state.update(alive=False, liveness_evidence="pid_unverified_lock_free")
        if state.get("status") == "running" and state["alive"] is False:
            state["status"] = "not_running"
            state["stop_reason"] = "process_exited_without_final_state"
        return state
    except (OSError, ValueError, TypeError):
        raise CollectionError("state_invalid") from None


def request_stop(root: Path = ROOT) -> dict[str, Any]:
    state = read_status(root)
    token = state.get("process_token", "")
    identity_valid = isinstance(state.get("pid"), int) and isinstance(token, str) and TOKEN_PATTERN.fullmatch(token)
    # A token-specific stop file is safe across PID namespaces: the worker must
    # independently match BOTH its own PID and token before it honors the file.
    if (state.get("status") != "running" or not identity_valid
            or not (state.get("pid_verified") or state.get("lock_held") is True)):
        return {"status": "no_matching_running_collector", "alive": state.get("alive", False)}
    path = contained(root, "logs", "baidu_collector", f"stop_{state['process_token']}.json")
    atomic_json(path, {"pid": state["pid"], "process_token": state["process_token"], "requested_at": iso(time.time())})
    return {"status": "stop_requested", "pid": state["pid"], "expected_max_request_wait_seconds": 15}


def execute(config_path: Path, once: bool, token: str) -> int:
    with exclusive_lock(ROOT):
        try:
            try:
                config = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                raise CollectionError("collection_config_unavailable") from None
            if datetime.fromisoformat(config["stop_at"]).timestamp() <= time.time():
                save_start_failure(ROOT, token, "configured_deadline_reached")
                emit({"status": "not_started", "stop_reason": "configured_deadline_reached"})
                return 0
            try:
                credentials = json.loads(contained(ROOT, "config", "baidu.local.json").read_text(encoding="utf-8"))
                ak, sk = credentials["ak"], credentials["sk"]
                if not isinstance(ak, str) or not ak or not isinstance(sk, str) or not sk:
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError):
                raise CollectionError("credentials_unavailable_or_invalid") from None
            runner = Collector(ROOT, config, ak, sk, token, config_path=str(config_path.relative_to(ROOT)))
        except CollectionError as exc:
            save_start_failure(ROOT, token, str(exc))
            raise
        return runner.run(once)


def save_start_failure(root: Path, token: str, reason: str) -> None:
    """Caller owns flock; never replace the state of another active worker."""
    state = {"status": "stopped", "stop_reason": reason, "pid": os.getpid(),
             "pid_start_ticks": process_start_ticks(os.getpid()), "process_token": token,
             "started_at": iso(time.time()), "finished_at": iso(time.time())}
    atomic_json(contained(root, "logs", "baidu_collector", "state.json"), state)


def start_background(config_path: Path) -> int:
    state = read_status()
    if state.get("alive") or state.get("lock_held"):
        emit({"status": "collector_already_running", "pid": state["pid"]})
        return 1
    # Validate before spawning; this does not read secrets or call a service.
    try:
        config = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise CollectionError("collection_config_unavailable") from None
    if datetime.fromisoformat(config["stop_at"]).timestamp() <= time.time():
        emit({"status": "not_started", "stop_reason": "configured_deadline_reached"})
        return 0
    token = uuid.uuid4().hex
    log_dir = contained(ROOT, "logs", "baidu_collector")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = contained(ROOT, "logs", "baidu_collector", "worker_" + token + ".log")
    with log_path.open("x", encoding="utf-8") as output:
        child = subprocess.Popen(
            [sys.executable, "-B", str(SCRIPT), "--run", "--config", str(config_path), "--worker-token", token],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
    for _ in range(50):
        state = read_status()
        if state.get("process_token") == token:
            active = state.get("alive") or (state.get("status") == "running"
                                             and state.get("lock_held") and state.get("heartbeat_recent"))
            label = "started" if state.get("alive") else "started_pid_unverified" if active else "startup_stopped"
            emit({"status": label,
                  "pid": child.pid, "stop_reason": state.get("stop_reason")})
            return 0 if active else 1
        if child.poll() is not None:
            emit({"status": "startup_failed", "exit_code": child.returncode})
            return 1
        time.sleep(0.1)
    emit({"status": "startup_pending", "pid": child.pid})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    group = parser.add_mutually_exclusive_group()
    for action in ("once", "run", "start", "status", "stop"):
        group.add_argument("--" + action, action="store_true")
    parser.add_argument("--config", default="config/collection.json", help="Non-secret config, confined to this workspace.")
    parser.add_argument("--worker-token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.status:
            emit(read_status())
            return 0
        if args.stop:
            emit(request_stop())
            return 0
        if not (args.once or args.run or args.start):
            parser.print_help()
            return 0
        config_path = contained(ROOT, args.config)
        if args.start:
            return start_background(config_path)
        if args.worker_token is None:
            # Make the identity token visible in /proc for PID-reuse-safe status/stop.
            action = "--once" if args.once else "--run"
            os.execv(sys.executable, [sys.executable, "-B", str(SCRIPT), action, "--config", str(config_path),
                                      "--worker-token", uuid.uuid4().hex])
        if not TOKEN_PATTERN.fullmatch(args.worker_token):
            raise CollectionError("invalid_worker_token")
        return execute(config_path, args.once, args.worker_token)
    except CollectionError as exc:
        emit({"status": "not_started_or_failed", "reason": str(exc)})
        return 1
    except KeyboardInterrupt:
        emit({"status": "interrupted"})
        return 1
    except Exception:
        emit({"status": "local_processing_error"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
