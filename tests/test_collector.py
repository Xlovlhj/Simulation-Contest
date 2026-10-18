"""Offline safety/collection tests. No real credentials or network are used.

AI-assisted by Codex/OpenAI, 2026-10-17; team review required before submission.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import collect_baidu as collection


class FakeClock:
    def __init__(self, epoch: float):
        self.epoch = epoch
    def time(self) -> float:
        return self.epoch
    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("negative_sleep")
        self.epoch += seconds


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="collector_test_", dir=ROOT / "tests")
        self.root = Path(self.temporary.name)
        (self.root / "logs" / "baidu_collector").mkdir(parents=True)
        self.clock = FakeClock(datetime.fromisoformat("2026-10-17T15:00:00+08:00").timestamp())
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, routes=2, stop_seconds=3600, **updates):
        value = {
            "study_area_id": "shanghai_test", "coordinate_system": "bd09ll",
            "bounds": {"south": 31.211, "north": 31.221, "west": 121.466, "east": 121.478},
            "traffic_interval_seconds": 120, "routes_interval_seconds": 300,
            "minimum_request_gap_seconds": 1, "maximum_consecutive_failures": 5,
            "daily_limits": {"traffic": 900, "direction": 1500},
            "route_pairs": [{"id": "pair_" + str(i), "origin": [31.213, 121.468],
                             "destination": [31.219, 121.474]} for i in range(routes)],
            "stop_at": collection.iso(self.clock.time() + stop_seconds),
        }
        value.update(updates)
        return collection.validate_config(value)

    def success(self, path, params, ak, sk, redactor):
        self.calls.append({"time": self.clock.time(), "path": path, "params": dict(params)})
        payload = {"status": 0, "result": {"routes": []}, "road_traffic": []}
        return payload, {"http_status": 200, "api_status": 0, "transport_status": "received"}

    def runner(self, config=None, transport=None, token="a" * 32):
        return collection.Collector(self.root, config or self.config(), "offline-ak", "offline-sk",
                                    token, clock=self.clock, transport=transport or self.success)

    def run_silently(self, runner, once=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run(once=once)

    def test_once_complete_batch_raw_layout_and_inter_request_gap(self):
        runner = self.runner()
        self.assertEqual(self.run_silently(runner), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.calls[1]["time"] - self.calls[0]["time"], 1)
        self.assertGreaterEqual(self.calls[2]["time"] - self.calls[1]["time"], 1)
        self.assertIn("timestamp", self.calls[1]["params"])
        self.assertEqual(self.calls[1]["params"]["steps_info"], "1")
        self.assertEqual(self.calls[1]["params"]["tactics"], "1")
        self.assertEqual(self.calls[1]["params"]["origin_road_type"], "0")
        records = [json.loads(line) for line in (runner.output / "requests.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertTrue((runner.output / record["response_file"]).exists())
            self.assertNotIn("ak", record["params"])
            self.assertIn("cycle_id", record)
            self.assertIsNone(record["source_observation_time"])
        manifest = json.loads((runner.output / "manifest.json").read_text())
        self.assertEqual(manifest["requests_index"], "requests.jsonl")
        self.assertEqual(manifest["study_area_id"], "shanghai_test")
        self.assertEqual(runner.state["stop_reason"], "once_complete")

    def test_deadline_prevents_first_request_and_stops_between_requests(self):
        expired = self.runner(self.config(stop_seconds=0))
        self.assertEqual(self.run_silently(expired), 0)
        self.assertEqual(len(self.calls), 0)
        timed = self.runner(self.config(stop_seconds=0.5), token="b" * 32)
        self.assertEqual(self.run_silently(timed), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(timed.state["stop_reason"], "configured_deadline_reached")

    def test_budget_survives_restart_and_rolls_over_in_beijing_timezone(self):
        ledger = collection.BudgetLedger(self.root)
        limits = {"traffic": 1, "direction": 2}
        late = datetime.fromisoformat("2026-10-17T23:59:59+08:00").timestamp()
        self.assertTrue(ledger.reserve("traffic", limits, late))
        restarted = collection.BudgetLedger(self.root)
        self.assertFalse(restarted.reserve("traffic", limits, late))
        self.assertTrue(restarted.reserve("traffic", limits, late + 1))
        self.assertEqual(restarted.data["days"]["2026-10-17"]["traffic"], 1)
        self.assertEqual(restarted.data["days"]["2026-10-18"]["traffic"], 1)

    def test_collector_restart_does_not_reset_budget(self):
        config = self.config(routes=1, daily_limits={"traffic": 1, "direction": 1})
        first = self.runner(config)
        self.assertEqual(self.run_silently(first), 0)
        second = self.runner(config, token="b" * 32)
        self.assertEqual(self.run_silently(second), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second.state["stop_reason"], "once_incomplete_daily_budget_exhausted")
        self.assertEqual(second.state["daily_counts"], {"traffic": 1, "direction": 1})

    def test_budget_is_persisted_before_transport_and_failures_are_charged(self):
        def permission_error(path, params, ak, sk, redactor):
            ledger = collection.BudgetLedger(self.root)
            self.assertEqual(ledger.counts(self.clock.time())["traffic"], 1)
            self.calls.append(path)
            return {"status": 211}, {"http_status": 200, "api_status": 211, "transport_status": "received"}
        runner = self.runner(transport=permission_error)
        self.assertEqual(self.run_silently(runner), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(runner.state["stop_reason"], "api_permission_quota_or_parameter_error")
        self.assertEqual(collection.BudgetLedger(self.root).counts(self.clock.time())["traffic"], 1)

    def test_minimum_gap_follows_previous_response_and_persists(self):
        def slow_success(*args):
            result = self.success(*args)
            self.clock.sleep(7)
            return result
        first = self.runner(self.config(routes=1), transport=slow_success)
        self.assertEqual(self.run_silently(first), 0)
        previous_finished = self.clock.time()
        second = self.runner(self.config(routes=1), token="b" * 32)
        self.assertEqual(self.run_silently(second), 0)
        self.assertGreaterEqual(self.calls[1]["time"] - self.calls[0]["time"], 8)
        self.assertGreaterEqual(self.calls[2]["time"] - previous_finished, 1)

    def test_once_reports_transient_failure_even_if_later_requests_succeed(self):
        def initial_failure(*args):
            if not self.calls:
                self.calls.append({"time": self.clock.time()})
                return None, {"http_status": None, "api_status": None, "transport_status": "network_error"}
            return self.success(*args)
        runner = self.runner(self.config(routes=1), transport=initial_failure)
        self.assertEqual(self.run_silently(runner), 1)
        self.assertEqual(runner.state["stop_reason"], "once_complete_with_errors")

    def test_http429_and_quota_stop_without_retry(self):
        for index, info in enumerate((
            {"http_status": 429, "api_status": None, "transport_status": "http_error"},
            {"http_status": 200, "api_status": 302, "transport_status": "received"},
        )):
            with self.subTest(info=info):
                calls = []
                def failing(*args):
                    calls.append(1)
                    return {"status": info["api_status"]}, info
                runner = self.runner(transport=failing, token=str(index + 1) * 32)
                self.assertEqual(self.run_silently(runner), 1)
                self.assertEqual(len(calls), 1)

    def test_transient_errors_have_finite_backoff_and_failure_limit(self):
        def unavailable(*args):
            self.calls.append(self.clock.time())
            return None, {"http_status": None, "api_status": None, "transport_status": "network_error"}
        runner = self.runner(self.config(routes=5, maximum_consecutive_failures=3), unavailable)
        self.assertEqual(self.run_silently(runner), 1)
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.calls[1] - self.calls[0], 5)
        self.assertGreaterEqual(self.calls[2] - self.calls[1], 10)
        self.assertEqual(runner.state["stop_reason"], "maximum_consecutive_failures_reached")

    def test_secret_redaction_in_every_written_file_and_output(self):
        def echo(path, params, ak, sk, redactor):
            signature = "offline-generated-signature"
            redactor.add(signature)
            return {"status": 0, "ak": ak, "nested": [{"sk": sk, "sn": signature}],
                    "message": ak + " " + sk + " " + signature,
                    "url": "https://api.map.baidu.com/route?ak=" + ak}, {
                        "http_status": 200, "api_status": 0, "transport_status": "received"}
        runner = self.runner(transport=echo)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(runner.run(once=True), 0)
        saved = output.getvalue()
        for path in self.root.rglob("*"):
            if path.is_file():
                saved += path.read_text()
        for secret in ("offline-ak", "offline-sk", "offline-generated-signature", "https://api.map.baidu.com"):
            self.assertNotIn(secret, saved)

    def test_transport_fixed_host_timeout_and_echoed_signature_redacted(self):
        captured = {}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size):
                return json.dumps({"status": 0, "message": captured["signature"] + " offline-ak offline-sk"}).encode()
        class Opener:
            def open(self, request, timeout):
                parsed = urlparse(request.full_url)
                self_test.assertEqual(parsed.scheme, "https")
                self_test.assertEqual(parsed.hostname, "api.map.baidu.com")
                self_test.assertEqual(timeout, 15)
                captured["signature"] = parse_qs(parsed.query)["sn"][0]
                return Response()
        self_test = self
        with patch.object(collection, "build_opener", return_value=Opener()):
            payload, info = collection.fetch("/traffic/v1/bound", [("bounds", "31.21,121.46;31.22,121.47")],
                                             "offline-ak", "offline-sk", collection.Redactor("offline-ak", "offline-sk"))
        self.assertEqual(info["api_status"], 0)
        self.assertNotIn(captured["signature"], json.dumps(payload))
        self.assertIsNone(collection.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_multi_instance_lock_and_corrupt_budget_fail_closed(self):
        with collection.exclusive_lock(self.root):
            with self.assertRaisesRegex(collection.CollectionError, "collector_already_running"):
                with collection.exclusive_lock(self.root):
                    self.fail("second_instance_acquired_lock")
        with collection.exclusive_lock(self.root):
            pass
        (self.root / "logs" / "baidu_collector" / "budget.json").write_text("broken")
        with self.assertRaisesRegex(collection.CollectionError, "budget_ledger_invalid_fail_closed"):
            collection.BudgetLedger(self.root)

    def test_status_and_stop_do_not_read_credentials_or_signal_other_process(self):
        self.assertEqual(collection.read_status(self.root)["status"], "not_started")
        state_path = self.root / "logs" / "baidu_collector" / "state.json"
        collection.atomic_json(state_path, {"status": "running", "pid": 1, "pid_start_ticks": "wrong",
                                            "process_token": "a" * 32})
        with patch.object(collection, "matching_process", return_value=False):
            result = collection.request_stop(self.root)
        self.assertEqual(result["status"], "no_matching_running_collector")
        self.assertEqual(list(state_path.parent.glob("stop_*.json")), [])
        with patch.object(collection, "matching_process", return_value=True):
            result = collection.request_stop(self.root)
        self.assertEqual(result["status"], "stop_requested")
        stop = json.loads(next(state_path.parent.glob("stop_*.json")).read_text())
        self.assertEqual(stop["process_token"], "a" * 32)

    def test_status_keeps_pid_namespace_uncertainty_when_lock_is_held(self):
        state_path = self.root / "logs" / "baidu_collector" / "state.json"
        state = {"status": "running", "pid": 123456, "pid_start_ticks": "123",
                 "process_token": "b" * 32, "updated_at": collection.iso(collection.time.time()),
                 "stop_reason": None}
        collection.atomic_json(state_path, state)
        with collection.exclusive_lock(self.root), patch.object(collection, "matching_process", return_value=False):
            status = collection.read_status(self.root)
            self.assertIsNone(status["alive"])
            self.assertTrue(status["lock_held"])
            self.assertTrue(status["heartbeat_recent"])
            self.assertEqual(status["status"], "running")
            self.assertIsNone(status["stop_reason"])
            result = collection.request_stop(self.root)
            self.assertEqual(result["status"], "stop_requested")
            stop = json.loads((state_path.parent / ("stop_" + "b" * 32 + ".json")).read_text())
            self.assertEqual(stop["pid"], state["pid"])
            self.assertEqual(stop["process_token"], state["process_token"])
            state["updated_at"] = collection.iso(collection.time.time() - 120)
            collection.atomic_json(state_path, state)
            stale = collection.read_status(self.root)
            self.assertIsNone(stale["alive"])
            self.assertFalse(stale["heartbeat_recent"])
            self.assertEqual(stale["status"], "running")
        self.assertFalse(collection.read_status(self.root)["alive"])

    def test_config_rejects_naive_deadline_and_oversize_rectangle(self):
        config = self.config()
        config["stop_at"] = "2026-10-19T20:00:00"
        with self.assertRaises(collection.CollectionError):
            collection.validate_config(config)
        config = self.config()
        config["bounds"]["north"] = 32
        with self.assertRaises(collection.CollectionError):
            collection.validate_config(config)
        with self.assertRaises(collection.CollectionError):
            collection.contained(self.root, "../outside")


if __name__ == "__main__":
    unittest.main()
